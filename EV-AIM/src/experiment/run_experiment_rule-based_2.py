
"""
Rule-based recovery baseline for EV-AIM.

Purpose:
- Run the same fault injection, observation, post-remediation metrics,
  feedback, and unified experience storage as EV-AIM.
- Replace the LLM planner + LLM code generator with a deterministic rule table.

Expected imports are mostly the same as your current experiment runner.
Adjust import paths if your project layout differs.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
import csv
from src.fault_injection import (
    inject_fault,
    recover_fault,
    start_load,
    stop_load,
    is_category_a_fault,
    is_category_b_fault,
)
from src.monitoring.collector import collect_multi_service_observation
from src.monitoring.config import CollectionWindow, ALL_METRIC_GROUPS
from src.planner.build_planner_metrics import build_planner_metrics
from src.executor.rollout_monitor import wait_for_rollout_completion, get_pod_failure_reasons
from src.feedback.compute_feedback import compute_feedback
from src.feedback.knowledge_store import store_unified_experience
from src.utils.infrastructure_state import (
    build_infrastructure_snapshot,
    print_infrastructure_snapshot,
    compare_infrastructure_states,
)
from src.utils.latency_tracker import LatencyTracker

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
FAULT_INIT_WAIT = 30
METRIC_SCRAPING_BUFFER = 60
ROLLOUT_TIMEOUT = 300
WARMUP_PERIOD = 60


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def build_collection_window(metric_cfg: dict, phase: str) -> CollectionWindow:
    return CollectionWindow(
        lookback_seconds=int(metric_cfg.get(f"{phase}_lookback_seconds", metric_cfg.get("lookback_seconds", 300))),
        step_seconds=int(metric_cfg.get(f"{phase}_step_seconds", metric_cfg.get("step_seconds", 60))),
        rate_interval=str(metric_cfg.get("rate_interval", "1m")),
    )


def metric_groups_from_config(metric_cfg: dict):
    return metric_cfg.get("groups") or ALL_METRIC_GROUPS


def timing_from_config(metric_cfg: dict, key: str, default: int) -> int:
    return int(metric_cfg.get(key, default))


def run_cmd(cmd: list[str], timeout: int = 120) -> Dict[str, Any]:
    """Run a kubectl command and return structured execution evidence."""
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return {
            "status": "success",
            "cmd": cmd,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": round(time.time() - start, 3),
        }
    except subprocess.CalledProcessError as e:
        return {
            "status": "error",
            "cmd": cmd,
            "stdout": e.stdout or "",
            "stderr": e.stderr or "",
            "returncode": e.returncode,
            "duration_seconds": round(time.time() - start, 3),
        }
    except Exception as e:
        return {
            "status": "error",
            "cmd": cmd,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "duration_seconds": round(time.time() - start, 3),
        }

def json_safe(value: Any) -> str:
    """Serialize nested metric values safely for CSV cells."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def flatten_for_csv(prefix: str, value: Any, out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Flatten nested metric dictionaries so summary CSVs remain readable."""
    if out is None:
        out = {}

    if isinstance(value, dict):
        for k, v in value.items():
            flatten_for_csv(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(value, list):
        # Keep short time series/nested lists as JSON in one cell.
        out[prefix] = json_safe(value)
    else:
        out[prefix] = value
    return out

def get_deployment_replicas(namespace: str, deployment: str, default: int = 1) -> int:
    result = run_cmd([
        "kubectl", "get", "deployment", deployment,
        "-n", namespace,
        "-o", "jsonpath={.spec.replicas}",
    ])

    if result.get("status") != "success":
        print(f"[WARNING] Could not get replicas for {namespace}/{deployment}. Using default={default}")
        return default

    value = result.get("stdout", "").strip()
    return int(value) if value else default


def get_deployment_resource_limits(namespace: str, deployment: str) -> Dict[str, Any]:
    result = run_cmd([
        "kubectl", "get", "deployment", deployment,
        "-n", namespace,
        "-o", "json",
    ])

    if result.get("status") != "success":
        print(f"[WARNING] Could not get resource limits for {namespace}/{deployment}")
        return {
            "cpu_limit_millicores": 200,
            "memory_limit_mi": 128,
        }

    data = json.loads(result.get("stdout", "{}"))
    containers = (
        data.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )

    if not containers:
        return {
            "cpu_limit_millicores": 200,
            "memory_limit_mi": 128,
        }

    limits = containers[0].get("resources", {}).get("limits", {})

    cpu = limits.get("cpu", "200m")
    memory = limits.get("memory", "128Mi")

    return {
        "cpu_limit_millicores": parse_cpu_to_millicores(cpu),
        "memory_limit_mi": parse_memory_to_mi(memory),
    }


def parse_cpu_to_millicores(cpu: str) -> int:
    if cpu is None:
        return 200

    cpu = str(cpu).strip()

    if cpu.endswith("m"):
        return int(float(cpu[:-1]))

    return int(float(cpu) * 1000)


def parse_memory_to_mi(memory: str) -> int:
    if memory is None:
        return 128

    memory = str(memory).strip()

    if memory.endswith("Mi"):
        return int(float(memory[:-2]))

    if memory.endswith("Gi"):
        return int(float(memory[:-2]) * 1024)

    if memory.endswith("M"):
        return int(float(memory[:-1]))

    if memory.endswith("G"):
        return int(float(memory[:-1]) * 1024)

    try:
        return int(float(memory) / (1024 * 1024))
    except Exception:
        return 128

def _safe_get(d, path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default

def _metric_stat_any(metrics, service, group, metric, stat="mean"):
    return _safe_get(
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


def build_paper_comparison_rows(
    *,
    fault_id: str,
    method: str,
    app: str,
    namespace: str,
    service: str,
    deployment: str,
    fault_type: str,
    metrics_baseline: dict,
    metrics_before: dict,
    metrics_after: dict,
    infra_baseline: dict,
    infra_before: dict,
    infra_after: dict,
    feedback: dict,
    plan: dict,
    rollout_result: dict,
    command_result: dict,
):
    rows = []

    metric_specs = [
        ("request_rate", "application_api", "request_rate", "mean"),
        ("latency_p95_ms", "application_api", "latency_p95", "mean"),
        ("error_5xx_rate", "application_api", "error_rate_5xx", "mean"),
        ("cpu_usage_cores", "container_resources", "cpu_usage_cores", "mean"),
        ("cpu_limit_ratio", "container_resources", "cpu_usage_to_limit_ratio", "mean"),
        ("cpu_throttle_ratio", "container_resources", "cpu_throttle_ratio", "mean"),
        ("memory_working_set_bytes", "container_resources", "memory_working_set_bytes", "mean"),
        ("memory_limit_ratio", "container_resources", "memory_usage_to_limit_ratio", "mean"),
        ("network_rx_bps", "container_resources", "network_rx_bytes_per_sec", "mean"),
        ("network_tx_bps", "container_resources", "network_tx_bytes_per_sec", "mean"),
    ]

    for label, group, metric, stat in metric_specs:
        baseline = _metric_stat_any(metrics_baseline, service, group, metric, stat)
        fault = _metric_stat_any(metrics_before, service, group, metric, stat)
        after = _metric_stat_any(metrics_after, service, group, metric, stat)

        rows.append({
            "fault_id": fault_id,
            "method": method,
            "app": app,
            "namespace": namespace,
            "service": service,
            "deployment": deployment,
            "fault_type": fault_type,
            "metric": label,
            "healthy_baseline": baseline,
            "fault_state": fault,
            "recovered_state": after,
            "fault_delta_from_baseline": safe_delta(fault, baseline),
            "recovery_delta_from_fault": safe_delta(after, fault),
            "recovery_ratio": safe_recovery_ratio(baseline, fault, after),
        })

    rows.append({
        "fault_id": fault_id,
        "method": method,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment,
        "fault_type": fault_type,
        "metric": "SHS",
        "healthy_baseline": "",
        "fault_state": feedback.get("SHS_before"),
        "recovered_state": feedback.get("SHS_after"),
        "fault_delta_from_baseline": "",
        "recovery_delta_from_fault": feedback.get("delta_SHS"),
        "recovery_ratio": feedback.get("RQ"),
    })

    return rows


def safe_delta(a, b):
    try:
        if a is None or b is None:
            return ""
        return float(a) - float(b)
    except Exception:
        return ""


def safe_recovery_ratio(baseline, fault, after):
    try:
        if baseline is None or fault is None or after is None:
            return ""
        baseline = float(baseline)
        fault = float(fault)
        after = float(after)

        lost = abs(fault - baseline)
        recovered = abs(fault - after)

        if lost <= 1e-9:
            return ""
        return max(0.0, min(1.0, recovered / lost))
    except Exception:
        return ""


def write_rows_csv(path: Path, rows: list[dict]):
    if not rows:
        return

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json_safe(v) for k, v in row.items()})


def _metric_stat(metrics, service, group, metric, stat="mean"):
    return _safe_get(
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


def _pod_phase(metrics, service, phase):
    return _safe_get(
        metrics,
        [
            "service_observations",
            service,
            "metrics",
            "pod_health",
            "pod_phase_count",
            "values",
            phase,
        ],
        0,
    )


def append_experiment_to_global_csv(result: dict, global_csv_path: Path):
    service = result.get("service")
    before = result.get("metrics_before", {})
    after = result.get("metrics_after", {})
    lat = result.get("latencies", {})

    row = {
        # experiment identity
        "timestamp": int(time.time()),
        "method": result.get("method"),
        "app": result.get("app"),
        "namespace": result.get("namespace"),
        "service": service,
        "deployment": result.get("deployment"),
        "fault_type": result.get("fault_type"),
        "fault_id": result.get("fault_id"),

        # timing
        "ttr_seconds": lat.get("total_seconds"),
        "rollout_duration_seconds": result.get("rollout_duration_seconds"),

        # execution/remediation
        "execution_required": result.get("execution_required"),
        "execution_status": result.get("execution_status"),
        "execution_error": result.get("execution_error"),
        "plan_action": result.get("plan_action"),
        "recovery_success": result.get("recovery_success"),

        # feedback
        "SHS_before": result.get("SHS_before"),
        "SHS_after": result.get("SHS_after"),
        "delta_SHS": result.get("delta_SHS"),
        "PS": result.get("PS"),
        "ES": result.get("ES"),
        "RQ": result.get("RQ"),
        "reward": result.get("reward"),
        "resource_cost": result.get("resource_cost"),
        "resource_penalty": result.get("resource_penalty"),
        "regression": result.get("regression"),

        # CPU
        "cpu_cores_before": _metric_stat(before, service, "container_resources", "cpu_usage_cores"),
        "cpu_cores_after": _metric_stat(after, service, "container_resources", "cpu_usage_cores"),
        "cpu_limit_ratio_before": _metric_stat(before, service, "container_resources", "cpu_usage_to_limit_ratio"),
        "cpu_limit_ratio_after": _metric_stat(after, service, "container_resources", "cpu_usage_to_limit_ratio"),
        "cpu_throttle_before": _metric_stat(before, service, "container_resources", "cpu_throttle_ratio"),
        "cpu_throttle_after": _metric_stat(after, service, "container_resources", "cpu_throttle_ratio"),

        # memory
        "memory_ws_bytes_before": _metric_stat(before, service, "container_resources", "memory_working_set_bytes"),
        "memory_ws_bytes_after": _metric_stat(after, service, "container_resources", "memory_working_set_bytes"),
        "memory_limit_ratio_before": _metric_stat(before, service, "container_resources", "memory_usage_to_limit_ratio"),
        "memory_limit_ratio_after": _metric_stat(after, service, "container_resources", "memory_usage_to_limit_ratio"),

        # network
        "network_rx_bps_before": _metric_stat(before, service, "container_resources", "network_rx_bytes_per_sec"),
        "network_rx_bps_after": _metric_stat(after, service, "container_resources", "network_rx_bytes_per_sec"),
        "network_tx_bps_before": _metric_stat(before, service, "container_resources", "network_tx_bytes_per_sec"),
        "network_tx_bps_after": _metric_stat(after, service, "container_resources", "network_tx_bytes_per_sec"),

        # application metrics, if available
        "request_rate_before": _metric_stat(before, service, "application_api", "request_rate"),
        "request_rate_after": _metric_stat(after, service, "application_api", "request_rate"),
        "latency_p95_before": _metric_stat(before, service, "application_api", "latency_p95"),
        "latency_p95_after": _metric_stat(after, service, "application_api", "latency_p95"),
        "error_5xx_before": _metric_stat(before, service, "application_api", "error_rate_5xx"),
        "error_5xx_after": _metric_stat(after, service, "application_api", "error_rate_5xx"),

        # pod/deployment state
        "running_pods_before": _pod_phase(before, service, "Running"),
        "running_pods_after": _pod_phase(after, service, "Running"),
        "failed_pods_before": _pod_phase(before, service, "Failed"),
        "failed_pods_after": _pod_phase(after, service, "Failed"),
        "pending_pods_before": _pod_phase(before, service, "Pending"),
        "pending_pods_after": _pod_phase(after, service, "Pending"),

        # infra/remediation change
        "pod_count_before": result.get("pod_count_before"),
        "pod_count_after": result.get("pod_count_after"),
        "pod_count_delta": result.get("pod_count_delta"),
        "scale_out_occurred": result.get("scale_out_occurred"),
        "scale_up_occurred": result.get("scale_up_occurred"),
        "deployment_replicas_before": result.get("deployment_replicas_before"),
        "deployment_replicas_target": result.get("deployment_replicas_target"),
        "experiment_dir": result.get("experiment_dir"),
    }

    file_exists = global_csv_path.exists()

    with open(global_csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))

        if not file_exists:
            writer.writeheader()

        writer.writerow({k: json_safe(v) for k, v in row.items()})

    print(f"[INFO] Appended compact experiment row to {global_csv_path}")


def build_rule_based_plan(
    fault: Dict[str, Any],
    infra_state_before: Dict[str, Any],
    max_replicas: int = 5,
) -> Dict[str, Any]:
    fault_type = fault["type"]
    service = fault["service"]
    deployment = fault.get("deployment", service)
    namespace = (
        fault.get("namespace")
        or fault.get("target_namespace")
        or fault.get("app")
        or "default"
    )

    pressure_type = fault.get("pressure_type")

    deployment_replicas = get_deployment_replicas(namespace, deployment)
    deployment_limits = get_deployment_resource_limits(namespace, deployment)

    current_replicas = deployment_replicas
    target_replicas = min(max_replicas, max(2, current_replicas + 1))

    cpu_m = deployment_limits["cpu_limit_millicores"]
    memory_mi = deployment_limits["memory_limit_mi"]

    target_cpu_m = max(300, int(cpu_m * 1.5))
    target_memory_mi = max(256, int(memory_mi * 1.5))

    if fault_type == "load_spike":
        action = "scale_out"
        reason = "Load spike is handled by scaling the affected frontend/service by one replica."

    elif fault_type == "pod_kill":
        action = "rollout_restart"
        reason = "Pod kill baseline restarts the affected deployment to bring pods back."

    elif fault_type == "dependency_failure":
        action = "restore_dependency"
        reason = "Dependency failure is injected by scaling the dependency to zero; restore it to the original replica count."

        original_replicas = fault.get("original_replicas")
        target_replicas = int(original_replicas) if original_replicas else 1

    elif fault_type == "bad_image":
        action = "rollout_undo"
        reason = "Bad image is handled by rolling back to the previous deployment revision."

    elif fault_type == "config_error":
        action = "rollout_undo"
        reason = "Config error (bad env var) is handled by rolling back to the previous good revision."

    elif fault_type == "stuck_deployment":
        action = "rollout_resume_then_undo"
        reason = "Stuck deployment is handled by resuming rollout first, then undoing if resume fails."

    elif fault_type == "db_overload":
        action = "rollout_restart"
        reason = "DB overload baseline restarts the overloaded DB deployment to terminate stress processes."

    elif fault_type == "cpu_hog":
        action = "scale_out"
        reason = "CPU hog (stressor saturating CPU) is handled by adding one replica to dilute load."

    elif fault_type == "mem_stress":
        action = "set_memory_limit"
        reason = "Memory stress is handled by increasing the memory limit by 50%."

    elif fault_type == "cpu_throttle":
        action = "set_cpu_limit"
        reason = "CPU throttle (shrunk CPU limit) is handled by restoring/raising the CPU limit."

    elif fault_type in ("net_delay", "net_loss", "disk_stress"):
        action = "rollout_restart"
        reason = f"{fault_type} baseline restarts the affected deployment to recover the impacted pods."

    elif fault_type == "mem_stress":
        action = "set_memory_limit"
        reason = "Memory stress is handled by increasing the affected deployment memory limit."

    elif fault_type == "cpu_hog":
        action = "scale_out"
        reason = "CPU hog is handled by adding one replica to distribute load."
        target_replicas = min(max_replicas, current_replicas + 1)

        # keep resource limits unchanged for pure scale_out
        target_cpu_m = cpu_m
        target_memory_mi = memory_mi

    elif fault_type == "pod_kill":
        action = "rollout_restart"
        reason = "Pod failure is handled by restarting the affected deployment."

    elif fault_type in {"net_delay", "net_loss"}:
        action = "rollout_restart"
        reason = "Network degradation baseline restarts the affected deployment."

    elif fault_type == "disk_stress":
        action = "rollout_restart"
        reason = "Disk stress baseline restarts the affected deployment."

    elif fault_type == "cpu_throttle":
        action = "set_cpu_limit"
        reason = "CPU throttling is handled by increasing CPU limit."

    elif fault_type == "config_error":
        action = "rollout_undo"
        reason = "Configuration error is handled by rolling back the affected deployment."

    else:
        action = "rollout_restart"
        reason = "Unknown fault type; fallback baseline restarts the affected deployment."

    execution_required = action not in {"noop", "monitor"}

    return {
        "planner_type": "rule_based",
        "strategy": action,
        "action": action,
        "severity": "degraded",
        "execution_required": execution_required,
        "execution_reason": reason,
        "service": service,
        "deployment": deployment,
        "namespace": namespace,
        "fault_type": fault_type,
        "pressure_type": pressure_type,

        "current_replicas": current_replicas,
        "target_replicas": target_replicas,

        "current_cpu_millicores": cpu_m,
        "target_cpu_millicores": target_cpu_m,

        "current_memory_mi": memory_mi,
        "target_memory_mi": target_memory_mi,

        "rule_reason": reason,
    }


def execute_rule_based_remediation(
    plan: Dict[str, Any],
    timeout: int = 120,
) -> Tuple[str, Optional[str], str, str, Dict[str, Any]]:
    namespace = plan["namespace"]
    deployment = plan["deployment"]
    action = plan.get("action") or plan.get("strategy")

    if not plan.get("execution_required", True):
        return (
            "skipped_noop",
            None,
            "",
            "",
            {"status": "skipped", "reason": "execution_not_required"},
        )

    commands: List[List[str]] = []

    if action == "scale_out":
        target = int(plan.get("target_replicas") or max(2, int(plan.get("current_replicas", 1)) + 1))
        commands.append([
            "kubectl", "scale", "deployment", deployment,
            "-n", namespace,
            f"--replicas={target}",
        ])

    elif action == "rollout_restart":
        commands.append([
            "kubectl", "rollout", "restart",
            f"deployment/{deployment}",
            "-n", namespace,
        ])

    elif action == "rollout_undo":
        commands.append([
            "kubectl", "rollout", "undo",
            f"deployment/{deployment}",
            "-n", namespace,
        ])

    elif action == "rollout_resume":
        commands.append([
            "kubectl", "rollout", "resume",
            f"deployment/{deployment}",
            "-n", namespace,
        ])

    elif action == "rollout_resume_then_undo":
        commands.append([
            "kubectl", "rollout", "resume",
            f"deployment/{deployment}",
            "-n", namespace,
        ])
        commands.append([
            "kubectl", "rollout", "status",
            f"deployment/{deployment}",
            "-n", namespace,
            "--timeout=60s",
        ])

    elif action == "set_cpu_limit":
        cpu_m = int(plan.get("target_cpu_millicores") or 300)
        commands.append([
            "kubectl", "set", "resources",
            f"deployment/{deployment}",
            "-n", namespace,
            f"--limits=cpu={cpu_m}m",
        ])

    elif action == "set_memory_limit":
        memory_mi = int(plan.get("target_memory_mi") or 256)
        commands.append([
            "kubectl", "set", "resources",
            f"deployment/{deployment}",
            "-n", namespace,
            f"--limits=memory={memory_mi}Mi",
        ])

    elif action == "scale_out_and_set_memory_limit":
        target = int(plan.get("target_replicas") or max(2, int(plan.get("current_replicas", 1)) + 1))
        memory_mi = int(plan.get("target_memory_mi") or 256)

        commands.append([
            "kubectl", "scale", "deployment", deployment,
            "-n", namespace,
            f"--replicas={target}",
        ])
        commands.append([
            "kubectl", "set", "resources",
            f"deployment/{deployment}",
            "-n", namespace,
            f"--limits=memory={memory_mi}Mi",
        ])

    elif action == "restore_dependency":
        target = int(plan.get("target_replicas") or 1)
        commands.append([
            "kubectl", "scale", "deployment", deployment,
            "-n", namespace,
            f"--replicas={target}"])

    else:
        commands.append([
            "kubectl", "rollout", "restart",
            f"deployment/{deployment}",
            "-n", namespace,
        ])

    stdout_parts = []
    stderr_parts = []
    command_results = []
    remediation_script = " && ".join(" ".join(cmd) for cmd in commands)

    for cmd in commands:
        command_result = run_cmd(cmd, timeout=timeout)
        command_results.append({
            "command": " ".join(cmd),
            "result": command_result,
        })

        stdout_parts.append(command_result.get("stdout", ""))

        if command_result.get("status") != "success":
            stderr = command_result.get("stderr", "unknown kubectl error")
            stderr_parts.append(stderr)

            if action == "rollout_resume_then_undo":
                undo_cmd = [
                    "kubectl", "rollout", "undo",
                    f"deployment/{deployment}",
                    "-n", namespace,
                ]
                undo_result = run_cmd(undo_cmd, timeout=timeout)
                command_results.append({
                    "command": " ".join(undo_cmd),
                    "result": undo_result,
                    "fallback": True,
                })
                stdout_parts.append(undo_result.get("stdout", ""))

                if undo_result.get("status") == "success":
                    return (
                        "success",
                        None,
                        "\n".join(stdout_parts),
                        remediation_script + " && " + " ".join(undo_cmd),
                        {
                            "status": "success",
                            "action": action,
                            "fallback_used": "rollout_undo",
                            "commands": command_results,
                        },
                    )

            return (
                "error",
                "\n".join(stderr_parts),
                "\n".join(stdout_parts),
                remediation_script,
                {
                    "status": "error",
                    "action": action,
                    "commands": command_results,
                },
            )

    return (
        "success",
        None,
        "\n".join(stdout_parts),
        remediation_script,
        {
            "status": "success",
            "action": action,
            "commands": command_results,
        },
    )


def run_single_rule_based_experiment(
    fault_type: str,
    service: str,
    duration: str,
    metrics_to_fetch: list[str],
    exp_results_path: Path,
    app: str = "robot-shop",
    experiment_name: Optional[str] = None,
    namespace: str = "robot-shop",
    deployment: Optional[str] = None,
    pod: Optional[str] = None,
    container: Optional[str] = None,
    users: Optional[int] = None,
    spawn_rate: Optional[int] = None,
    pressure_type: Optional[str] = None,
    bad_image: Optional[str] = None,
    slo_thresholds: Optional[Dict[str, float]] = None,
    memory_percent: int = None,
    cpu_cores: int = None,
    memory_mb: int = None,
    metric_collection: Optional[Dict[str, Any]] = None,

    # Chaos Mesh / new fault params
    mode: Optional[str] = None,
    workers: Optional[int] = None,
    size: Optional[str] = None,
    load: Optional[int] = None,
    latency: Optional[str] = None,
    jitter: Optional[str] = None,
    loss: Optional[int] = None,
    correlation: Optional[int] = None,
    direction: Optional[str] = None,
    action: Optional[str] = None,
    limit: Optional[str] = None,
    env: Optional[str] = None,
    bad_value: Optional[str] = None,
    size_mb: Optional[int] = None,
    max_sec: Optional[int] = None,
):
    fault = {
        "type": fault_type,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment or service,
    }

    for key, value in {
                            "name": experiment_name,
                            "duration": duration,
                            "pod": pod,
                            "container": container,
                            "users": users,
                            "spawn_rate": spawn_rate,
                            "pressure_type": pressure_type,
                            "bad_image": bad_image,
                            "memory_percent": memory_percent,
                            "cpu_cores": cpu_cores,
                            "memory_mb": memory_mb,
                            "metric_collection": metric_collection,

                            # Chaos Mesh / new fault params
                            "mode": mode,
                            "workers": workers,
                            "size": size,
                            "load": load,
                            "latency": latency,
                            "jitter": jitter,
                            "loss": loss,
                            "correlation": correlation,
                            "direction": direction,
                            "action": action,
                            "limit": limit,
                            "env": env,
                            "bad_value": bad_value,
                            "size_mb": size_mb,
                            "max_sec": max_sec,
                            }.items():
            if value is not None and value != "":
                fault[key] = value

    run_id = f"rule-{namespace}-{fault_type}-{service}-{int(time.time())}"
    exp_result_dir = exp_results_path / run_id
    exp_result_dir.mkdir(parents=True, exist_ok=True)

    result = run_rule_based_experiment(
        fault=fault,
        exp_dir=exp_result_dir,
        metrics_to_fetch=metrics_to_fetch,
        slo_thresholds=slo_thresholds,
    )

    with open(exp_result_dir / "summary.jsonl", "a") as f:
        f.write(json.dumps(result, default=str) + "\n")

    flat_result = flatten_for_csv("", result)
    with open(exp_result_dir / "summary.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat_result.keys()))
        writer.writeheader()
        writer.writerow({k: json_safe(v) for k, v in flat_result.items()})

    append_experiment_to_global_csv(
        result,
        exp_results_path / "all_experiments_summary.csv"
    )

    return result

def run_rule_based_experiment(
    fault: dict,
    exp_dir: Path,
    metrics_to_fetch: list[str],
    slo_thresholds: Optional[Dict[str, float]] = None,
):
    """
    Rule-based baseline runner.

    Same experiment lifecycle as EV-AIM, but no LLM planner and no LLM executor.
    """
    tracker = LatencyTracker()
    tracker.mark("detect")

    service = fault["service"]
    fault_type = fault["type"]
    app = fault.get("app", "robot-shop")
    namespace = fault.get("namespace") or fault.get("target_namespace") or app
    deployment = fault.get("deployment", service)
    fault_id = f"rule-{fault_type}-{service}-{int(time.time())}"

    metric_cfg = fault.get("metric_collection", {})
    metric_groups = metric_groups_from_config(metric_cfg)
    observe_services = metric_cfg.get("observe_services") or [service]
    fault_init_wait = timing_from_config(metric_cfg, "fault_init_wait_seconds", FAULT_INIT_WAIT)
    fault_observation_wait = timing_from_config(metric_cfg, "fault_observation_wait_seconds", METRIC_SCRAPING_BUFFER)
    warmup_seconds = timing_from_config(metric_cfg, "warmup_seconds", WARMUP_PERIOD)
    rollout_timeout = timing_from_config(metric_cfg, "rollout_timeout_seconds", ROLLOUT_TIMEOUT)
    recovery_wait = int(metric_cfg.get("recovery_wait_seconds", 30))

    before_window = build_collection_window(metric_cfg, "before")
    after_window = build_collection_window(metric_cfg, "after")

    baseline_window = CollectionWindow(
                                        lookback_seconds=int(metric_cfg.get("baseline_lookback_seconds", 120)),
                                        step_seconds=int(metric_cfg.get("baseline_step_seconds", 15)),
                                        rate_interval=str(metric_cfg.get("rate_interval", "30s")),
                                    )

    exp_dir.mkdir(parents=True, exist_ok=True)
    write_json(exp_dir / "effective_config.json", {
        "method": "rule_based",
        "fault": fault,
        "metrics_requested": metrics_to_fetch,
        "metric_groups_used": metric_groups,
        "observe_services_used": observe_services,
        "fault_init_wait_seconds": fault_init_wait,
        "fault_observation_wait_seconds": fault_observation_wait,
        "warmup_seconds": warmup_seconds,
        "rollout_timeout_seconds": rollout_timeout,
        "recovery_wait_seconds": recovery_wait,
        "before_collection_window": before_window,
        "after_collection_window": after_window,
        "baseline_collection_window": baseline_window,
    })

    print("\n========== RULE-BASED RECOVERY BASELINE ==========")
    print(f"[INFO] Fault ID: {fault_id}")
    print(f"[INFO] App={app}, namespace={namespace}, service={service}, deployment={deployment}")
    print(f"[INFO] Fault type={fault_type}")
    print(f"[INFO] Results directory: {exp_dir}")

    background_load_result = None

    if is_category_b_fault(fault):
        baseline_users = int(fault.get("baseline_users", fault.get("users", 20)))
        baseline_spawn_rate = int(fault.get("baseline_spawn_rate", fault.get("spawn_rate", 2)))

        print("\n[STAGE 0A] Starting low background traffic for Category-B fault...")
        print(
            f"[LOAD] app={app}, users={baseline_users}, "
            f"spawn_rate={baseline_spawn_rate}"
        )

        start_load(app, baseline_users, baseline_spawn_rate)

        background_load_result = {
            "app": app,
            "users": baseline_users,
            "spawn_rate": baseline_spawn_rate,
            "traffic_type": "low_background_traffic",
        }

        print(f"[WAIT] Sleeping {warmup_seconds}s before healthy baseline.")
        time.sleep(warmup_seconds)

    elif is_category_a_fault(fault):
        baseline_users = int(fault.get("baseline_users", 20))
        baseline_spawn_rate = int(fault.get("baseline_spawn_rate", 2))

        print("\n[STAGE 0A] Starting normal baseline traffic for Category-A fault...")
        print(
            f"[LOAD] app={app}, users={baseline_users}, "
            f"spawn_rate={baseline_spawn_rate}"
        )

        start_load(app, baseline_users, baseline_spawn_rate)

        background_load_result = {
            "app": app,
            "users": baseline_users,
            "spawn_rate": baseline_spawn_rate,
            "traffic_type": "normal_baseline_traffic",
        }

        print(f"[WAIT] Sleeping {warmup_seconds}s before healthy baseline.")
        time.sleep(warmup_seconds)

    print("\n[STAGE 0] Collecting healthy baseline metrics...")
    metrics_baseline = collect_multi_service_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        services=observe_services,
        window=baseline_window,
        metric_groups=metric_groups,
    )

    write_json(exp_dir / "metrics_baseline.json", metrics_baseline)

    infra_state_baseline = build_infrastructure_snapshot(metrics_baseline, target_service=service)
    write_json(exp_dir / "infrastructure_baseline.json", infra_state_baseline)


    if is_category_a_fault(fault):
        print("\n[STAGE 0B] Stopping normal baseline traffic before fault injection...")
        try:
            stop_load(app)
        except Exception as e:
            print(f"[WARNING] Could not stop baseline traffic: {type(e).__name__}: {e}")

        print("[WAIT] Sleeping 10s before injecting traffic fault.")
        time.sleep(10)

    print("\n[STAGE 1] Injecting fault...")
    fault_injection_result = inject_fault(fault)
    tracker.mark("fault_injected")
    write_json(exp_dir / "fault_injection_result.json", fault_injection_result)
    print(f"[INFO] Fault injection result saved: {exp_dir / 'fault_injection_result.json'}")

    print("\n[STAGE 2] Waiting for fault initialization...")
    print(f"[WAIT] Sleeping {fault_init_wait}s.")
    time.sleep(fault_init_wait)
    tracker.mark("fault_initialized")

    print("\n[STAGE 3] Waiting for metric ingestion...")
    print(f"[WAIT] Sleeping {fault_observation_wait}s.")
    time.sleep(fault_observation_wait)
    tracker.mark("metrics_available")

    
    print("\n[STAGE 4] Collecting pre-remediation metrics...")
    print(
        f"[INFO] Querying Prometheus: namespace={namespace}, service={service}, "
        f"lookback={before_window.lookback_seconds}s, step={before_window.step_seconds}s, "
        f"rate_interval={before_window.rate_interval}"
    )
    print(f"[INFO] Collecting groups: {metric_groups}")
    print(f"[INFO] Observing services: {observe_services}")

    metrics_before = collect_multi_service_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        services=observe_services,
        window=before_window,
        metric_groups=metric_groups,
    )
    tracker.mark("metrics_before")
    write_json(exp_dir / "metrics_before.json", metrics_before)

    print("\n[STAGE 5] Building infrastructure snapshot...")
    infra_state_before = build_infrastructure_snapshot(metrics_before, target_service=service)
    write_json(exp_dir / "infrastructure_before.json", infra_state_before)
    print_infrastructure_snapshot(infra_state_before)

    print("\n[STAGE 6] Building planner-equivalent context...")
    planner_context = build_planner_metrics(metrics_before)

    print("\n" + "=" * 80)
    print("PLANNER CONTEXT")
    print("=" * 80)
    print(json.dumps(planner_context, indent=2, sort_keys=False))
    print("=" * 80)

    write_json(exp_dir / "planner_metrics_before.json", planner_context)

    print("\n[STAGE 7] Selecting deterministic rule-based remediation...")
    plan = build_rule_based_plan(fault, infra_state_before)
    tracker.mark("plan_generated")
    write_json(exp_dir / "plan.json", plan)
    print("[INFO] Rule-based plan selected:")
    print(json.dumps(plan, indent=2, default=str))

    print("\n[STAGE 8] Executing rule-based remediation...")
    exec_status, exec_error, stdout, remediation_script, command_result = execute_rule_based_remediation(plan)
    tracker.mark("remediation_executed")
    write_json(exp_dir / "rule_execution.json", command_result)
    with open(exp_dir / "remediation_script.txt", "w") as f:
        f.write(remediation_script)
    print(f"[INFO] Rule execution status: {exec_status}")
    if exec_error:
        print(f"[ERROR] Rule execution error: {exec_error}")

    print("\n[STAGE 9] Waiting for rollout after rule-based remediation...")
    if exec_status == "success" and plan.get("execution_required", True):
        rollout_result = wait_for_rollout_completion(
            service=deployment,
            namespace=namespace,
            timeout=rollout_timeout,
        )
    else:
        rollout_result = {
            "rollout_completed": False,
            "rollout_duration_seconds": None,
            "timeout_occurred": False,
            "final_pod_count": None,
            "skipped": True,
            "reason": "execution_failed_or_not_required",
        }
    tracker.mark("rollout_complete")
    write_json(exp_dir / "rollout_status.json", rollout_result)
    print(json.dumps(rollout_result, indent=2, default=str))

    if not rollout_result.get("rollout_completed"):
        failure_reasons = get_pod_failure_reasons(deployment, namespace)
        if failure_reasons:
            write_json(exp_dir / "pod_failures.json", failure_reasons)

    print("\n[STAGE 10] Waiting for post-remediation warmup...")
    if warmup_seconds > 0:
        print(f"[WAIT] Sleeping {warmup_seconds}s.")
        time.sleep(warmup_seconds)
    tracker.mark("warmup_complete")

    print("\n[STAGE 11] Collecting post-remediation metrics...")
    metrics_after = collect_multi_service_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        services=observe_services,
        window=after_window,
        metric_groups=metric_groups,
    )
    tracker.mark("metrics_after")
    write_json(exp_dir / "metrics_after.json", metrics_after)

    print("\n[STAGE 12] Capturing post-remediation infrastructure state...")
    infra_state_after = build_infrastructure_snapshot(metrics_after, target_service=service)
    infra_comparison = compare_infrastructure_states(infra_state_before, infra_state_after)
    before_mem = plan.get("current_memory_mi")
    after_mem = plan.get("target_memory_mi")
    before_cpu = plan.get("current_cpu_millicores")
    after_cpu = plan.get("target_cpu_millicores")

    infra_comparison["deployment_replicas_before"] = plan.get("current_replicas")
    infra_comparison["deployment_replicas_after"] = get_deployment_replicas(namespace, deployment)

    infra_comparison["scale_out_occurred"] = (
        infra_comparison["deployment_replicas_after"]
        > infra_comparison["deployment_replicas_before"]
    )

    infra_comparison["memory_limit_changed"] = after_mem != before_mem and plan.get("action") in {
        "set_memory_limit",
        "scale_out_and_set_memory_limit",
    }

    infra_comparison["cpu_limit_changed"] = after_cpu != before_cpu and plan.get("action") == "set_cpu_limit"

    infra_comparison["scale_up_occurred"] = (
        infra_comparison["memory_limit_changed"]
        or infra_comparison["cpu_limit_changed"]
    )
    write_json(exp_dir / "infrastructure_after.json", infra_state_after)
    write_json(exp_dir / "infrastructure_comparison.json", infra_comparison)
    print_infrastructure_snapshot(infra_state_after)

    print("\n[STAGE 13] Computing common feedback metrics...")
    feedback = compute_feedback(
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        infra_state_before=infra_state_before,
        infra_state_after=infra_state_after,
        infra_comparison=infra_comparison,
        plan=plan,
        app=app,
        fault_type=fault_type,
        target_service=service,
        execution_required=plan.get("execution_required", True),
        execution_status=exec_status,
        execution_error=exec_error,
        rollout_result=rollout_result,
        ansible_log=stdout,
        playbook_retries=0,
        slo_thresholds=slo_thresholds,
    )
    tracker.mark("feedback_computed")
    write_json(exp_dir / "feedback.json", feedback)
    print(json.dumps(feedback, indent=2, default=str))

    comparison_rows = build_paper_comparison_rows(
        fault_id=fault_id,
        method="rule_based",
        app=app,
        namespace=namespace,
        service=service,
        deployment=deployment,
        fault_type=fault_type,
        metrics_baseline=metrics_baseline,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        infra_baseline=infra_state_baseline,
        infra_before=infra_state_before,
        infra_after=infra_state_after,
        feedback=feedback,
        plan=plan,
        rollout_result=rollout_result,
        command_result=command_result,
    )

    write_rows_csv(exp_dir / "paper_comparison.csv", comparison_rows)

    print("\n[STAGE 14] Storing unified rule-based experience...")
    print("\n[STAGE 14] Skipping experience storage...")
    
    print("\n[STAGE 15] Final fault cleanup/recovery...")
    try:
        
        if background_load_result:
            try:
                print("\n[CLEANUP] Stopping experiment background/baseline traffic...")
                stop_load(background_load_result["app"])
            except Exception as e:
                print(f"[WARNING] Could not stop experiment traffic: {type(e).__name__}: {e}")
        recovery_result = recover_fault(fault, fault_injection_result)
    except Exception as e:
        recovery_result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
    write_json(exp_dir / "final_recovery.json", recovery_result)
    if recovery_wait > 0:
        print(f"[WAIT] Sleeping {recovery_wait}s after final recovery.")
        time.sleep(recovery_wait)
    tracker.mark("final_recovery_done")

    latencies = tracker.summary()
    write_json(exp_dir / "latencies.json", latencies)

    result = {
        "method": "rule_based",
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment,
        "fault_type": fault_type,
        "fault_id": fault_id,
        "latencies": latencies,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "planner_icl_samples": 0,
        "executor_icl_samples": 0,
        "SHS_before": feedback.get("SHS_before"),
        "SHS_after": feedback.get("SHS_after"),
        "delta_SHS": feedback.get("delta_SHS"),
        "RQ": feedback.get("RQ"),
        "reward": feedback.get("reward"),
        "resource_cost": feedback.get("resource_cost"),
        "resource_penalty": feedback.get("resource_penalty"),
        "PS": feedback.get("PS"),
        "ES": feedback.get("ES"),
        "plan_action": feedback.get("plan_action"),
        "expected_actions": feedback.get("expected_actions"),
        "code_changed_system": feedback.get("code_changed_system"),
        "execution_failure_reason": feedback.get("execution_failure_reason"),
        "recovery_success": feedback.get("recovery_success"),
        "regression": feedback.get("regression"),
        "execution_required": plan.get("execution_required", True),
        "execution_reason": plan.get("execution_reason"),
        "execution_status": exec_status,
        "execution_error": exec_error,
        "playbook_retries": 0,
        "rollout_completed": rollout_result.get("rollout_completed"),
        "rollout_duration_seconds": rollout_result.get("rollout_duration_seconds"),
        "rollout_timeout_occurred": rollout_result.get("timeout_occurred"),
        "pod_count_before": infra_state_before.get("namespace_running_pods"),
        "pod_count_after": infra_state_after.get("namespace_running_pods"),
        "pod_count_delta": infra_comparison.get("namespace_running_pods_delta"),
        "deployment_replicas_before": plan.get("current_replicas"),
        "deployment_replicas_target": plan.get("target_replicas"),
        "deployment_cpu_limit_before_millicores": plan.get("current_cpu_millicores"),
        "deployment_cpu_limit_target_millicores": plan.get("target_cpu_millicores"),
        "deployment_memory_limit_before_mi": plan.get("current_memory_mi"),
        "deployment_memory_limit_target_mi": plan.get("target_memory_mi"),
        "scale_out_occurred": infra_comparison.get("scale_out_occurred"),
        "scale_up_occurred": infra_comparison.get("scale_up_occurred"),
        "cpu_limit_per_pod_before_millicores": infra_state_before.get("cpu_limit_per_pod_millicores"),
        "cpu_limit_per_pod_after_millicores": infra_state_after.get("cpu_limit_per_pod_millicores"),
        "memory_limit_per_pod_before_bytes": infra_state_before.get("memory_limit_per_pod_bytes"),
        "memory_limit_per_pod_after_bytes": infra_state_after.get("memory_limit_per_pod_bytes"),
        "infrastructure_before": infra_state_before,
        "infrastructure_after": infra_state_after,
        "infrastructure_comparison": infra_comparison,
        "ansible_recap": {"method": "kubectl_rule", "command": remediation_script},
        "final_recovery": recovery_result,
        "experiment_dir": str(exp_dir),
        "metrics_baseline": metrics_baseline,
        "infrastructure_baseline": infra_state_baseline,
        "paper_comparison_csv": str(exp_dir / "paper_comparison.csv"),
    }

    write_json(exp_dir / "result.json", result)
    print("\n[SUCCESS] Rule-based experiment completed.")
    print(f"[INFO] Result saved to {exp_dir / 'result.json'}")
    return result
