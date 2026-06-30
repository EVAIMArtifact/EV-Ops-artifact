# src/planner/retrieval.py

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import random

DEFAULT_TOP_K = 3


FAULT_TO_SUFFIX = {
    "cpu_hog": "cpu",
    "cpu_pressure": "cpu",
    "resource_pressure_cpu": "cpu",

    "mem_stress": "mem",
    "memory_pressure": "mem",
    "resource_pressure_memory": "mem",

    "disk_stress": "disk",
    "disk_pressure": "disk",
    "disk_io": "disk",
}


def get_code_kb_path(namespace: str, fault_type: str) -> Path:
    """
    Returns the appropriate experience database path.

    Examples:
        online-boutique + mem_stress
            -> knowledge/evaim_experience_ob_mem.jsonl

        robot-shop + cpu_hog
            -> knowledge/evaim_experience_rs_cpu.jsonl

        sock-shop + disk_stress
            -> knowledge/evaim_experience_ss_disk.jsonl
    """

    namespace = namespace.lower()
    fault_type = fault_type.lower()

    if "online" in namespace:
        app = "ob"
    elif "robot" in namespace:
        app = "rs"
    elif "sock" in namespace:
        app = "ss"
    else:
        raise ValueError(f"Unknown namespace: {namespace}")

    resource = FAULT_TO_SUFFIX.get(fault_type)
    if resource is None:
        raise ValueError(f"Unsupported fault type: {fault_type}")

    return Path(f"knowledge/evaim_experience_{app}_{resource}_noreward.jsonl")


def _load_code_kb(namespace: str, fault_type: str) -> List[Dict[str, Any]]:
    kb_path = get_code_kb_path(namespace, fault_type)

    if not kb_path.exists():
        return []

    rows = []
    with open(kb_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return rows

def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def _get(d: Dict[str, Any], path: List[str], default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


def _norm(v: Optional[float], cap: float) -> float:
    if v is None:
        return 0.0
    return min(abs(v) / cap, 1.0)


def _metric_stat(metrics, service, group, metric, stat="p95"):
    return _get(
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
    return _get(
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


def build_signature(planner_metrics: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(planner_metrics, dict):
        return {}

    fault = planner_metrics.get("fault", {}) or planner_metrics.get("incident", {}) or {}

    fault_type = (
        fault.get("fault_type")
        or planner_metrics.get("fault_type")
    )

    service = (
        planner_metrics.get("target_service")
        or fault.get("service")
        or fault.get("target_service")
        or planner_metrics.get("service")
    )

    pod_phase = _metric_values(
        planner_metrics,
        service,
        "pod_health",
        "pod_phase_count",
    )

    waiting_reasons = _metric_values(
        planner_metrics,
        service,
        "pod_health",
        "container_waiting_reason",
    )

    terminated_reasons = _metric_values(
        planner_metrics,
        service,
        "pod_health",
        "container_terminated_reason",
    )

    symptoms = set()

    if pod_phase.get("Pending", 0) > 0:
        symptoms.add("pending_pods")
    if pod_phase.get("Failed", 0) > 0:
        symptoms.add("failed_pods")
    if waiting_reasons:
        symptoms.update([f"waiting:{r}" for r in waiting_reasons.keys()])
    if terminated_reasons:
        symptoms.update([f"terminated:{r}" for r in terminated_reasons.keys()])

    cpu_pct = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "cpu_usage_to_limit_ratio",
        "p95",
    )

    memory_pct = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "memory_usage_to_limit_ratio",
        "p95",
    )

    cpu_throttle_pct = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "cpu_throttle_ratio",
        "p95",
    )
    disk_read_bytes = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "fs_read_bytes_per_sec",
        "p95",
    )

    disk_write_bytes = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "fs_write_bytes_per_sec",
        "p95",
    )

    disk_usage_ratio = _metric_stat(
        planner_metrics,
        service,
        "container_resources",
        "fs_usage_to_limit_ratio",
        "last",
    )

    return {
        "fault_type": fault_type,
        "target_service": service,
        "symptoms": symptoms,

        "cpu_pct": _ratio_to_pct(cpu_pct),
        "memory_pct": _ratio_to_pct(memory_pct),
        "cpu_throttle_pct": _ratio_to_pct(cpu_throttle_pct),

        "replicas_desired": _metric_stat(
            planner_metrics,
            service,
            "deployment_health",
            "replicas_desired",
            "last",
        ),
        "replicas_ready": _metric_stat(
            planner_metrics,
            service,
            "deployment_health",
            "replicas_ready",
            "last",
        ),
        "replicas_available": _metric_stat(
            planner_metrics,
            service,
            "deployment_health",
            "replicas_available",
            "last",
        ),

        "pod_restarts": _metric_stat(
            planner_metrics,
            service,
            "pod_health",
            "pod_restarts",
            "sum",
        ),
        "oom_kills": _metric_stat(
            planner_metrics,
            service,
            "pod_health",
            "oom_kills",
            "sum",
        ),
        "disk_read_bytes": disk_read_bytes,
        "disk_write_bytes": disk_write_bytes,
        "disk_usage_ratio": _ratio_to_pct(disk_usage_ratio),
    }

def _ratio_to_pct(v):
    try:
        if v is None:
            return None
        return float(v) * 100.0
    except Exception:
        return None
    
def _symptom_similarity(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def signature_similarity(
    current: Dict[str, Any],
    past: Dict[str, Any],
) -> float:
    """
    Weighted similarity between current incident and past incident.
    """

    score = 0.0

    if current.get("fault_type") and current.get("fault_type") == past.get("fault_type"):
        score += 0.35

    if current.get("target_service") and current.get("target_service") == past.get("target_service"):
        score += 0.10

    score += 0.20 * _symptom_similarity(
        current.get("symptoms", set()),
        past.get("symptoms", set()),
    )

    cpu_diff = abs(
        (_to_float(current.get("cpu_pct")) or 0)
        - (_to_float(past.get("cpu_pct")) or 0)
    )
    score += 0.10 * (1.0 - min(cpu_diff / 100.0, 1.0))

    mem_diff = abs(
        (_to_float(current.get("memory_pct")) or 0)
        - (_to_float(past.get("memory_pct")) or 0)
    )
    score += 0.10 * (1.0 - min(mem_diff / 100.0, 1.0))

    if canonical_fault_type(current.get("fault_type")) == "disk_stress":

        read_diff = abs(
            (_to_float(current.get("disk_read_bytes")) or 0)
            - (_to_float(past.get("disk_read_bytes")) or 0)
        )

        score += 0.05 * (1.0 - min(read_diff / (50 * 1024 * 1024), 1.0))

        write_diff = abs(
            (_to_float(current.get("disk_write_bytes")) or 0)
            - (_to_float(past.get("disk_write_bytes")) or 0)
        )

        score += 0.05 * (1.0 - min(write_diff / (50 * 1024 * 1024), 1.0))

    if current.get("cpu_trend") and current.get("cpu_trend") == past.get("cpu_trend"):
        score += 0.05

    if current.get("memory_trend") and current.get("memory_trend") == past.get("memory_trend"):
        score += 0.05

    current_ready = _to_float(current.get("replicas_ready"))
    past_ready = _to_float(past.get("replicas_ready"))

    if current_ready is not None and past_ready is not None:
        score += 0.05 * (1.0 - min(abs(current_ready - past_ready) / 5.0, 1.0))

    return round(score, 4)

def _flat_outcome(exp: Dict[str, Any]) -> Dict[str, Any]:
    outcome = exp.get("outcome", {}) or exp.get("feedback", {}) or {}

    if isinstance(outcome.get("feedback"), dict):
        merged = dict(outcome.get("feedback") or {})
        for k, v in outcome.items():
            if k != "feedback" and v is not None:
                merged[k] = v
        return merged

    return outcome

def canonical_fault_type(ft: Any) -> str:
    ft = str(ft or "").lower()
    if ft in {"net_delay", "network_delay", "network_latency"}:
        return "net_delay"
    if ft in {"net_loss", "packet_loss"}:
        return "net_loss"
    if ft in {"pod_kill", "pod_crash"}:
        return "pod_kill"
    if ft in {"mem_stress", "memory_pressure"}:
        return "mem_stress"
    if ft in {"cpu_hog", "cpu_pressure", "cpu_throttle"}:
        return "cpu_hog"
    if ft in {"disk_stress", "disk_pressure", "disk_io"}:
        return "disk_stress"    
    return ft


def outcome_quality(exp: Dict[str, Any]) -> float:
    outcome = _flat_outcome(exp)

    frq = clamp01(outcome.get("FRQ"))
    reward = _to_float(outcome.get("reward")) or 0.0
    reward01 = (max(-1.0, min(1.0, reward)) + 1.0) / 2.0
    es = clamp01(outcome.get("ES"))
    ps = clamp01(outcome.get("PS"))

    recovery_success = bool(outcome.get("recovery_success", False))
    regression = bool(outcome.get("regression", False))

    score = (0.25 * es
    + 0.75 * reward01
)

    if regression:
        score *= 0.25

    return round(clamp01(score), 4)


def clamp01(v):
    try:
        if v is None:
            return 0.0
        return max(0.0, min(float(v), 1.0))
    except Exception:
        return 0.0

def _experience_signature(exp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Supports both new and older experience formats.

    Preferred new format:
      exp["planner_context"] or exp["incident"]["planner_metrics"]

    Older format:
      exp["incident"]["metrics"]
    """

    if isinstance(exp.get("planner_context"), dict):
        return build_signature(exp["planner_context"])

    incident = exp.get("incident", {})

    if isinstance(incident.get("planner_metrics"), dict):
        return build_signature(incident["planner_metrics"])

    if isinstance(incident.get("metrics"), dict):
        # legacy fallback
        m = incident["metrics"]
        return {
            "fault_type": incident.get("fault"),
            "target_service": incident.get("service"),
            "symptoms": set(),
            "cpu_pct": _get(m, ["target_service", "cpu", "usage_to_limit_pct", "p95"])
                or m.get("cpu_usage_to_limit_ratio")
                or m.get("cpu_usage_pct"),
            "memory_pct": _get(m, ["target_service", "memory", "usage_to_limit_pct", "p95"])
                or m.get("memory_usage_to_limit_ratio")
                or m.get("memory_usage_pct"),
            "cpu_throttle_pct": m.get("cpu_throttle_ratio") or m.get("cpu_throttle_pct"),
        }

    return {
        "fault_type": incident.get("fault"),
        "target_service": incident.get("service"),
        "symptoms": set(),
    }



def retrieve_experience(
        namespace: str,
    fault_type: str,
    metrics: Dict[str, Any],
    service: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    retrieval_mode: str = "evaim",
) -> List[Dict[str, Any]]:

    if not isinstance(metrics, dict):
        raise TypeError(
            f"retrieve_experience expected metrics dict, got {type(metrics).__name__}: {metrics}"
        )
    

    kb = _load_code_kb(namespace, fault_type)
    if not kb:
        print("[INFO] Experience KB empty.")
        return []
    
    if retrieval_mode == "random":
        return random.sample(kb, min(top_k, len(kb)))

    current_sig = build_signature(metrics)

    if fault_type:
        current_sig["fault_type"] = fault_type

    if service:
        current_sig["target_service"] = service

    candidates = []

    

    for exp in kb:
        past_sig = _experience_signature(exp)
        sim = signature_similarity(current_sig, past_sig)
        quality = outcome_quality(exp)

        incident = exp.get("incident", {}) or {}
        keys = exp.get("retrieval_keys", {}) or {}

        past_fault = (
            keys.get("fault_type")
            or incident.get("fault_type")
            or incident.get("fault")
            or exp.get("fault_type")
        )

        past_service = (
            keys.get("service")
            or incident.get("target_service")
            or incident.get("service")
            or exp.get("service")
        )

        same_fault = canonical_fault_type(past_fault) == canonical_fault_type(fault_type)
        same_service = service is not None and past_service == service

        MIN_REWARD = 0.45

        outcome = _flat_outcome(exp)
        raw_reward = _to_float(outcome.get("reward")) or 0.0

        # if raw_reward < MIN_REWARD:
        #     continue
        recovery_success = bool(outcome.get("recovery_success", False))
        regression = bool(outcome.get("regression", False))

        score = round((0.60 * sim) + (0.40 * quality), 4)

        candidates.append({
            "score": score,
            "sim": sim,
            "quality": quality,
            "same_fault": same_fault,
            "same_service": same_service,
            "recovery_success": recovery_success,
            "regression": regression,
            "exp": exp,
        })

    tiers = [
        # strongest: same fault + same service + successful
        lambda c: c["same_fault"] and c["quality"] >= 0.45  and c["same_service"],

        # same fault + successful, even if service differs
        lambda c: c["same_fault"] and c["quality"] >= 0.45,

        # same fault, high-quality failed/non-regressive examples can teach what not to repeat
        lambda c: c["quality"] >= 0.45,

        # # fallback: anything non-regressive
        # lambda c: not c["regression"],

        # # last fallback: all experiences
        # lambda c: True,
    ]

    selected = []
    seen_ids = set()

    for tier in tiers:
        tier_items = [c for c in candidates if tier(c)]
        tier_items = sorted(
            tier_items,
            key=lambda c: (c["score"], c["quality"], c["sim"]),
            reverse=True,
        )

        for c in tier_items:
            exp_id = (
                c["exp"].get("id")
                or c["exp"].get("fault_id")
                or json.dumps(c["exp"].get("incident", {}), sort_keys=True)
            )

            if exp_id in seen_ids:
                continue

            selected.append(c["exp"])
            seen_ids.add(exp_id)

            if len(selected) >= top_k:
                return selected

    return selected[:top_k]


def _compact_action(plan: Dict[str, Any]) -> Any:
    if not isinstance(plan, dict):
        return plan

    if "actions" in plan:
        return plan["actions"]

    if "strategy" in plan:
        return plan["strategy"]

    return plan


def _target_metrics_from_exp(exp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract useful target-service metrics from stored unified experience.
    Supports:
    - exp["planner_context"]["target_service"]
    - exp["evidence"]["before"]
    - legacy incident/state_before formats
    """

    evidence_before = _get(exp, ["evidence", "before"], {}) or {}
    planner_target = _get(exp, ["planner_context", "target_service"], {}) or {}
    state_target = _get(exp, ["incident", "state_before", "target_service"], {}) or {}

    return {
        "memory_usage_to_limit_pct_p95": (
            evidence_before.get("memory_usage_to_limit_pct_p95")
            or _get(planner_target, ["memory", "usage_to_limit_pct", "p95"])
            or _get(state_target, ["memory", "usage_to_limit_pct", "p95"])
        ),
        "memory_usage_to_limit_pct_last": (
            evidence_before.get("memory_usage_to_limit_pct_last")
            or _get(planner_target, ["memory", "usage_to_limit_pct", "last"])
            or _get(state_target, ["memory", "usage_to_limit_pct", "last"])
        ),
        "memory_working_set_mb_p95": (
            evidence_before.get("memory_working_set_mb_p95")
            or _get(planner_target, ["memory", "working_set_mb", "p95"])
            or _get(state_target, ["memory", "working_set_mb", "p95"])
        ),
        "cpu_usage_to_limit_pct_p95": (
            evidence_before.get("cpu_usage_to_limit_pct_p95")
            or _get(planner_target, ["cpu", "usage_to_limit_pct", "p95"])
            or _get(state_target, ["cpu", "usage_to_limit_pct", "p95"])
        ),
        "cpu_throttle_pct_p95": (
            evidence_before.get("cpu_throttle_pct_p95")
            or _get(planner_target, ["cpu", "throttle_pct", "p95"])
            or _get(state_target, ["cpu", "throttle_pct", "p95"])
        ),
        "latency_p95": (
            evidence_before.get("latency_p95")
            or _get(planner_target, ["application", "latency_p95"])
            or _get(state_target, ["application", "latency_p95"])
        ),
        "error_5xx": (
            evidence_before.get("error_5xx")
            or _get(planner_target, ["application", "error_5xx"])
            or _get(state_target, ["application", "error_5xx"])
        ),
        "replicas_desired": (
            evidence_before.get("replicas_desired")
            or _get(planner_target, ["replicas", "desired"])
            or _get(state_target, ["replicas", "desired"])
        ),
        "replicas_ready": (
            evidence_before.get("replicas_ready")
            or _get(planner_target, ["replicas", "ready"])
            or _get(state_target, ["replicas", "ready"])
        ),
        "oom_kills": (
            evidence_before.get("oom_kills")
            or _get(planner_target, ["health", "oom_kills", "max"])
            or _get(state_target, ["health", "oom_kills", "max"])
        ),
        "restart_count": (
            evidence_before.get("restart_count")
            or _get(planner_target, ["health", "pod_restarts", "max"])
            or _get(state_target, ["health", "pod_restarts", "max"])
        ),
        "fs_read_bytes_per_sec": (
            evidence_before.get("fs_read_bytes_per_sec")
        ),

        "fs_write_bytes_per_sec": (
            evidence_before.get("fs_write_bytes_per_sec")
        ),

        "fs_usage_to_limit_ratio": (
            evidence_before.get("fs_usage_to_limit_ratio")
        ),
    }


def _clean_none_values(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _first_non_null(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _evidence_section(exp: Dict[str, Any], section: str) -> Dict[str, Any]:
    """
    Return compact stored evidence for a phase: before / after.
    Supports both the new unified experience format and older legacy formats.
    """
    return (
        _get(exp, ["evidence", section], {})
        or _get(exp, [f"evidence_{section}"], {})
        or _get(exp, ["incident", f"state_{section}", "target_service"], {})
        or {}
    )


def _outcome_feedback(exp: Dict[str, Any]) -> Dict[str, Any]:
    outcome = _flat_outcome(exp)
    if isinstance(outcome.get("feedback"), dict):
        merged = dict(outcome["feedback"])
        merged.update({k: v for k, v in outcome.items() if k != "feedback"})
        return merged
    return outcome


def _resource_snapshot_from_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract planner-relevant before/after values.
    Keep names explicit so the prompt is readable and retrieval-friendly.
    """
    if not isinstance(evidence, dict):
        evidence = {}

    return _clean_none_values({
        "cpu_limit_per_pod_millicores": _first_non_null(
            evidence.get("cpu_limit_per_pod_millicores"),
            evidence.get("target_cpu_limit_per_pod_millicores"),
            evidence.get("cpu_limit_millicores"),
            evidence.get("cpu_limit"),
        ),
        "memory_limit_per_pod_mib": _first_non_null(
            evidence.get("memory_limit_per_pod_mib"),
            evidence.get("target_memory_limit_per_pod_mib"),
            evidence.get("memory_limit_mib"),
            evidence.get("memory_limit"),
        ),
        "replicas_desired": _first_non_null(
            evidence.get("replicas_desired"),
            evidence.get("target_replicas_desired"),
            evidence.get("deployment_replicas_desired"),
        ),
        "replicas_ready": _first_non_null(
            evidence.get("replicas_ready"),
            evidence.get("target_replicas_ready"),
            evidence.get("deployment_replicas_ready"),
        ),
        "replicas_available": _first_non_null(
            evidence.get("replicas_available"),
            evidence.get("target_replicas_available"),
            evidence.get("deployment_replicas_available"),
        ),
        "cpu_usage_to_limit_pct_p95": _first_non_null(
            evidence.get("cpu_usage_to_limit_pct_p95"),
            evidence.get("cpu_pct"),
            evidence.get("cpu_usage_pct"),
        ),
        "memory_usage_to_limit_pct_p95": _first_non_null(
            evidence.get("memory_usage_to_limit_pct_p95"),
            evidence.get("memory_pct"),
            evidence.get("memory_usage_pct"),
        ),
        "latency_p95_ms": _first_non_null(
            evidence.get("latency_p95_ms"),
            evidence.get("latency_p95"),
        ),
        "error_rate": _first_non_null(
            evidence.get("error_rate"),
            evidence.get("error_5xx"),
            evidence.get("error_rate_5xx"),
        ),
        "pod_restarts": _first_non_null(
            evidence.get("pod_restarts"),
            evidence.get("restart_count"),
        ),
        "oom_kills": evidence.get("oom_kills"),
        "fs_read_bytes_per_sec": evidence.get("fs_read_bytes_per_sec"),
        "fs_write_bytes_per_sec": evidence.get("fs_write_bytes_per_sec"),
        "fs_usage_to_limit_ratio": evidence.get("fs_usage_to_limit_ratio"),
    })


def _resource_changes(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute readable before→after deltas for resources and replicas.
    Values can be numeric or strings; numeric deltas are added when possible.
    """
    keys = [
        "cpu_limit_per_pod_millicores",
        "memory_limit_per_pod_mib",
        "replicas_desired",
        "replicas_ready",
        "replicas_available",
        "cpu_usage_to_limit_pct_p95",
        "memory_usage_to_limit_pct_p95",
        "latency_p95_ms",
        "error_rate",
        "pod_restarts",
        "oom_kills",
        "fs_read_bytes_per_sec",
        "fs_write_bytes_per_sec",
        "fs_usage_to_limit_ratio",
    ]

    out: Dict[str, Any] = {}

    for key in keys:
        b = before.get(key)
        a = after.get(key)

        if b is None and a is None:
            continue

        item = {"before": b, "after": a}

        # bf = _to_float(b)
        # af = _to_float(a)
        # if bf is not None and af is not None:
        #     item["delta"] = round(af - bf, 6)

        out[key] = item

    return out


def _normalized_action_from_exp(
    *,
    exp: Dict[str, Any],
    plan: Dict[str, Any],
    outcome: Dict[str, Any],
    keys: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Prefer feedback.normalized_action from compute_feedback_fixed.py.
    Fall back to older plan_action/action_type/target_changes fields.
    """
    normalized = (
        outcome.get("normalized_action")
        or exp.get("normalized_action")
        or {}
    )

    if isinstance(normalized, dict) and normalized:
        return _clean_none_values({
            "action_type": normalized.get("action_type") or normalized.get("type"),
            "target": normalized.get("target") or normalized.get("deployment") or keys.get("service"),
            "value": normalized.get("value") or normalized.get("target_value"),
        })

    target_changes = plan.get("target_changes") or {}
    return _clean_none_values({
        "action_type": (
            outcome.get("plan_action")
            or outcome.get("action_type")
            or keys.get("action_type")
            or target_changes.get("type")
        ),
        "target": (
            target_changes.get("target")
            or keys.get("deployment")
            or keys.get("service")
        ),
        "value": (
            target_changes.get("target_value")
            or target_changes.get("value")
            or keys.get("target_changes_target_value")
        ),
    })


def _compact_outcome_for_prompt(outcome: Dict[str, Any]) -> Dict[str, Any]:
    return _clean_none_values({
        "FRQ": outcome.get("FRQ"),
        "RQ": outcome.get("RQ"),
        "SHS_before": outcome.get("SHS_before"),
        "SHS_after": outcome.get("SHS_after"),
        "delta_SHS": outcome.get("delta_SHS"),
        "PS": outcome.get("PS"),
        "ES": outcome.get("ES"),
        "reward": outcome.get("reward"),
        "resource_cost": outcome.get("resource_cost"),
        "fault_aware_resource_penalty": outcome.get("fault_aware_resource_penalty"),
        "primary_metric_fixed": outcome.get("primary_metric_fixed"),
        "improved_metrics": outcome.get("improved_metrics"),
        "degraded_metrics": outcome.get("degraded_metrics"),
        "recovery_success": outcome.get("recovery_success"),
        "regression": outcome.get("regression"),
        "fault_success_reason": outcome.get("fault_success_reason"),
    })


def _symptom_delta_for_prompt(outcome: Dict[str, Any]) -> Dict[str, Any]:
    """
    Preserve the fault-aware symptom explanation generated by feedback.
    """
    deltas = (
        outcome.get("symptom_deltas")
        or outcome.get("symptoms_delta")
        or outcome.get("relative_improvement_components")
        or {}
    )
    return deltas if isinstance(deltas, dict) else {}



def _planner_example_from_exp(exp: Dict[str, Any], idx: int) -> Dict[str, Any]:
    incident = exp.get("incident", {}) or {}
    keys = exp.get("retrieval_keys", {}) or {}
    plan = exp.get("plan", {}) or exp.get("mitigation_plan", {}) or {}
    remediation = exp.get("remediation", {}) or {}
    outcome = _outcome_feedback(exp)

    raw_plan = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}

    target_changes = (
        plan.get("target_changes")
        or raw_plan.get("target_changes")
        or {
            "type": keys.get("target_changes_type"),
            "previous_value": keys.get("target_changes_previous_value"),
            "target_value": keys.get("target_changes_target_value"),
        }
    )

    actions = (
        plan.get("actions")
        or raw_plan.get("actions")
        or []
    )

    evidence_before = _resource_snapshot_from_evidence(_evidence_section(exp, "before"))
    evidence_after = _resource_snapshot_from_evidence(_evidence_section(exp, "after"))

    # Legacy fallback: if explicit evidence is absent, still include whatever
    # the old target metric extractor can recover as "before".
    if not evidence_before:
        evidence_before = _clean_none_values(_target_metrics_from_exp(exp))

    normalized_action = _normalized_action_from_exp(
        exp=exp,
        plan=plan,
        outcome=outcome,
        keys=keys,
    )

    return _clean_none_values({
                            "example": idx,
                            "fault_type": keys.get("fault_type") or incident.get("fault_type") or incident.get("fault"),
                            "service": keys.get("service") or incident.get("service") or incident.get("target_service"),

                            "plan": plan,
                            "changes": _resource_changes(evidence_before, evidence_after),

                            "outcome": {
                                # "reward": outcome.get("reward"),
                                # "resource_cost": outcome.get("resource_penalty"),
                            },
                        })

def _safe_json(obj: Any) -> Any:
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
    
def format_for_prompt(experiences: List[Dict[str, Any]]) -> str:
    if not experiences:
        return "No prior experience available."

    examples = [
        _planner_example_from_exp(exp, i)
        for i, exp in enumerate(experiences, 1)
    ]

    return "\n".join(
        json.dumps(_safe_json(example), separators=(",", ":"), sort_keys=False)
        for example in examples
    )