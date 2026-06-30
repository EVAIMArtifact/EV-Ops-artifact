import os
import time
import json
import subprocess
from typing import Optional, Dict, Any

import requests
from requests.auth import HTTPBasicAuth

from src.fault_injection.chaos_faults import (
    CHAOS_HANDLERS,
    CHAOS_RECOVER_TYPES,
    recover_chaos_fault,
)


BASE_URL = os.environ["BASE_URL"].rstrip("/")
AUTH = HTTPBasicAuth(
    os.environ["BASIC_AUTH_USER"],
    os.environ["BASIC_AUTH_PASSWORD"],
)


# Keep in sync with src/monitoring/config.py: sock-shop / online-boutique map to
# the instrumented "2" namespaces (the legacy ones emit no spanmetrics, so RED
# metrics + SHS would be invalid). An explicit fault["namespace"] still wins.
APP_TO_NAMESPACE = {
    "robot-shop": "robot-shop",
    "sock-shop": "sock-shop",
    "online-boutique": "online-boutique",
}

# Category A: load-driven faults (a Locust spike IS the fault).
CATEGORY_A_FAULTS = {"load_spike", "db_overload"}

# Category B: infrastructure faults. A low background load is started so RED
# metrics register the impact. cpu_hog/mem_stress/net_*/pod_kill/disk_stress/
# config_error/cpu_throttle/dependency_failure are ported from rca-service;
# bad_image/stuck_deployment are EV-native.
CATEGORY_B_FAULTS = {
    "cpu_hog",
    "mem_stress",
    "net_delay",
    "net_loss",
    "pod_kill",
    "disk_stress",
    "config_error",
    "cpu_throttle",
    "dependency_failure",
    "bad_image",
    "stuck_deployment",
}

def is_category_a_fault(fault: Dict[str, Any]) -> bool:
    return fault["type"] in CATEGORY_A_FAULTS


def is_category_b_fault(fault: Dict[str, Any]) -> bool:
    return fault["type"] in CATEGORY_B_FAULTS

DEFAULT_LOW_TRAFFIC_USERS = 20
DEFAULT_LOW_TRAFFIC_SPAWN_RATE = 2


LOCUST_URL_PATTERNS = {
    "robot-shop": "{base}/{app}/locust/{action}",
    "sock-shop": "{base}/locust/{app}/{action}",
    "online-boutique": "{base}/locust/{app}/{action}",
}

def get_locust_url(app: str, action: str) -> str:
    pattern = LOCUST_URL_PATTERNS.get(
        app,
        "{base}/locust/{app}/{action}"  # default
    )

    return pattern.format(
        base=BASE_URL,
        app=app,
        action=action,
    )

def should_start_low_traffic(fault: Dict[str, Any]) -> bool:
    return (
        fault["type"] in CATEGORY_B_FAULTS
        and fault.get("users") is not None
        and fault.get("spawn_rate") is not None
    )


def start_low_traffic_for_category_b(fault: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not should_start_low_traffic(fault):
        return None

    app = fault["app"]
    users = int(fault.get("users", DEFAULT_LOW_TRAFFIC_USERS))
    spawn_rate = int(fault.get("spawn_rate", DEFAULT_LOW_TRAFFIC_SPAWN_RATE))

    print(
        f"[LOAD] Starting low traffic for Category B fault: "
        f"app={app}, users={users}, spawn_rate={spawn_rate}"
    )

    start_load(app, users, spawn_rate)

    return {
        "app": app,
        "users": users,
        "spawn_rate": spawn_rate,
        "traffic_type": "low_background_traffic",
    }


def run_cmd(cmd: list[str], check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl"] + cmd,
        capture_output=True,
        text=True,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed: kubectl {' '.join(cmd)}\n{result.stderr}"
        )

    return result.stdout.strip()


def start_load(app: str, users: int = 50, spawn_rate: int = 5) -> str:
    url = get_locust_url(app, "swarm")

    data = {
        "user_count": users,
        "spawn_rate": spawn_rate,
    }

    r = requests.post(url, auth=AUTH, data=data, timeout=15)
    r.raise_for_status()
    return r.text


def stop_load(app: str) -> str:
    url = get_locust_url(app, "stop")

    r = requests.get(url, auth=AUTH, timeout=15)
    r.raise_for_status()
    return r.text


def reset_load_stats(app: str) -> str:
    url = get_locust_url(app, "stats/reset")

    r = requests.get(url, auth=AUTH, timeout=15)
    r.raise_for_status()
    return r.text


def get_pods(namespace: str, service: Optional[str] = None) -> list[str]:
    out = run_cmd([
        "get", "pods",
        "-n", namespace,
        "-o", "json",
    ])

    data = json.loads(out)
    pods = [item["metadata"]["name"] for item in data["items"]]

    if service:
        pods = [p for p in pods if p.startswith(service)]

    return pods


def get_first_pod(namespace: str, service: str) -> str:
    pods = get_pods(namespace, service)
    if not pods:
        raise ValueError(f"No pod found for service={service} in namespace={namespace}")
    return pods[0]


def get_deployment_replicas(namespace: str, deployment: str) -> int:
    out = run_cmd([
        "get", "deployment", deployment,
        "-n", namespace,
        "-o", "jsonpath={.spec.replicas}",
    ])

    return int(out) if out else 1


def wait_rollout(namespace: str, deployment: str, timeout: int = 180) -> str:
    return run_cmd([
        "rollout", "status",
        f"deployment/{deployment}",
        "-n", namespace,
        f"--timeout={timeout}s",
    ])


def scale_deployment(namespace: str, deployment: str, replicas: int) -> str:
    print(f"[K8S] Scaling {namespace}/{deployment} to {replicas}")
    return run_cmd([
        "scale", "deployment", deployment,
        f"--replicas={replicas}",
        "-n", namespace,
    ])


def restart_deployment(namespace: str, deployment: str) -> str:
    print(f"[K8S] Restarting {namespace}/{deployment}")
    return run_cmd([
        "rollout", "restart",
        f"deployment/{deployment}",
        "-n", namespace,
    ])


def rollback_deployment(namespace: str, deployment: str) -> str:
    print(f"[K8S] Rolling back {namespace}/{deployment}")
    return run_cmd([
        "rollout", "undo",
        f"deployment/{deployment}",
        "-n", namespace,
    ])


# ---------------------------------------------------------
# Baseline snapshot and full state restore
# ---------------------------------------------------------
def get_json(kind: str, namespace: str, name: Optional[str] = None) -> Dict[str, Any]:
    cmd = ["get", kind]
    if name:
        cmd.append(name)
    cmd += ["-n", namespace, "-o", "json"]
    out = run_cmd(cmd)
    return json.loads(out)


def _remove_server_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep a Kubernetes object safe for kubectl apply.
    This removes fields owned by the apiserver while preserving spec/template.
    """
    obj = json.loads(json.dumps(obj))

    metadata = obj.get("metadata", {})
    for key in [
        "uid",
        "resourceVersion",
        "generation",
        "creationTimestamp",
        "managedFields",
        "selfLink",
    ]:
        metadata.pop(key, None)

    metadata.pop("annotations", None) if metadata.get("annotations") == {} else None
    obj.pop("status", None)
    return obj


def _kubectl_apply_object(obj: Dict[str, Any]) -> str:
    import tempfile

    safe_obj = _remove_server_fields(obj)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(safe_obj, f)
        tmp_path = f.name

    try:
        return run_cmd(["apply", "-f", tmp_path])
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def capture_recovery_baseline(fault: Dict[str, Any]) -> Dict[str, Any]:
    """
    Capture the cluster state that must be restored after the experiment.

    This is intentionally done before fault injection, so it captures the true
    pre-fault and pre-remediation state. Both EV-AIM and rule-based runners can
    rely on recover_fault() to restore this state later.
    """
    namespace = (
        fault.get("namespace")
        or APP_TO_NAMESPACE.get(fault.get("app", ""))
        or fault.get("target_namespace")
    )
    service = fault.get("service")
    deployment = fault.get("deployment") or service

    baseline: Dict[str, Any] = {
        "namespace": namespace,
        "target_deployment": deployment,
        "deployments": {},
        "hpas": {},
    }

    if not namespace:
        baseline["error"] = "Cannot capture baseline without namespace"
        return baseline

    # Capture all deployments in the namespace, not only the target deployment.
    # This protects experiments where the planner/executor changes a dependency
    # deployment instead of the directly affected service.
    try:
        deployments = get_json("deployments", namespace)
        for item in deployments.get("items", []):
            name = item.get("metadata", {}).get("name")
            if name:
                baseline["deployments"][name] = item
    except Exception as e:
        baseline["deployment_capture_error"] = f"{type(e).__name__}: {e}"

    # Capture HPAs so new HPAs created by remediation can be removed.
    try:
        hpas = get_json("hpa", namespace)
        for item in hpas.get("items", []):
            name = item.get("metadata", {}).get("name")
            if name:
                baseline["hpas"][name] = item
    except Exception as e:
        # Some apps/namespaces may not have HPA API objects.
        baseline["hpa_capture_error"] = f"{type(e).__name__}: {e}"

    return baseline


def restore_recovery_baseline(
    baseline: Optional[Dict[str, Any]],
    rollout_timeout: int = 180,
) -> Dict[str, Any]:
    """
    Restore deployment specs, replica counts, images, resource limits, paused state,
    template annotations, and remove HPAs created during remediation.

    This runs after the fault-specific cleanup. It is the common final reset step
    for both rule-based and EV-AIM experiments.
    """
    result: Dict[str, Any] = {
        "status": "started",
        "restored_deployments": {},
        "deleted_new_hpas": {},
        "restored_hpas": {},
    }

    if not baseline:
        result["status"] = "skipped"
        result["reason"] = "no_baseline_provided"
        return result

    namespace = baseline.get("namespace")
    if not namespace:
        result["status"] = "error"
        result["reason"] = "baseline_missing_namespace"
        return result

    original_deployments = baseline.get("deployments", {}) or {}
    original_hpas = baseline.get("hpas", {}) or {}

    # Delete HPAs that did not exist before. This avoids autoscalers keeping
    # changed replica counts after a run.
    try:
        current_hpas = get_json("hpa", namespace).get("items", [])
        original_hpa_names = set(original_hpas.keys())

        for hpa in current_hpas:
            name = hpa.get("metadata", {}).get("name")
            if name and name not in original_hpa_names:
                try:
                    result["deleted_new_hpas"][name] = run_cmd([
                        "delete", "hpa", name,
                        "-n", namespace,
                        "--ignore-not-found=true",
                    ])
                except Exception as e:
                    result["deleted_new_hpas"][name] = f"{type(e).__name__}: {e}"
    except Exception as e:
        result["delete_new_hpa_error"] = f"{type(e).__name__}: {e}"

    # Restore original HPA specs if they existed and were changed.
    for name, hpa_obj in original_hpas.items():
        try:
            result["restored_hpas"][name] = _kubectl_apply_object(hpa_obj)
        except Exception as e:
            result["restored_hpas"][name] = f"{type(e).__name__}: {e}"

    # Restore deployment specs. This puts replicas, images, resource limits,
    # rollout pause state, env vars, command args, and pod template annotations
    # back to exactly what they were before the experiment.
    for name, dep_obj in original_deployments.items():
        try:
            result["restored_deployments"][name] = _kubectl_apply_object(dep_obj)
            try:
                result["restored_deployments"][f"{name}__rollout"] = wait_rollout(
                    namespace, name, timeout=rollout_timeout
                )
            except Exception as rollout_error:
                result["restored_deployments"][f"{name}__rollout_error"] = (
                    f"{type(rollout_error).__name__}: {rollout_error}"
                )
        except Exception as e:
            result["restored_deployments"][name] = f"{type(e).__name__}: {e}"

    result["status"] = "completed"
    return result


# ---------------------------------------------------------
# Fault 1: workload load spike
# ---------------------------------------------------------

def inject_load_spike(fault: Dict[str, Any]) -> Dict[str, Any]:
    app = fault["app"]
    users = int(fault.get("users", 200))
    spawn_rate = int(fault.get("spawn_rate", 20))

    start_load(app, users, spawn_rate)

    return {
        "fault_type": "load_spike",
        "app": app,
        "users": users,
        "spawn_rate": spawn_rate,
        "recovery": "stop_load",
    }


# ---------------------------------------------------------
# Pod failure, dependency failure, and the CPU/memory/network/disk/config/
# cpu-throttle faults are ported from rca-service; see chaos_faults.py.
# ---------------------------------------------------------


# ---------------------------------------------------------
# bad image / config-like rollout fault (EV-native)
# ---------------------------------------------------------

def inject_bad_image(fault: Dict[str, Any]) -> Dict[str, Any]:
    namespace = fault["namespace"]
    deployment = fault["deployment"]
    container = fault.get("container", deployment)
    bad_image = fault.get("bad_image", "invalid-image:latest")

    original_image = run_cmd([
        "get", "deployment", deployment,
        "-n", namespace,
        "-o",
        f"jsonpath={{.spec.template.spec.containers[?(@.name=='{container}')].image}}",
    ])

    print(f"[FAULT] Setting bad image for {namespace}/{deployment}: {container}={bad_image}")

    run_cmd([
        "set", "image",
        f"deployment/{deployment}",
        f"{container}={bad_image}",
        "-n", namespace,
    ])

    return {
        "fault_type": "bad_image",
        "namespace": namespace,
        "deployment": deployment,
        "container": container,
        "original_image": original_image,
        "bad_image": bad_image,
        "recovery": "rollout_undo_or_restore_image",
    }


# ---------------------------------------------------------
# Fault 5: stuck deployment
# ---------------------------------------------------------

def inject_stuck_deployment(fault: Dict[str, Any]) -> Dict[str, Any]:
    namespace = fault["namespace"]
    deployment = fault["deployment"]

    print(f"[FAULT] Pausing rollout for {namespace}/{deployment}")

    run_cmd([
        "rollout", "pause",
        f"deployment/{deployment}",
        "-n", namespace,
    ])

    return {
        "fault_type": "stuck_deployment",
        "namespace": namespace,
        "deployment": deployment,
        "recovery": "rollout_resume_or_undo",
    }


# ---------------------------------------------------------
# Fault 6: DB overload
# ---------------------------------------------------------

def inject_db_overload(fault: Dict[str, Any]) -> Dict[str, Any]:
    namespace = fault["namespace"]
    service = fault["service"]
    duration = int(fault.get("duration", 300))
    cpu_cores = int(fault.get("cpu_cores", 1))

    pod = fault.get("pod") or get_first_pod(namespace, service)

    print(f"[FAULT] Stressing DB pod {namespace}/{pod} with cpu_cores={cpu_cores}")

    # Spawn `yes` workers, record THEIR PIDs, sleep, then kill those exact PIDs.
    # We must NOT use `pkill` (datastore images like mysql lack procps -> pkill
    # missing -> the old code's `pkill yes` failed, `yes` never died, and the
    # backgrounded process held the kubectl-exec session open forever). Killing
    # by recorded PID works without procps and lets the fault self-terminate.
    # Recovery cannot signal the stressors: in this cluster `kill` from a LATER
    # kubectl-exec returns EPERM even as root (cross-exec PID isolation), the same
    # reason disk_stress uses a sentinel file. But a process CAN kill its own
    # children within the SAME session. So: the launcher spawns `yes` workers and
    # runs a watchdog that waits for a STOP file (or `duration`) then kills its
    # own children. Recovery just touches STOP (cross-exec file writes DO work).
    stop_file = "/tmp/evaim_db_overload.stop"
    pids_file = "/tmp/evaim_db_overload.pids"
    # Single-quoted below, so keep NO single quotes in here (use `pids=`).
    inner = (
        f"rm -f {stop_file}; pids=; "
        f"for i in $(seq 1 {cpu_cores}); do yes > /dev/null 2>&1 & pids=\"$pids $!\"; done; "
        f"echo \"$pids\" > {pids_file}; "
        "S=$(date +%s); "
        f"while [ ! -e {stop_file} ] && [ $(( $(date +%s) - S )) -lt {duration} ]; do sleep 1; done; "
        "kill $pids 2>/dev/null; "
        f"rm -f {pids_file} {stop_file}"
    )
    # Detached (nohup + background + redirect all fds) so exec returns at once.
    # `echo $!` (outside the single quotes) returns the launcher PID.
    background_cmd = (
        f"nohup sh -c '{inner}' "
        ">/tmp/evaim_db_overload.log 2>&1 & echo $!"
    )

    launcher_pid = run_cmd([
        "exec", pod,
        "-n", namespace,
        "--",
        "sh", "-c",
        background_cmd,
    ], check=True)

    print(f"[FAULT] DB overload started: launcher_pid={launcher_pid}")

    return {
        "fault_type": "db_overload",
        "namespace": namespace,
        "service": service,
        "pod": pod,
        "duration": duration,
        "cpu_cores": cpu_cores,
        "pids_file": pids_file,
        "stop_file": stop_file,
        "launcher_pid": launcher_pid,
        "recovery": "touch_stop_file_or_restart_db",
    }


def inject_fault(fault: Dict[str, Any]) -> Dict[str, Any]:
    if "namespace" not in fault and "app" in fault:
        fault["namespace"] = APP_TO_NAMESPACE.get(fault["app"])

    # Capture baseline BEFORE injecting the fault. This baseline is later used by
    # recover_fault() to undo both the injected fault and any remediation side
    # effects produced by rule-based or EV-AIM execution.
    recovery_baseline = capture_recovery_baseline(fault)

    # EV-native faults + faults ported from rca-service (CHAOS_HANDLERS:
    # cpu_hog, mem_stress, net_delay, net_loss, pod_kill, disk_stress,
    # config_error, cpu_throttle, dependency_failure).
    handlers = {
        "load_spike": inject_load_spike,
        "bad_image": inject_bad_image,
        "stuck_deployment": inject_stuck_deployment,
        "db_overload": inject_db_overload,
        **CHAOS_HANDLERS,
    }

    fault_type = fault["type"]

    if fault_type not in handlers:
        raise ValueError(
            f"Unsupported fault type: {fault_type} (have {sorted(handlers)})"
        )

    low_traffic_result = None
    # low_traffic_result = start_low_traffic_for_category_b(fault)

    print(f"[FAULT] Injecting {fault_type}")
    fault_result = handlers[fault_type](fault)

    # Ported handlers return rca-style dicts without fault_type; normalize so
    # downstream (recover_fault, feedback, storage) can rely on it.
    fault_result.setdefault("fault_type", fault_type)
    fault_result["recovery_baseline"] = recovery_baseline

    # if low_traffic_result:
    #     fault_result["background_load"] = low_traffic_result

    return fault_result


def recover_fault(fault: Dict[str, Any], fault_result: Optional[Dict[str, Any]] = None) -> Any:
    """
    Common cleanup entry point for both rule-based and EV-AIM.

    It performs two cleanup layers:
    1. Fault-specific cleanup: stop load, delete stress pod, resume rollout, etc.
    2. Baseline restore: restore deployments/HPAs to the exact state captured
       before fault injection and before remediation.
    """
    fault_type = fault["type"]
    fault_result = fault_result or {}

    namespace = fault.get("namespace") or fault_result.get("namespace")
    deployment = fault.get("deployment") or fault_result.get("deployment")

    recovery_result: Dict[str, Any] = {
        "fault_cleanup": {},
        "baseline_restore": {},
    }

    # background_load = fault_result.get("background_load")
    # if background_load:
    #     try:
    #         recovery_result["fault_cleanup"]["stop_background_load"] = stop_load(background_load["app"])
    #     except Exception as e:
    #         recovery_result["fault_cleanup"]["stop_background_load_error"] = f"{type(e).__name__}: {e}"

    try:
        if fault_type in CHAOS_RECOVER_TYPES:
            # Faults ported from rca-service: delete the Chaos Mesh CR, scale the
            # dependency back, touch the disk-stress STOP sentinel, restore the
            # env var, or restore the CPU limit/request (per the `recover` key).
            recovery_result["fault_cleanup"]["chaos_recover"] = recover_chaos_fault(fault_result)

        elif fault_type == "load_spike":
            recovery_result["fault_cleanup"]["stop_load"] = stop_load(fault["app"])

        elif fault_type == "db_overload":
            pod = fault_result.get("pod")
            stop_file = fault_result.get("stop_file", "/tmp/evaim_db_overload.stop")
            if pod and namespace:
                # Touch the STOP sentinel; the launcher's watchdog sees it within
                # ~1s and kills its own `yes` children (same-session kill works;
                # a cross-exec kill here would EPERM). File writes cross execs fine.
                recovery_result["fault_cleanup"]["stop_db_overload"] = run_cmd([
                    "exec", pod, "-n", namespace, "--", "sh", "-c",
                    f"touch {stop_file}",
                ], check=False)
            elif deployment and namespace:
                # Fallback: restart the DB deployment to clear stray stressors.
                recovery_result["fault_cleanup"]["restart_deployment"] = restart_deployment(namespace, deployment)
            else:
                recovery_result["fault_cleanup"]["message"] = "db_overload recovery needs a pod or deployment"

        elif fault_type == "bad_image":
            # Rollback is useful for fast recovery, but final correctness comes
            # from baseline restore below.
            recovery_result["fault_cleanup"]["rollback_deployment"] = rollback_deployment(namespace, deployment)

        elif fault_type == "stuck_deployment":
            recovery_result["fault_cleanup"]["resume_rollout"] = run_cmd([
                "rollout", "resume",
                f"deployment/{deployment}",
                "-n", namespace,
            ], check=False)

        else:
            recovery_result["fault_cleanup"]["message"] = f"No specific recovery handler for fault type: {fault_type}"

    except Exception as e:
        # Do not stop here. Even if fault-specific cleanup fails, baseline restore
        # may still be able to reset the system.
        recovery_result["fault_cleanup"]["error"] = f"{type(e).__name__}: {e}"

    baseline = fault_result.get("recovery_baseline") or fault.get("recovery_baseline")
    recovery_result["baseline_restore"] = restore_recovery_baseline(baseline)

    return recovery_result


if __name__ == "__main__":
    test_fault = {
        "type": "pod_kill",
        "app": "robot-shop",
        "namespace": "robot-shop",
        "service": "catalogue",
    }

    print("\n[TEST] Injecting test fault...\n")

    result = inject_fault(test_fault)

    print("\n[RESULT]")
    print(json.dumps(result, indent=2))