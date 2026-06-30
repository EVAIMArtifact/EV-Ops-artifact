# src/executor/ansible_generator.py

import json
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, get_executor_prompts
from src.clients.llm_client import create_llm_client


class AnsibleExecutor:
    def __init__(self, llm_client):
        self.llm = llm_client

    @classmethod
    def from_config(cls, model_config: Dict[str, Any]):
        llm_client = create_llm_client(model_config)
        return cls(llm_client)

    def _get_nested(self, d, path, default=None):
        cur = d or {}
        for key in path:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(key)
        return cur if cur is not None else default

    def _flat_outcome(self, ex: Dict[str, Any]) -> Dict[str, Any]:
        outcome = ex.get("outcome", {}) or ex.get("feedback", {}) or {}
        if isinstance(outcome.get("feedback"), dict):
            merged = dict(outcome.get("feedback") or {})
            for k, v in outcome.items():
                if k != "feedback" and v is not None:
                    merged[k] = v
            return merged
        return outcome

    def _clean_none(self, d: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in d.items() if v is not None}

    def _target_changes_from_experience(self, ex: Dict[str, Any]) -> Dict[str, Any]:
        keys = ex.get("retrieval_keys", {}) or {}
        plan = ex.get("plan", {}) or {}
        raw_plan = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}

        return (
            plan.get("target_changes")
            or raw_plan.get("target_changes")
            or {
                "type": keys.get("target_changes_type"),
                "previous_value": keys.get("target_changes_previous_value"),
                "target_value": keys.get("target_changes_target_value"),
            }
        )

    def _normalized_action_from_experience(self, ex: Dict[str, Any]) -> Dict[str, Any]:
        outcome = self._flat_outcome(ex)
        plan = ex.get("plan", {}) or {}
        raw_plan = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}
        target_changes = self._target_changes_from_experience(ex)

        normalized = (
            outcome.get("normalized_action")
            or plan.get("normalized_action")
            or raw_plan.get("normalized_action")
            or {}
        )

        if isinstance(normalized, dict) and normalized.get("action_type"):
            return normalized

        action_type = (
            outcome.get("plan_action")
            or plan.get("action_type")
            or raw_plan.get("action_type")
            or target_changes.get("type")
            or "unknown"
        )

        if action_type == "memory_limit":
            action_type = "scale_up_memory"
        elif action_type == "cpu_limit":
            action_type = "scale_up_cpu"
        elif action_type == "replicas":
            action_type = "scale_out"
        elif action_type == "image":
            action_type = "rollback"
        elif action_type == "config":
            action_type = "config_fix"
        elif action_type == "none":
            action_type = "none"

        return {
            "action_type": action_type,
            "target": (
                target_changes.get("target")
                or (ex.get("incident", {}) or {}).get("deployment")
                or (ex.get("incident", {}) or {}).get("service")
            ),
            "value": target_changes.get("target_value"),
        }

    def _resource_change_summary(self, ex: Dict[str, Any]) -> Dict[str, Any]:
        """
        Supports the new run_experiment/store format:
          exp["resource_changes"]
        and older flat/result formats.
        """
        direct = ex.get("resource_changes")
        if isinstance(direct, dict):
            return direct

        outcome = self._flat_outcome(ex)
        infra_cmp = (
            ex.get("infrastructure_comparison")
            or self._get_nested(ex, ["infrastructure", "comparison"], {})
            or {}
        )

        return self._clean_none({
            "cpu_limit_before": outcome.get("cpu_limit_before") or infra_cmp.get("cpu_limit_per_pod_before_millicores"),
            "cpu_limit_after": outcome.get("cpu_limit_after") or infra_cmp.get("cpu_limit_per_pod_after_millicores"),
            "memory_limit_before": outcome.get("memory_limit_before") or infra_cmp.get("memory_limit_per_pod_before_bytes"),
            "memory_limit_after": outcome.get("memory_limit_after") or infra_cmp.get("memory_limit_per_pod_after_bytes"),
            "replicas_before": outcome.get("replicas_before") or infra_cmp.get("deployment_replicas_before"),
            "replicas_after": outcome.get("replicas_after") or infra_cmp.get("deployment_replicas_after"),
            "replica_delta": outcome.get("replica_delta") or infra_cmp.get("namespace_running_pods_delta"),
            "scale_out_occurred": outcome.get("scale_out_occurred") or infra_cmp.get("scale_out_occurred"),
            "scale_up_occurred": outcome.get("scale_up_occurred") or infra_cmp.get("scale_up_occurred"),
        })

    def _target_metrics_from_experience(self, ex):
        evidence_before = self._get_nested(ex, ["evidence", "before"], {}) or {}
        before = self._get_nested(ex, ["before"], {}) or {}
        planner_target = self._get_nested(ex, ["planner_context", "target_service"], {}) or {}
        state_target = self._get_nested(ex, ["incident", "state_before", "target_service"], {}) or {}

        metrics = {
            "cpu_usage_to_limit_pct_p95": (
                evidence_before.get("cpu_usage_to_limit_pct_p95")
                or before.get("cpu_usage_to_limit_pct_p95")
                or self._get_nested(planner_target, ["cpu", "usage_to_limit_pct", "p95"])
                or self._get_nested(state_target, ["cpu", "usage_to_limit_pct", "p95"])
            ),
            "cpu_throttle_pct_p95": (
                evidence_before.get("cpu_throttle_pct_p95")
                or before.get("cpu_throttle_pct_p95")
                or self._get_nested(planner_target, ["cpu", "throttle_pct", "p95"])
                or self._get_nested(state_target, ["cpu", "throttle_pct", "p95"])
            ),
            "memory_usage_to_limit_pct_p95": (
                evidence_before.get("memory_usage_to_limit_pct_p95")
                or before.get("memory_usage_to_limit_pct_p95")
                or self._get_nested(planner_target, ["memory", "usage_to_limit_pct", "p95"])
                or self._get_nested(state_target, ["memory", "usage_to_limit_pct", "p95"])
            ),
            "memory_working_set_mb_p95": (
                evidence_before.get("memory_working_set_mb_p95")
                or before.get("memory_working_set_mb_p95")
                or self._get_nested(planner_target, ["memory", "working_set_mb", "p95"])
                or self._get_nested(state_target, ["memory", "working_set_mb", "p95"])
            ),
            "latency_p95": (
                evidence_before.get("latency_p95")
                or before.get("latency_p95")
                or self._get_nested(planner_target, ["application", "latency_p95"])
                or self._get_nested(state_target, ["application", "latency_p95"])
            ),
            "error_5xx": (
                evidence_before.get("error_5xx")
                or before.get("error_5xx")
                or self._get_nested(planner_target, ["application", "error_5xx"])
                or self._get_nested(state_target, ["application", "error_5xx"])
            ),
            "replicas_desired": (
                evidence_before.get("replicas_desired")
                or before.get("replicas_desired")
                or self._get_nested(planner_target, ["replicas", "desired"])
                or self._get_nested(state_target, ["replicas", "desired"])
            ),
            "replicas_ready": (
                evidence_before.get("replicas_ready")
                or before.get("replicas_ready")
                or self._get_nested(planner_target, ["replicas", "ready"])
                or self._get_nested(state_target, ["replicas", "ready"])
            ),
            "restart_count": (
                evidence_before.get("restart_count")
                or before.get("restart_count")
                or self._get_nested(planner_target, ["health", "pod_restarts", "max"])
                or self._get_nested(state_target, ["health", "pod_restarts", "max"])
            ),
            "oom_kills": (
                evidence_before.get("oom_kills")
                or before.get("oom_kills")
                or self._get_nested(planner_target, ["health", "oom_kills", "max"])
                or self._get_nested(state_target, ["health", "oom_kills", "max"])
            ),
        }

        return self._clean_none(metrics)

    def format_examples_for_prompt(self, examples):
        if not examples:
            return "No prior executor/code experience available."

        lines = []

        for i, ex in enumerate(examples, 1):
            incident = ex.get("incident", {}) or {}
            keys = ex.get("retrieval_keys", {}) or {}
            plan = ex.get("plan", {}) or {}
            raw_plan = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}
            remediation = ex.get("remediation", {}) or {}
            lesson = ex.get("execution_lesson", {}) or {}
            feedback = self._flat_outcome(ex)

            target_changes = self._target_changes_from_experience(ex)
            normalized_action = self._normalized_action_from_experience(ex)

            actions = (
                plan.get("actions")
                or raw_plan.get("actions")
                or []
            )

            playbook_yaml = remediation.get("playbook_yaml")

            example = {
                "example": i,
                "fault_type": keys.get("fault_type") or incident.get("fault_type") or incident.get("fault"),
                "service": keys.get("service") or incident.get("service") or incident.get("target_service"),
                "strategy": plan.get("strategy") or raw_plan.get("strategy"),
                "normalized_action": normalized_action,
                "resource_changes": self._resource_change_summary(ex),
                "actions": actions,
                "execution": {
                    "status": remediation.get("execution_status"),
                    "error": remediation.get("execution_error"),
                    "playbook_retries": remediation.get("playbook_retries"),
                },
                "outcome": {
                            "reward": feedback.get("reward"),
                        },
                "playbook_yaml": playbook_yaml
            }

            lines.append(json.dumps(example, separators=(",", ":"), sort_keys=False, default=str))

        return "\n".join(lines)

    def build_prompt(
        self,
        plan: Dict[str, Any],
        service: str,
        namespace: str,
        exp_dir: Path,
        examples: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        formatted_examples = self.format_examples_for_prompt(examples)

        _, user_template = get_executor_prompts()

        return user_template.format(
            plan_json=json.dumps(plan, indent=2, default=str),
            service=service,
            namespace=namespace,
            examples=formatted_examples,
        )

    def generate_playbook(
        self,
        plan,
        service,
        namespace,
        exp_dir,
        icl_examples=None,
    ):
        prompt = self.build_prompt(
                                plan=plan,
                                service=service,
                                namespace=namespace,
                                exp_dir=exp_dir,
                                examples=icl_examples,
                            )

        with open(exp_dir / "code_prompt.txt", "w") as f:
            f.write(prompt)

        print("[INFO] Generating kubectl-based Ansible playbook via LLM")
        print(prompt)

        playbook_yaml = self.llm.generate(
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        playbook_yaml = strip_markdown_fences(playbook_yaml)

        with open(exp_dir / "playbook_generated.yaml", "w") as f:
            f.write(playbook_yaml)

        return playbook_yaml

    def regenerate_playbook(
        self,
        service: str,
        namespace: str,
        plan: Dict[str, Any],
        failed_yaml: str,
        stdout: str,
        error_log: str,
    ) -> str:
        execution_feedback = stdout if stdout and stdout.strip() else error_log

        fix_prompt = build_playbook_fix_prompt(
                service=service,
                namespace=namespace,
                plan=plan,
                failed_yaml=failed_yaml,
                execution_feedback=execution_feedback,
            )

        print("[INFO] Regenerating playbook using execution feedback")
        print(fix_prompt)

        fixed_yaml = self.llm.generate(
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            user_prompt=fix_prompt,
        )

        return strip_markdown_fences(fixed_yaml)


def strip_markdown_fences(content: str) -> str:
    if content is None:
        return ""

    cleaned = content.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    return cleaned


def build_playbook_fix_prompt(
    service: str,
    namespace: str,
    plan: Dict[str, Any],
    failed_yaml: str,
    execution_feedback: str,
) -> str:
    return f"""
You generated a kubectl-based Ansible playbook for EV-AIM, but it failed during execution.

Target Service:
{service}

Namespace:
{namespace}

Mitigation Plan JSON:
{json.dumps(plan, indent=2, default=str)}

Failed Playbook:
{failed_yaml}

Execution Feedback:
{execution_feedback}

Fix the playbook.

Rules:
- Return RAW YAML only.
- Start with "- hosts: localhost".
- Use ONLY kubectl shell commands.
- Do NOT use kubernetes.core modules.
- Read namespace, deployment, container, target_changes, and normalized_action from the mitigation plan.
- Do NOT delete pods unless explicitly stated in the plan actions.
- Fix YAML syntax.
- Fix kubectl syntax.
- Keep retries and delay as integers.
- Implement only the given normalized_action/action_type and target_changes.
- Do NOT add rollout status, monitoring, metric collection, HPA changes, or extra validation tasks.
- If the target_changes value caused a kubectl syntax/unit error, keep the same intent but correct only the unit/syntax.
- Never use the default namespace if namespace is provided.
- Never omit the namespace.
- if there is a container name missing error then add a code to extract -c <container> and store it for later use in the code.

Return ONLY corrected YAML.
"""
