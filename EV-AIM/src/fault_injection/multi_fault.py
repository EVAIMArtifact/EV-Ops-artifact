from typing import Any, Dict, List, Optional
import copy
import time

from src.fault_injection.fault_inject import inject_fault, recover_fault, APP_TO_NAMESPACE


def normalize_multifault_config(exp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Converts either:
      - old single-fault config
      - new multi-fault config with exp["faults"]
    into a list of fault dicts.
    """
    if "faults" in exp and isinstance(exp["faults"], list):
        raw_faults = exp["faults"]
    else:
        raw_faults = [exp]

    faults = []

    for i, f in enumerate(raw_faults, start=1):
        fault = copy.deepcopy(f)

        fault.setdefault("id", f"f{i}")
        fault.setdefault("app", exp.get("app"))
        fault.setdefault("namespace", exp.get("namespace") or APP_TO_NAMESPACE.get(exp.get("app")))
        fault.setdefault("duration", exp.get("duration"))
        fault.setdefault("users", exp.get("users"))
        fault.setdefault("spawn_rate", exp.get("spawn_rate"))

        if "deployment" not in fault and "service" in fault:
            fault["deployment"] = fault["service"]

        faults.append(fault)

    return faults


def inject_faults(exp: Dict[str, Any]) -> Dict[str, Any]:
    faults = normalize_multifault_config(exp)

    results = []
    errors = []

    for fault in faults:
        try:
            print(f"[MULTI-FAULT] Injecting {fault['id']}: {fault['type']} on {fault.get('service')}")
            result = inject_fault(fault)
            result["fault_id"] = fault["id"]
            results.append({
                "fault": fault,
                "result": result,
                "status": "injected",
            })
        except Exception as e:
            errors.append({
                "fault": fault,
                "status": "failed",
                "error": f"{type(e).__name__}: {e}",
            })
            break

    return {
        "mode": "multi_fault" if len(faults) > 1 else "single_fault",
        "fault_count": len(faults),
        "results": results,
        "errors": errors,
    }


def recover_faults(exp: Dict[str, Any], injection_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not injection_result:
        return {"status": "skipped", "reason": "no_injection_result"}

    recovery = {
        "status": "started",
        "recovered": [],
        "errors": [],
    }

    # Reverse order is safer: last injected, first recovered.
    for item in reversed(injection_result.get("results", [])):
        fault = item.get("fault")
        result = item.get("result")

        try:
            print(f"[MULTI-FAULT] Recovering {fault.get('id')}: {fault.get('type')}")
            recovery_result = recover_fault(fault, result)
            recovery["recovered"].append({
                "fault_id": fault.get("id"),
                "fault_type": fault.get("type"),
                "result": recovery_result,
            })
        except Exception as e:
            recovery["errors"].append({
                "fault_id": fault.get("id"),
                "fault_type": fault.get("type"),
                "error": f"{type(e).__name__}: {e}",
            })

    recovery["status"] = "completed" if not recovery["errors"] else "completed_with_errors"
    return recovery