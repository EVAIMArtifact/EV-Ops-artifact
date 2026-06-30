import json
from pathlib import Path
from typing import Dict, Any, Union, Optional

from src.planner.planner_prompt import PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE
from src.clients.llm_client import create_llm_client


CANONICAL_ACTION_TYPES = {
    "restart",
    "scale_out",
    "scale_up_cpu",
    "scale_up_memory",
    "rollback",
    "config_fix",
    "traffic_control",
    "wait",
    "none",
}

target_changes_TYPES = {
    "none",
    "replicas",
    "cpu_limit",
    "memory_limit",
    "image",
    "config",
}

ACTION_TO_target_changes = {
    "none": {"none"},
    "wait": {"none"},
    "restart": {"none", "replicas", "config"},
    "scale_out": {"replicas"},
    "scale_up_cpu": {"cpu_limit"},
    "scale_up_memory": {"memory_limit"},
    "rollback": {"image", "config"},
    "config_fix": {"config"},
    "traffic_control": {"config", "none"},
}

FAULT_ALLOWED_ACTIONS = {
    "mem_stress": {"scale_up_memory", "restart", "none", "wait"},
    "memory_pressure": {"scale_up_memory", "restart", "none", "wait"},

    "cpu_hog": {"scale_up_cpu", "scale_out", "restart", "none", "wait"},
    "cpu_pressure": {"scale_up_cpu", "scale_out", "restart", "none", "wait"},

    "pod_kill": {"restart", "wait", "none", "scale_out"},
    "pod_crash": {"restart", "wait", "none", "scale_out"},

    "net_delay": {"traffic_control", "restart", "rollback", "scale_out", "none", "wait"},
    "network_delay": {"traffic_control", "restart", "rollback", "scale_out", "none", "wait"},
    "network_latency": {"traffic_control", "restart", "rollback", "scale_out", "none", "wait"},

    "net_loss": {"traffic_control", "restart", "rollback", "scale_out", "none", "wait"},
    "packet_loss": {"traffic_control", "restart", "rollback", "scale_out", "none", "wait"},

    "dependency_failure": {"restart", "rollback", "scale_out", "config_fix", "none", "wait"},

    "bad_image": {"rollback"},
    "stuck_deployment": {"rollback", "config_fix", "restart"},
}

ALLOWED_EXECUTION_REASONS = {
    "active_degradation",
    "active_failure",
    "resource_pressure",
    "unsafe_state",
    "self_healed",
    "insufficient_evidence",
}

ALLOWED_SEVERITIES = {"low", "moderate", "high", "critical"}


def _fault_allowed_actions(fault_type: str) -> set[str]:
    # ft = str(fault_type or "").lower()
    # for key, actions in FAULT_ALLOWED_ACTIONS.items():
    #     if key in ft:
    #         return actions
    return CANONICAL_ACTION_TYPES


def _normalize_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1"}:
            return True
        if v in {"false", "no", "0"}:
            return False

    raise ValueError(f"Planner output field '{field_name}' must be boolean true/false.")


def _infer_action_type(plan: Dict[str, Any]) -> str:
    """
    Fallback normalizer for older LLM outputs. The prompt now requires action_type,
    but this keeps the planner robust if a model omits it.
    """
    explicit = str(plan.get("action_type") or "").strip().lower()
    if explicit:
        return explicit

    target_changes = plan.get("target_changes") or {}
    change_type = str(target_changes.get("type") or "none").strip().lower()

    if change_type == "replicas":
        return "scale_out"
    if change_type == "cpu_limit":
        return "scale_up_cpu"
    if change_type == "memory_limit":
        return "scale_up_memory"
    if change_type == "image":
        return "rollback"
    if change_type == "config":
        text = json.dumps(plan, default=str).lower()
        if "rollback" in text or "previous" in text:
            return "rollback"
        if "network" in text or "traffic" in text or "packet" in text:
            return "traffic_control"
        return "config_fix"
    # if change_type == "none":
    #     text = json.dumps(plan, default=str).lower()
    #     if "restart" in text or "rollout restart" in text or "delete pod" in text:
    #         return "restart"
    #     if "wait" in text or "self-healed" in text or "self healed" in text:
    #         return "wait"
    #     return "none"

    return "none"


def _validate_target_changes(plan: Dict[str, Any]) -> Dict[str, Any]:
    target_changes = plan.get("target_changes")

    if not isinstance(target_changes, dict):
        raise ValueError("Planner output field 'target_changes' must be an object.")

    change_type = str(target_changes.get("type") or "").strip().lower()
    target_changes["type"] = change_type

    if change_type not in target_changes_TYPES:
        raise ValueError(f"target_changes.type must be one of {target_changes_TYPES}.")

    if "target_value" not in target_changes:
        raise ValueError("target_changes.target_value is required.")

    if "previous_value" not in target_changes:
        # Keep backward compatibility. Retrieval examples now include before/after,
        # but the planner may not know the previous value in some cases.
        target_changes["previous_value"] = "unknown"

    return target_changes


def _validate_action_consistency(
    *,
    fault_type: str,
    action_type: str,
    target_changes: Dict[str, Any],
    execution_required: bool,
) -> None:
    if action_type not in CANONICAL_ACTION_TYPES:
        raise ValueError(f"action_type must be one of {CANONICAL_ACTION_TYPES}.")

    allowed_for_fault = _fault_allowed_actions(fault_type)
    if action_type not in allowed_for_fault:
        raise ValueError(
            f"action_type must be one of {allowed_for_fault}; got '{action_type}'."
        )

    change_type = target_changes.get("type")
    allowed_change_types = ACTION_TO_target_changes.get(action_type, target_changes_TYPES)

    if change_type not in allowed_change_types:
        raise ValueError(
            f"action_type='{action_type}' is inconsistent with target_changes.type='{change_type}'. "
            f"Allowed target_changes.type values: {allowed_change_types}."
        )

    if not execution_required:
        if action_type not in {"none", "wait"}:
            raise ValueError("action_type must be 'none' or 'wait' when execution_required=false.")
        if change_type != "none":
            raise ValueError("target_changes.type must be 'none' when execution_required=false.")
        if target_changes.get("target_value") not in {"none", None, ""}:
            raise ValueError("target_changes.target_value must be 'none' when execution_required=false.")
        return

    if action_type in {"none", "wait"}:
        raise ValueError("action_type cannot be 'none' or 'wait' when execution_required=true.")

    if change_type != "none":
        target_value = target_changes.get("target_value")
        if target_value in {"none", None, ""}:
            raise ValueError(
                "target_changes.target_value must be a real exact value when execution_required=true."
            )


class MitigationPlanner:
    def __init__(self, llm_client):
        self.llm = llm_client

    @classmethod
    def from_config(cls, model_config: Dict[str, Any]):
        llm_client = create_llm_client(model_config)
        return cls(llm_client)

    def __build_prompt(
        self,
        fault_type: str,
        metrics: Union[str, Dict[str, Any]],
        service: str = "unknown",
        experience: str = "None",
    ) -> str:

        if isinstance(metrics, str):
            metrics_str = metrics
        else:
            service = (
                metrics.get("incident", {}).get("target_service")
                or metrics.get("target_service")
                or metrics.get("service")
                or service
                or "unknown"
            )
            metrics_str = json.dumps(metrics, indent=2, default=str)

        return PLANNER_USER_TEMPLATE.format(
            fault_type=fault_type,
            service=service,
            metrics=metrics_str,
            experience=experience,
        )

    def plan(
        self,
        fault_type: str,
        exp_dir: Path,
        metrics: Union[str, Dict[str, Any]],
        service: str = "unknown",
        experience: str = "None",
    ) -> Dict[str, Any]:

        user_prompt = self.__build_prompt(
            fault_type=fault_type,
            metrics=metrics,
            service=service,
            experience=experience,
        )

        print("\nGenerated planner prompt:\n")
        print(user_prompt)
        print("\n[INFO] Planner prompt generated.")

        exp_dir.mkdir(parents=True, exist_ok=True)

        with open(exp_dir / "planner_prompt.txt", "w") as f:
            f.write(user_prompt)

        raw_output = self.llm.generate(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        with open(exp_dir / "planner_raw_output.txt", "w") as f:
            f.write(raw_output)

        return self._validate_and_parse(raw_output, fault_type=fault_type)

    def _validate_and_parse(self, raw_output: str, fault_type: str = "") -> Dict[str, Any]:
        cleaned_output = raw_output.strip()

        if cleaned_output.startswith("```"):
            lines = cleaned_output.split("\n")

            if lines and lines[0].startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            cleaned_output = "\n".join(lines).strip()

        try:
            plan = json.loads(cleaned_output)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Planner output is not valid JSON.\n"
                f"Original output:\n{raw_output}\n\n"
                f"Cleaned output:\n{cleaned_output}\n\n"
                f"JSON error: {str(e)}"
            )

        required_fields = {
            "execution_required",
            "execution_reason",
            "severity",
            "diagnosis",
            "strategy",
            "root_cause_hypothesis",
            "target_changes",
            "actions",
            "safety_checks",
            "success_criteria",
        }

        missing = required_fields - set(plan.keys())
        if missing:
            raise ValueError(f"Planner output missing fields: {missing}")

        plan["execution_required"] = _normalize_bool(
            plan.get("execution_required"),
            "execution_required",
        )

        if plan["execution_reason"] not in ALLOWED_EXECUTION_REASONS:
            raise ValueError(
                f"Planner output field 'execution_reason' must be one of {ALLOWED_EXECUTION_REASONS}."
            )

        if plan["severity"] not in ALLOWED_SEVERITIES:
            raise ValueError(
                "Planner output field 'severity' must be low, moderate, high, or critical."
            )

        target_changes = _validate_target_changes(plan)

        action_type = _infer_action_type(plan)

        # Repair inconsistent planner output:
        # execution_required=true but no executable target change/action.
        if plan["execution_required"] and action_type in {"none", "wait"}:
            plan["execution_required"] = False
            plan["execution_reason"] = "insufficient_evidence"

            plan["target_changes"] = {
                "type": "none",
                "target": plan.get("target_changes", {}).get("target", "target_deployment"),
                "previous_value": "none",
                "target_value": "none",
            }

            action_type = "wait"
            target_changes = plan["target_changes"]

            plan["actions"] = [
                "Observe the service and collect additional evidence before applying a Kubernetes change."
            ]

        _validate_action_consistency(
            fault_type=fault_type,
            action_type=action_type,
            target_changes=target_changes,
            execution_required=plan["execution_required"],
        )

        for field in ["actions", "safety_checks", "success_criteria"]:
            if field not in plan:
                plan[field] = []
            if not isinstance(plan[field], list):
                raise ValueError(f"Planner output field '{field}' must be a list.")

        if plan["execution_required"] and not plan["actions"]:
            raise ValueError(
                "Planner output must contain at least one executable action when execution_required=true."
            )

        # Add a normalized_action block consumed by feedback/retrieval.
        # This does not break older executors because target_changes/actions are unchanged.
        plan["normalized_action"] = {
            "action_type": action_type,
            "target": plan.get("target_changes", {}).get("target_resource")
            or plan.get("target_changes", {}).get("target")
            or "target_deployment",
            "value": plan.get("target_changes", {}).get("target_value"),
        }

        return plan
