"""
Unified EV-AIM experience store.

Stores one complete outcome-based record per run in:
    knowledge/evaim_experience.jsonl

Updated for fault-aware feedback:
- FRQ is the primary recovery signal.
- normalized_action/action_type is stored consistently for planner and executor retrieval.
- before/after evidence includes CPU limit, memory limit, replicas, latency, errors,
  pod health, restart/OOM signals, and resource configuration changes.
- outcome stores symptom deltas, improved/degraded metrics, primary_metric_fixed,
  resource_penalty, and resource_changes.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

FAULT_TO_SUFFIX = {
    "cpu_hog": "cpu",
    "cpu_pressure": "cpu",
    "mem_stress": "mem",
    "memory_pressure": "mem",
    "disk_stress": "disk",
    "disk_pressure": "disk",
}


def get_experience_path(namespace: str, fault_type: str) -> Path:
    namespace = namespace.lower()
    fault_type = fault_type.lower()

    if "robot" in namespace:
        app = "rs"
    elif "sock" in namespace:
        app = "ss"
    elif "online" in namespace:
        app = "ob"
    else:
        raise ValueError(f"Unknown namespace: {namespace}")

    resource = FAULT_TO_SUFFIX[fault_type]

    path = Path(f"knowledge/evaim_experience_{app}_{resource}_noreward.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())


def _safe_json(obj: Any) -> Any:
    """Make common non-JSON values serializable."""
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, dict):
            return {str(k): _safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_safe_json(v) for v in obj]
        return str(obj)


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _safe_delta(after: Any, before: Any) -> Optional[float]:
    a = _to_float(after)
    b = _to_float(before)
    if a is None or b is None:
        return None
    return round(a - b, 6)


def _safe_ratio_delta(after: Any, before: Any) -> Optional[float]:
    a = _to_float(after)
    b = _to_float(before)
    if a is None or b is None or abs(b) <= 1e-12:
        return None
    return round((a - b) / b, 6)


def hash_text(text: Optional[str], length: int = 12) -> Optional[str]:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def load_experiences(namespace: str, fault_type: str):
    path = get_experience_path(namespace, fault_type)
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_experience(
    record: Dict[str, Any],
    namespace: str,
    fault_type: str,
):
    path = get_experience_path(namespace, fault_type)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_safe_json(record), sort_keys=True) + "\n")


def _get_nested(d: Optional[Dict[str, Any]], path: list, default=None):
    cur = d or {}
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _ratio_to_pct(v):
    try:
        if v is None:
            return None
        return round(float(v) * 100.0, 2)
    except Exception:
        return None


def _bytes_to_mb(v):
    try:
        if v is None:
            return None
        return round(float(v) / (1024 * 1024), 2)
    except Exception:
        return None


def _metric_stat(metrics, service, group, metric, stat):
    return _get_nested(
        metrics,
        [
            "service_observations",
            service,
            "metrics",
            group,
            metric,
            "aggregate_stats",
            stat,
        ],
    )


def _metric_values(metrics, service, group, metric):
    return _get_nested(
        metrics,
        [
            "service_observations",
            service,
            "metrics",
            group,
            metric,
            "values",
        ],
        {},
    ) or {}


def _pick_first(*values):
    for v in values:
        if v is not None:
            return v
    return None


# -----------------------------------------------------------------------------
# Action normalization fallback
# -----------------------------------------------------------------------------

def _normalize_action_from_plan(plan: Dict[str, Any], feedback: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prefer compute_feedback's normalized_action. Fall back to planner fields.
    This keeps the store compatible with old runs and with the new feedback code.
    """
    normalized = feedback.get("normalized_action")
    if isinstance(normalized, dict) and normalized.get("action_type"):
        return {
            "action_type": normalized.get("action_type"),
            "target": normalized.get("target"),
            "value": normalized.get("value"),
        }

    target_changes = plan.get("target_changes") or {}
    change_type = str(target_changes.get("type") or "none").lower()

    action_type = plan.get("action_type") or feedback.get("plan_action")
    if not action_type:
        if change_type == "replicas":
            action_type = "scale_out"
        elif change_type == "cpu_limit":
            action_type = "scale_up_cpu"
        elif change_type == "memory_limit":
            action_type = "scale_up_memory"
        elif change_type == "image":
            action_type = "rollback"
        elif change_type == "config":
            action_type = "config_fix"
        else:
            action_type = "none"

    return {
        "action_type": action_type,
        "target": (
            plan.get("deployment")
            or plan.get("target_deployment")
            or plan.get("service")
            or plan.get("target_service")
        ),
        "value": target_changes.get("target_value"),
    }


# -----------------------------------------------------------------------------
# Evidence and change extraction
# -----------------------------------------------------------------------------

def _extract_target_evidence(
    *,
    metrics_before,
    metrics_after,
    infrastructure_before,
    infrastructure_after,
    service,
    deployment,
):
    def service_evidence(metrics):
        if not isinstance(metrics, dict) or not service:
            return {}

        pod_phase = _metric_values(metrics, service, "pod_health", "pod_phase_count")

        return {
            # Resource usage and pressure
            "cpu_usage_cores_p95": _metric_stat(metrics, service, "container_resources", "cpu_usage_cores", "p95"),
            "cpu_usage_cores_last": _metric_stat(metrics, service, "container_resources", "cpu_usage_cores", "last"),
            "cpu_usage_to_limit_pct_p95": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "cpu_usage_to_limit_ratio", "p95")
            ),
            "cpu_usage_to_limit_pct_last": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "cpu_usage_to_limit_ratio", "last")
            ),
            "cpu_throttle_pct_p95": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "cpu_throttle_ratio", "p95")
            ),
            "cpu_throttle_pct_last": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "cpu_throttle_ratio", "last")
            ),
            "memory_usage_to_limit_pct_p95": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "memory_usage_to_limit_ratio", "p95")
            ),
            "memory_usage_to_limit_pct_last": _ratio_to_pct(
                _metric_stat(metrics, service, "container_resources", "memory_usage_to_limit_ratio", "last")
            ),
            "memory_working_set_mb_p95": _bytes_to_mb(
                _metric_stat(metrics, service, "container_resources", "memory_working_set_bytes", "p95")
            ),
            "memory_working_set_mb_last": _bytes_to_mb(
                _metric_stat(metrics, service, "container_resources", "memory_working_set_bytes", "last")
            ),

            # Disk I/O
            "fs_read_bytes_per_sec_p95": _metric_stat(
                metrics, service, "container_resources",
                "fs_read_bytes_per_sec", "p95"
            ),
            "fs_read_bytes_per_sec_last": _metric_stat(
                metrics, service, "container_resources",
                "fs_read_bytes_per_sec", "last"
            ),

            "fs_write_bytes_per_sec_p95": _metric_stat(
                metrics, service, "container_resources",
                "fs_write_bytes_per_sec", "p95"
            ),
            "fs_write_bytes_per_sec_last": _metric_stat(
                metrics, service, "container_resources",
                "fs_write_bytes_per_sec", "last"
            ),

            "fs_read_ops_per_sec_p95": _metric_stat(
                metrics, service, "container_resources",
                "fs_read_ops_per_sec", "p95"
            ),
            "fs_write_ops_per_sec_p95": _metric_stat(
                metrics, service, "container_resources",
                "fs_write_ops_per_sec", "p95"
            ),

            "fs_usage_bytes": _metric_stat(
                metrics, service, "container_resources",
                "fs_usage_bytes", "last"
            ),

            "fs_limit_bytes": _metric_stat(
                metrics, service, "container_resources",
                "fs_limit_bytes", "last"
            ),

            "fs_usage_to_limit_ratio": _metric_stat(
                metrics, service, "container_resources",
                "fs_usage_to_limit_ratio", "last"
            ),

            # Resource configuration from Prometheus, if available
            "cpu_request_per_pod_cores": _metric_stat(metrics, service, "container_resources", "cpu_request_per_pod", "last"),
            "cpu_limit_per_pod_cores": _metric_stat(metrics, service, "container_resources", "cpu_limit_per_pod", "last"),
            "memory_request_per_pod_mb": _bytes_to_mb(
                _metric_stat(metrics, service, "container_resources", "memory_request_per_pod_bytes", "last")
            ),
            "memory_limit_per_pod_mb": _bytes_to_mb(
                _metric_stat(metrics, service, "container_resources", "memory_limit_per_pod_bytes", "last")
            ),

            # Application symptoms
            "request_rate": _metric_stat(metrics, service, "application_api", "request_rate", "last"),
            "latency_p95": _metric_stat(metrics, service, "application_api", "latency_p95", "p95"),
            "error_5xx": _metric_stat(metrics, service, "application_api", "error_rate_5xx", "last"),

            # Deployment/pod state
            "replicas_desired": _metric_stat(metrics, service, "deployment_health", "replicas_desired", "last"),
            "replicas_ready": _metric_stat(metrics, service, "deployment_health", "replicas_ready", "last"),
            "replicas_available": _metric_stat(metrics, service, "deployment_health", "replicas_available", "last"),
            "replicas_unavailable": _metric_stat(metrics, service, "deployment_health", "replicas_unavailable", "last"),
            "deployment_generation_mismatch": _metric_stat(metrics, service, "deployment_health", "deployment_generation_mismatch", "last"),
            "running_pods": pod_phase.get("Running", 0.0),
            "pending_pods": pod_phase.get("Pending", 0.0),
            "failed_pods": pod_phase.get("Failed", 0.0),
            "restart_count": _metric_stat(metrics, service, "pod_health", "pod_restarts", "sum"),
            "oom_kills": _metric_stat(metrics, service, "pod_health", "oom_kills", "sum"),
            "pod_not_ready": _metric_stat(metrics, service, "pod_health", "pod_not_ready", "sum"),
        }

    before = service_evidence(metrics_before)
    after = service_evidence(metrics_after)

    # Prefer infra snapshots for actual configured limits because Prometheus may miss them.
    before["cpu_limit_per_pod_millicores"] = _pick_first(
        _get_nested(infrastructure_before, ["target_cpu_limit_per_pod_millicores"]),
        _get_nested(infrastructure_before, ["cpu_limit_per_pod_millicores"]),
    )
    after["cpu_limit_per_pod_millicores"] = _pick_first(
        _get_nested(infrastructure_after, ["target_cpu_limit_per_pod_millicores"]),
        _get_nested(infrastructure_after, ["cpu_limit_per_pod_millicores"]),
    )

    before["memory_limit_per_pod_bytes"] = _pick_first(
        _get_nested(infrastructure_before, ["target_memory_limit_per_pod_bytes"]),
        _get_nested(infrastructure_before, ["memory_limit_per_pod_bytes"]),
    )
    after["memory_limit_per_pod_bytes"] = _pick_first(
        _get_nested(infrastructure_after, ["target_memory_limit_per_pod_bytes"]),
        _get_nested(infrastructure_after, ["memory_limit_per_pod_bytes"]),
    )

    before["memory_limit_per_pod_mb"] = before.get("memory_limit_per_pod_mb") or _bytes_to_mb(
        before.get("memory_limit_per_pod_bytes")
    )
    after["memory_limit_per_pod_mb"] = after.get("memory_limit_per_pod_mb") or _bytes_to_mb(
        after.get("memory_limit_per_pod_bytes")
    )

    infra_before = {
        "namespace_running_pods": _get_nested(infrastructure_before, ["namespace_running_pods"]),
        "namespace_pending_pods": _get_nested(infrastructure_before, ["namespace_pending_pods"]),
        "namespace_failed_pods": _get_nested(infrastructure_before, ["namespace_failed_pods"]),
        "node_memory_available_ratio": _get_nested(infrastructure_before, ["node_memory_available_ratio"]),
        "node_disk_pressure": _get_nested(infrastructure_before, ["node_disk_pressure"]),
        "node_disk_available_ratio": _get_nested(infrastructure_before, ["node_disk_available_ratio"]),
    }

    infra_after = {
        "namespace_running_pods": _get_nested(infrastructure_after, ["namespace_running_pods"]),
        # fixed bug: pending should read namespace_pending_pods, not namespace_failed_pods
        "namespace_pending_pods": _get_nested(infrastructure_after, ["namespace_pending_pods"]),
        "namespace_failed_pods": _get_nested(infrastructure_after, ["namespace_failed_pods"]),
        "node_memory_available_ratio": _get_nested(infrastructure_after, ["node_memory_available_ratio"]),
    }

    return {
        "service": service,
        "deployment": deployment,
        "before": before,
        "after": after,
        "infrastructure_before": infra_before,
        "infrastructure_after": infra_after,
    }


def _build_resource_changes(evidence: Dict[str, Any], infrastructure_comparison: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    before = evidence.get("before", {}) or {}
    after = evidence.get("after", {}) or {}
    infra_cmp = infrastructure_comparison or {}

    return {
        "replicas_desired_before": before.get("replicas_desired"),
        "replicas_desired_after": after.get("replicas_desired"),
        "replicas_ready_before": before.get("replicas_ready"),
        "replicas_ready_after": after.get("replicas_ready"),
        "replicas_desired_delta": _safe_delta(after.get("replicas_desired"), before.get("replicas_desired")),
        "replicas_ready_delta": _safe_delta(after.get("replicas_ready"), before.get("replicas_ready")),
        "cpu_limit_before_millicores": before.get("cpu_limit_per_pod_millicores"),
        "cpu_limit_after_millicores": after.get("cpu_limit_per_pod_millicores"),
        "cpu_limit_delta_millicores": _safe_delta(
            after.get("cpu_limit_per_pod_millicores"), before.get("cpu_limit_per_pod_millicores")
        ),
        "cpu_limit_delta_ratio": _safe_ratio_delta(
            after.get("cpu_limit_per_pod_millicores"), before.get("cpu_limit_per_pod_millicores")
        ),
        "memory_limit_before_bytes": before.get("memory_limit_per_pod_bytes"),
        "memory_limit_after_bytes": after.get("memory_limit_per_pod_bytes"),
        "memory_limit_delta_bytes": _safe_delta(
            after.get("memory_limit_per_pod_bytes"), before.get("memory_limit_per_pod_bytes")
        ),
        "memory_limit_delta_ratio": _safe_ratio_delta(
            after.get("memory_limit_per_pod_bytes"), before.get("memory_limit_per_pod_bytes")
        ),
        "scale_out_occurred": infra_cmp.get("scale_out_occurred"),
        "scale_up_occurred": infra_cmp.get("scale_up_occurred"),
        "resource_change_occurred": infra_cmp.get("resource_change_occurred"),
        "disk_usage_before": before.get("fs_usage_bytes"),
        "disk_usage_after": after.get("fs_usage_bytes"),
        "disk_usage_delta": _safe_delta(
            after.get("fs_usage_bytes"),
            before.get("fs_usage_bytes"),
        ),

        "disk_usage_ratio_before": before.get("fs_usage_to_limit_ratio"),
        "disk_usage_ratio_after": after.get("fs_usage_to_limit_ratio"),
        "disk_usage_ratio_delta": _safe_delta(
            after.get("fs_usage_to_limit_ratio"),
            before.get("fs_usage_to_limit_ratio"),
        ),
    }


def _build_symptom_deltas(evidence: Dict[str, Any], feedback: Dict[str, Any]) -> Dict[str, Any]:
    before = evidence.get("before", {}) or {}
    after = evidence.get("after", {}) or {}

    explicit = feedback.get("symptom_deltas")
    if isinstance(explicit, dict) and explicit:
        return explicit

    deltas = {
        "latency_p95_delta": _safe_delta(after.get("latency_p95"), before.get("latency_p95")),
        "error_5xx_delta": _safe_delta(after.get("error_5xx"), before.get("error_5xx")),
        "cpu_usage_to_limit_pct_p95_delta": _safe_delta(
            after.get("cpu_usage_to_limit_pct_p95"), before.get("cpu_usage_to_limit_pct_p95")
        ),
        "cpu_throttle_pct_p95_delta": _safe_delta(after.get("cpu_throttle_pct_p95"), before.get("cpu_throttle_pct_p95")),
        "memory_usage_to_limit_pct_p95_delta": _safe_delta(
            after.get("memory_usage_to_limit_pct_p95"), before.get("memory_usage_to_limit_pct_p95")
        ),
        "replicas_unavailable_delta": _safe_delta(after.get("replicas_unavailable"), before.get("replicas_unavailable")),
        "pod_not_ready_delta": _safe_delta(after.get("pod_not_ready"), before.get("pod_not_ready")),
        "restart_count_delta": _safe_delta(after.get("restart_count"), before.get("restart_count")),
        "oom_kills_delta": _safe_delta(after.get("oom_kills"), before.get("oom_kills")),
        "fs_read_bytes_delta": _safe_delta(
                    after.get("fs_read_bytes_per_sec_p95"),
                    before.get("fs_read_bytes_per_sec_p95"),
                ),

                "fs_write_bytes_delta": _safe_delta(
                    after.get("fs_write_bytes_per_sec_p95"),
                    before.get("fs_write_bytes_per_sec_p95"),
                ),

                "fs_usage_ratio_delta": _safe_delta(
                    after.get("fs_usage_to_limit_ratio"),
                    before.get("fs_usage_to_limit_ratio"),
                ),
    }
    return {k: v for k, v in deltas.items() if v is not None}


def _build_metric_outcome_lists(feedback: Dict[str, Any], symptom_deltas: Dict[str, Any]) -> tuple[list[str], list[str]]:
    improved = feedback.get("improved_metrics")
    degraded = feedback.get("degraded_metrics")

    if isinstance(improved, list) or isinstance(degraded, list):
        return list(improved or []), list(degraded or [])

    # Fallback convention: for pressure/error/latency/unavailable symptoms, negative delta is improvement.
    improved_metrics: list[str] = []
    degraded_metrics: list[str] = []

    name_map = {
        "latency_p95_delta": "latency",
        "error_5xx_delta": "error",
        "cpu_usage_to_limit_pct_p95_delta": "cpu",
        "cpu_throttle_pct_p95_delta": "cpu_throttle",
        "memory_usage_to_limit_pct_p95_delta": "memory",
        "replicas_unavailable_delta": "replicas_unavailable",
        "pod_not_ready_delta": "pod_ready",
        "fs_read_bytes_delta": "disk_read",
        "fs_write_bytes_delta": "disk_write",
        "fs_usage_ratio_delta": "disk_usage",
        "oom_kills_delta": "oom_kills",
    }

    for key, label in name_map.items():
        val = _to_float(symptom_deltas.get(key))
        if val is None:
            continue
        if val < 0:
            improved_metrics.append(label)
        elif val > 0:
            degraded_metrics.append(label)

    return improved_metrics, degraded_metrics


# -----------------------------------------------------------------------------
# Main store function
# -----------------------------------------------------------------------------

def store_unified_experience(
    *,
    incident: Dict[str, Any],
    plan: Dict[str, Any],
    playbook_yaml: Optional[str],
    metrics_before: Dict[str, Any],
    metrics_after: Optional[Dict[str, Any]],
    infrastructure_before: Optional[Dict[str, Any]],
    infrastructure_after: Optional[Dict[str, Any]],
    infrastructure_comparison: Optional[Dict[str, Any]],
    feedback: Dict[str, Any],
    execution_status: str,
    execution_error: Optional[str] = None,
    ansible_stdout: Optional[str] = None,
    ansible_recap: Optional[Dict[str, Any]] = None,
    rollout_result: Optional[Dict[str, Any]] = None,
    playbook_retries: int = 0,
    exp_dir: Optional[str] = None,
    store_failed: bool = True,
    min_reward_to_store: Optional[float] = None,
) -> Dict[str, Any]:

    reward = feedback.get("reward")

    if not store_failed and execution_status != "success":
        print(f"[EXPERIENCE] Skipping failed experience: status={execution_status}")
        return {}

    if min_reward_to_store is not None and reward is not None:
        try:
            if float(reward) < float(min_reward_to_store):
                print(
                    f"[EXPERIENCE] Skipping low-reward experience: "
                    f"reward={float(reward):.3f} < {float(min_reward_to_store):.3f}"
                )
                return {}
        except (TypeError, ValueError):
            pass

    target_changes = plan.get("target_changes") or {
        "type": "none",
        "previous_value": "none",
        "target_value": "none",
    }

    actions = plan.get("actions", [])
    if not isinstance(actions, list):
        actions = [str(actions)]

    service = (
        plan.get("service")
        or plan.get("target_service")
        or incident.get("service")
        or incident.get("target_service")
    )

    deployment = (
        plan.get("deployment")
        or plan.get("target_deployment")
        or incident.get("deployment")
        or incident.get("target_deployment")
    )

    namespace = (
        plan.get("namespace")
        or plan.get("target_namespace")
        or incident.get("namespace")
    )

    fault_type = incident.get("fault_type") or incident.get("fault")

    evidence = _extract_target_evidence(
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        infrastructure_before=infrastructure_before,
        infrastructure_after=infrastructure_after,
        service=service,
        deployment=deployment,
    )

    normalized_action = _normalize_action_from_plan(plan, feedback)
    action_type = normalized_action.get("action_type")
    resource_changes = _build_resource_changes(evidence, infrastructure_comparison)
    symptom_deltas = _build_symptom_deltas(evidence, feedback)
    improved_metrics, degraded_metrics = _build_metric_outcome_lists(feedback, symptom_deltas)

    outcome_summary = {
        "FRQ": feedback.get("FRQ"),
        "RQ": feedback.get("RQ"),
        "SHS_before": feedback.get("SHS_before"),
        "SHS_after": feedback.get("SHS_after"),
        "delta_SHS": feedback.get("delta_SHS"),
        "PS": feedback.get("PS"),
        "ES": feedback.get("ES"),
        "reward": feedback.get("reward"),
        "raw_resource_cost": feedback.get("raw_resource_cost"),
        "resource_penalty": feedback.get("resource_cost"),
        "degradation_penalty": feedback.get("degradation_penalty"),
        "primary_metric_fixed": feedback.get("primary_metric_fixed"),
        "recovery_success": feedback.get("recovery_success"),
        "regression": feedback.get("regression"),
        "fault_success_reason": feedback.get("fault_success_reason"),
        "code_changed_system": feedback.get("code_changed_system"),
        "improved_metrics": improved_metrics,
        "degraded_metrics": degraded_metrics,
        "symptom_deltas": symptom_deltas,
        "resource_changes": resource_changes,
    }

    record = {
        "schema_version": "evaim_experience_v3_fault_aware",
        "timestamp": _now(),

        "experiment": {
            "experiment_dir": exp_dir,
        },

        "incident": {
            **incident,
            "app": incident.get("app"),
            "namespace": namespace,
            "service": service,
            "deployment": deployment,
            "fault_type": fault_type,
            "state_before": metrics_before,
            "infrastructure_before": infrastructure_before,
        },

        "plan": {
            "execution_required": plan.get("execution_required"),
            "execution_reason": plan.get("execution_reason"),
            "severity": plan.get("severity"),
            "diagnosis": plan.get("diagnosis"),
            "strategy": plan.get("strategy"),
            "root_cause_hypothesis": plan.get("root_cause_hypothesis"),
            "normalized_action": normalized_action,
            "target_changes": {
                "type": target_changes.get("type"),
                "previous_value": target_changes.get("previous_value"),
                "target_value": target_changes.get("target_value"),
            },
            "actions": actions,
            "safety_checks": plan.get("safety_checks", []),
            "success_criteria": plan.get("success_criteria", []),
        },

        "evidence": evidence,

        "remediation": {
            "playbook_yaml": playbook_yaml,
            "execution_status": execution_status,
            "execution_error": execution_error,
            "playbook_retries": playbook_retries,
            "ansible_recap": ansible_recap,
        },

        "outcome": {
            **outcome_summary,
        }

    }

    append_experience(
                    record,
                    namespace=namespace,
                    fault_type=fault_type,
                )

    print("[EXPERIENCE] Stored unified experience: ")

    return record
