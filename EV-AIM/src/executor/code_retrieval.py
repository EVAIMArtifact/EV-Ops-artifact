import json
import math
from pathlib import Path
from typing import Dict, Any, List, Optional
import random

TOP_K = 3

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


def _get(d: Dict[str, Any], path: List[str], default=None):
    cur = d or {}
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _f(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def _outcome(e: Dict[str, Any]) -> Dict[str, Any]:
    outcome = e.get("outcome", {}) or e.get("feedback", {}) or {}

    if isinstance(outcome.get("feedback"), dict):
        merged = dict(outcome.get("feedback") or {})
        for k, v in outcome.items():
            if k != "feedback" and v is not None:
                merged[k] = v
        return merged

    return outcome


def _keys(e: Dict[str, Any]) -> Dict[str, Any]:
    return e.get("retrieval_keys", {}) or {}


def _incident_value(e: Dict[str, Any], name: str):
    keys = _keys(e)
    incident = e.get("incident", {}) or {}

    if name == "fault_type":
        return keys.get("fault_type") or incident.get("fault_type") or incident.get("fault")

    if name == "service":
        return keys.get("service") or incident.get("service") or incident.get("target_service")

    if name == "deployment":
        return keys.get("deployment") or incident.get("deployment") or incident.get("target_deployment")

    if name == "namespace":
        return keys.get("namespace") or incident.get("namespace")

    if name == "app":
        return keys.get("app") or incident.get("app")

    return keys.get(name) or incident.get(name)


def _plan_dict(e: Dict[str, Any]) -> Dict[str, Any]:
    plan = e.get("plan", {}) or {}
    raw = plan.get("raw") if isinstance(plan.get("raw"), dict) else {}
    merged = dict(raw)
    merged.update({k: v for k, v in plan.items() if k != "raw"})
    return merged


def _plan_strategy(e: Dict[str, Any]) -> Optional[str]:
    plan = _plan_dict(e)
    return plan.get("strategy")


def _target_changes(e: Dict[str, Any]) -> Dict[str, Any]:
    keys = _keys(e)
    plan = _plan_dict(e)

    return (
        plan.get("target_changes")
        or {
            "type": keys.get("target_changes_type"),
            "previous_value": keys.get("target_changes_previous_value"),
            "target_value": keys.get("target_changes_target_value"),
        }
    )


def _normalized_action_from_exp(e: Dict[str, Any]) -> Dict[str, Any]:
    outcome = _outcome(e)
    plan = _plan_dict(e)
    target_changes = _target_changes(e)

    normalized = (
        outcome.get("normalized_action")
        or plan.get("normalized_action")
        or {}
    )

    if isinstance(normalized, dict) and normalized.get("action_type"):
        return normalized

    action_type = (
        outcome.get("plan_action")
        or plan.get("action_type")
        or plan.get("plan_action")
        or target_changes.get("type")
        or "unknown"
    )

    # Map planner target_changes.type to canonical executor action type.
    if action_type == "memory_limit":
        action_type = "scale_up_memory"
    elif action_type == "cpu_limit":
        action_type = "scale_up_cpu"
    elif action_type == "replicas":
        action_type = "scale_out"
    elif action_type == "image":
        action_type = "rollback"
    elif action_type == "none":
        action_type = "none"

    return {
        "action_type": action_type,
        "target": (
            normalized.get("target")
            if isinstance(normalized, dict)
            else None
        ) or _incident_value(e, "deployment") or _incident_value(e, "service"),
        "value": (
            normalized.get("value")
            if isinstance(normalized, dict)
            else None
        ) or target_changes.get("target_value"),
    }


def _current_action_type(plan: Dict[str, Any]) -> str:
    if not isinstance(plan, dict):
        return "unknown"

    normalized = plan.get("normalized_action") or {}
    if isinstance(normalized, dict) and normalized.get("action_type"):
        return str(normalized["action_type"])

    if plan.get("action_type"):
        return str(plan["action_type"])

    change_type = _get(plan, ["target_changes", "type"], "unknown")
    if change_type == "memory_limit":
        return "scale_up_memory"
    if change_type == "cpu_limit":
        return "scale_up_cpu"
    if change_type == "replicas":
        return "scale_out"
    if change_type == "image":
        return "rollback"
    if change_type == "config":
        return "config_fix"
    if change_type == "none":
        return "none"

    return str(change_type or "unknown")

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
    if ft in {"disk_stress", "disk_pressure", "disk_io", "disk_io_pressure"}:
        return "disk_stress"

    return ft

def _target_changes_match(current_plan: Dict[str, Any], exp: Dict[str, Any]) -> bool:
    current = current_plan.get("target_changes") or {}
    past = _target_changes(exp)

    if not isinstance(current, dict) or not isinstance(past, dict):
        return False

    if current.get("type") and current.get("type") == past.get("type"):
        return True

    return _current_action_type(current_plan) == _normalized_action_from_exp(exp).get("action_type")


def _strategy_match(current: str, past: str) -> bool:
    current = str(current or "").lower()
    past = str(past or "").lower()

    if not current or not past:
        return False

    current_tokens = set(current.replace(".", "").replace("_", " ").split())
    past_tokens = set(past.replace(".", "").replace("_", " ").split())

    if not current_tokens or not past_tokens:
        return False

    overlap = len(current_tokens & past_tokens) / len(current_tokens | past_tokens)
    return overlap >= 0.35

def _flat_outcome(exp: Dict[str, Any]) -> Dict[str, Any]:
    outcome = exp.get("outcome", {}) or exp.get("feedback", {}) or {}

    if isinstance(outcome.get("feedback"), dict):
        merged = dict(outcome.get("feedback") or {})
        for k, v in outcome.items():
            if k != "feedback" and v is not None:
                merged[k] = v
        return merged

    return outcome

def clamp01(v):
    try:
        if v is None:
            return 0.0
        return max(0.0, min(float(v), 1.0))
    except Exception:
        return 0.0
    

def _quality(e: Dict[str, Any]) -> float:
    """
    Executor ICL should prefer examples that:
    - executed safely, ES=1
    - fixed the primary fault symptom, high FRQ
    - used a matching normalized action
    - avoided regression
    Generic SHS/reward remain secondary.
    """
    outcome = _flat_outcome(e)

    frq = clamp01(outcome.get("FRQ"))
    reward_raw = _f(outcome.get("reward"), 0.0)
    reward01 = (max(-1.0, min(1.0, reward_raw)) + 1.0) / 2.0
    es = clamp01(outcome.get("ES"))

    resource_penalty = clamp01(outcome.get("resource_penalty"))
    degradation_penalty = clamp01(outcome.get("degradation_penalty"))

    score = (0.25 * es
    + 0.75 * reward01
)

    return round(max(0.0, min(score, 1.0)), 4)


def _strip_embeddings(entry: Dict[str, Any]) -> Dict[str, Any]:
    clean = dict(entry)
    clean.pop("intent_embedding", None)
    clean.pop("strategy_embedding", None)
    return clean


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



def retrieve_icl_examples(
    namespace: str,
    service: str,
    fault_type: str,
    plan: Dict[str, Any],
    retrieval_mode: str = "evaim",
) -> List[Dict[str, Any]]:
    
    kb = _load_code_kb(namespace, fault_type)
    if not kb:
        return []
    
    if retrieval_mode == "random":
        return random.sample(kb, min(TOP_K, len(kb)))

    current_strategy = plan.get("strategy")
    current_action_type = _current_action_type(plan)

    candidates = []

    for e in kb:
        remediation = e.get("remediation", {}) or {}
        MIN_REWARD = 0.45

        outcome = _flat_outcome(e)
        raw_reward = _to_float(outcome.get("reward")) or 0.0

        # if raw_reward < MIN_REWARD:
        #     continue

        e_fault = _incident_value(e, "fault_type")
        e_service = _incident_value(e, "service")
        e_strategy = _plan_strategy(e)
        e_playbook = remediation.get("playbook_yaml")

        if not e_playbook:
            continue

        normalized_action = _normalized_action_from_exp(e)
        e_action_type = normalized_action.get("action_type")

        same_fault = canonical_fault_type(e_fault) == canonical_fault_type(fault_type)
        same_service = e_service == service
        same_strategy = _strategy_match(current_strategy, e_strategy)
        same_action = current_action_type == e_action_type
        same_change_type = _target_changes_match(plan, e)

        recovery_success = outcome.get("recovery_success") is True
        regression = outcome.get("regression") is True
        primary_fixed = outcome.get("primary_metric_fixed") is True
        es = _f(outcome.get("ES"))
        frq = _f(outcome.get("FRQ"))


        candidates.append({
            "entry": _strip_embeddings(e),
            "quality": _quality(e),
            "same_fault": same_fault,
            "same_service": same_service,
            "same_strategy": same_strategy,
            "same_action": same_action,
            "same_change_type": same_change_type,
            "recovery_success": recovery_success,
            "primary_fixed": primary_fixed,
            "regression": regression,
            "ES": es,
            "FRQ": frq,
        })

    tiers = [
        # Best executor examples: same fault/service/action and known safe execution.
        lambda c: c["same_fault"] and c["same_service"] and c["quality"] >= 0.45,
        lambda c: c["same_fault"] and c["quality"] >= 0.45,
        lambda c: c["quality"] >= 0.45,
    ]

    selected = []
    seen = set()

    for tier in tiers:
        items = [c for c in candidates if tier(c)]
        items = sorted(
            items,
            key=lambda c: (c["quality"], c["FRQ"], c["ES"]),
            reverse=True,
        )

        for c in items:
            e = c["entry"]
            remediation = e.get("remediation", {}) or {}
            normalized_action = _normalized_action_from_exp(e)

            key = json.dumps({
                "fault_type": _incident_value(e, "fault_type"),
                "service": _incident_value(e, "service"),
                "action_type": normalized_action.get("action_type"),
                "target_changes": _target_changes(e),
                "playbook_yaml": remediation.get("playbook_yaml", "")[:180],
            }, sort_keys=True)

            if key in seen:
                continue

            selected.append(e)
            seen.add(key)

            if len(selected) >= TOP_K:
                return selected

    return selected[:TOP_K]
