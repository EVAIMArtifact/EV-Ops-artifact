import time
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
import yaml
import csv
from src.monitoring.collector import collect_fault_observation, collect_multi_service_observation
from src.monitoring.config import CollectionWindow, ALL_METRIC_GROUPS
import sys
import os
from prometheus_api_client import PrometheusConnect
from src.monitoring.prometheus_client import PublicPrometheusClient

# --- Fault Injection ---
from src.fault_injection import (
    inject_fault,
    recover_fault,
    start_load,
    stop_load,
    is_category_a_fault,
    is_category_b_fault,
)

# --- Planner ---
from src.planner.llm_planner import MitigationPlanner
from src.planner.retrieval import (
    retrieve_experience,
    format_for_prompt
)
from src.planner.build_planner_metrics import build_planner_metrics, build_planner_metrics_groq

# --- Executor ---
from src.executor.ansible_generator import AnsibleExecutor
from src.executor.code_retrieval import retrieve_icl_examples
from src.executor.rollout_monitor import wait_for_rollout_completion, get_pod_failure_reasons

from src.executor.validator import validate_ansible_playbook


# # --- Feedback ---
from src.feedback.compute_feedback import compute_feedback
from src.feedback.knowledge_store import store_unified_experience


# --- Monitoring ---
# from src.monitoring.infrastructure_state import capture_infrastructure_state, compare_infrastructure_states
from src.utils.infrastructure_state import (
    build_infrastructure_snapshot,
    print_infrastructure_snapshot,
    compare_infrastructure_states,
)


# --- Model Config ---
from src.experiment.model_config import ModelConfig
from src.utils.latency_tracker import LatencyTracker
from src.utils.ansi_parser import parse_ansible_recap
from src.clients.llm_client import preload_llm_dependencies

# -----------------------------
# Constants
# -----------------------------


# -----------------------------
# Helper Functions
# -----------------------------
def strip_markdown_fences(content: str) -> str:
    """
    Strip markdown code fences from LLM-generated content.

    Handles formats like:
    - ```yaml\\n...\\n```
    - ```\\n...\\n```
    - Raw content (no fences)

    Args:
        content: Raw LLM output potentially containing markdown fences

    Returns:
        Clean content with fences removed
    """
    cleaned = content.strip()

    # Remove markdown code fences if present
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")

        # Remove opening fence (```yaml, ```json, or just ```)
        if lines[0].startswith("```"):
            lines = lines[1:]

        # Remove closing fence (```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        cleaned = "\n".join(lines).strip()

    return cleaned


def ensure_average_cpu_metric(metrics: dict) -> None:
    """Ensure the presence of average_cpu_usage in a metrics dict.

    Some Prometheus queries or scrape windows occasionally omit the
    ``average_cpu_usage`` key.  Downstream logic (feedback computation, CSV
    formatting, planning prompts) expects this metric to exist.  To avoid
    conditional checks everywhere we normalize the dictionary early by
    providing a default value of ``0.4`` when the key is missing.
    """
    if "average_cpu_usage" not in metrics:
        metrics["average_cpu_usage"] = 0.4


def extract_metric_value(metric_entry):
    """Backward-compatible metric scalar extraction for legacy Prometheus rows."""
    if isinstance(metric_entry, list) and len(metric_entry) > 0:
        return metric_entry[0].get("value")
    if isinstance(metric_entry, dict):
        # New collector returns nested dicts. Keep them as JSON in CSV.
        return json_safe(metric_entry)
    return metric_entry


def fix_playbook_types(playbook_yaml: str) -> str:
    """
    Fix type mismatches in LLM-generated Ansible playbooks.

    LLMs often generate issues with Jinja2 expressions and type mismatches:
    1. Unquoted Jinja2 expressions break YAML parsing
    2. Static string values like replicas: "2" should be integers

    This function applies text-based fixes to handle both cases while preserving
    Jinja2 expressions that Ansible needs to evaluate at runtime.

    Args:
        playbook_yaml: Raw playbook YAML string (possibly with type errors)

    Returns:
        Corrected playbook YAML string
    """
    import re

    # PHASE 1: Fix Jinja2 template expressions (text-based, before YAML parsing)
    # LLMs often generate UNQUOTED Jinja2 expressions which break YAML parsing
    # We need to ADD quotes around unquoted {{ ... }} expressions

    # First, add quotes to UNQUOTED Jinja2 expressions (the main issue)
    # Match: "  field: {{ expr }}" where expr is NOT already quoted
    # Replace with: "  field: "{{ expr }}""
    unquoted_jinja_pattern = r'(:\s+)(\{\{[^}]*\}\})(\s*$|\s*\n)'
    playbook_yaml = re.sub(unquoted_jinja_pattern, r'\1"\2"\3', playbook_yaml, flags=re.MULTILINE)

    # Also handle cases where the Jinja2 expression has nested braces (e.g., {{ x | int + 1 }})
    # The above pattern might not catch all cases, so we use a more permissive pattern
    unquoted_jinja_pattern2 = r'(:\s+)(\{\{[^"\']*?\}\})(?=[,\s\n]|$)'
    playbook_yaml = re.sub(unquoted_jinja_pattern2, r'\1"\2"', playbook_yaml)

    # Check if there are any Jinja2 expressions in the playbook
    # If so, skip YAML round-trip to preserve them correctly
    has_jinja2 = '{{' in playbook_yaml and '}}' in playbook_yaml

    if has_jinja2:
        # PHASE 2a: Text-based static string fix (preserves Jinja2 expressions)
        # Fix cases like: replicas: "2" -> replicas: 2
        # But NOT: replicas: "{{ expr }}" (keep as-is for Ansible)

        # Pattern: field: "number" or field: 'number' where number is digits only
        # This handles static string integers like replicas: "2"
        static_int_pattern = r'(\s+replicas:\s+)["\'](\d+)["\']'
        playbook_yaml = re.sub(static_int_pattern, r'\1\2', playbook_yaml)

        # NOTE: We do NOT remove quotes from Jinja2 expressions like replicas: "{{ value }}"
        # because Ansible's kubernetes.core.k8s module REQUIRES quotes in definition dicts
        # The LLM should use kubectl scale commands instead for dynamic replica counts

        # Similar for delay, retries
        for field in ['delay', 'retries']:
            pattern = rf'(\s+{field}:\s+)["\'](\d+)["\']'
            playbook_yaml = re.sub(pattern, r'\1\2', playbook_yaml)

        # PHASE 2a-extra: Skip complex k8s module conversion to avoid regex backtracking
        # The kubernetes.core.k8s module with Jinja2 in replicas field works fine in practice
        # Previous complex regex caused catastrophic backtracking on long playbooks
        # If this causes issues, we'll handle them during execution with retry logic

        print("[INFO] Jinja2 expressions detected - using text-based fixes only")
        return playbook_yaml

    # PHASE 2b: YAML-based static string fix (no Jinja2 expressions)
    # Fields that should be numeric
    numeric_fields = ['replicas', 'cpu', 'memory', 'limits', 'requests', 'delay', 'retries']

    try:
        # Parse YAML
        playbook_data = yaml.safe_load(playbook_yaml)

        # Recursively fix type issues
        def fix_types_recursive(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    # Fix numeric fields that are static strings
                    if key in numeric_fields and isinstance(value, str):
                        try:
                            obj[key] = int(value)
                        except ValueError:
                            try:
                                obj[key] = float(value)
                            except ValueError:
                                pass  # Keep as string if not convertible

                    # Recursively process nested dicts and lists
                    elif isinstance(value, (dict, list)):
                        fix_types_recursive(value)

            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, (dict, list)):
                        fix_types_recursive(item)

        fix_types_recursive(playbook_data)

        # Convert back to YAML
        return yaml.dump(playbook_data, default_flow_style=False, sort_keys=False)

    except yaml.YAMLError as e:
        # If YAML parsing fails, return the Phase 1 result
        print(f"[WARNING] Could not parse playbook YAML for Phase 2 type fixing: {e}")
        print("[INFO] Returning Phase 1 result")
        return playbook_yaml

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
        ("fs_read_bps", "container_resources", "fs_read_bytes_per_sec", "mean"),
        ("fs_write_bps", "container_resources", "fs_write_bytes_per_sec", "mean"),
        ("fs_read_ops", "container_resources", "fs_read_ops_per_sec", "mean"),
        ("fs_write_ops", "container_resources", "fs_write_ops_per_sec", "mean"),
        ("fs_usage_ratio", "container_resources", "fs_usage_to_limit_ratio", "mean"),
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

    rows.append({
        "fault_id": fault_id,
        "method": method,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment,
        "fault_type": fault_type,
        "metric": "FRQ",
        "healthy_baseline": "",
        "fault_state": "",
        "recovered_state": feedback.get("FRQ"),
        "fault_delta_from_baseline": "",
        "recovery_delta_from_fault": "",
        "recovery_ratio": feedback.get("FRQ"),
    })

    rows.append({
        "fault_id": fault_id,
        "method": method,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment,
        "fault_type": fault_type,
        "metric": "primary_metric_fixed",
        "healthy_baseline": "",
        "fault_state": "",
        "recovered_state": feedback.get("primary_metric_fixed"),
        "fault_delta_from_baseline": "",
        "recovery_delta_from_fault": "",
        "recovery_ratio": "",
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
    # identity
    "timestamp": int(time.time()),
    "method": result.get("method"),
    "app": result.get("app"),
    "namespace": result.get("namespace"),
    "service": service,
    "deployment": result.get("deployment"),
    "fault_type": result.get("fault_type"),
    "fault_id": result.get("fault_id"),
    "experiment_dir": result.get("experiment_dir"),

    # timing / overhead
    "ttr_seconds": lat.get("total_seconds"),
    "rollout_duration_seconds": result.get("rollout_duration_seconds"),
    "planner_icl_samples": result.get("planner_icl_samples"),
    "executor_icl_samples": result.get("executor_icl_samples"),
    "playbook_retries": result.get("playbook_retries"),

    # plan / execution
    "execution_required": result.get("execution_required"),
    "execution_reason": result.get("execution_reason"),
    "execution_status": result.get("execution_status"),
    "execution_error": result.get("execution_error"),
    "action_type": result.get("action_type"),
    "normalized_action": json_safe(result.get("normalized_action")),
    "code_changed_system": result.get("code_changed_system"),
    "execution_failure_reason": result.get("execution_failure_reason"),
    "rollout_completed": result.get("rollout_completed"),
    "rollout_timeout_occurred": result.get("rollout_timeout_occurred"),

    # main evaluation signals
    "FRQ": result.get("FRQ"),
    "reward": result.get("reward"),
    "ES": result.get("ES"),
    "recovery_success": result.get("recovery_success"),

    # diagnostics
    "SHS_before": result.get("SHS_before"),
    "SHS_after": result.get("SHS_after"),
    "delta_SHS": result.get("delta_SHS"),
    "resource_penalty": result.get("resource_penalty"),
    "degradation_penalty": result.get("degradation_penalty"),
    "fault_success_reason": result.get("fault_success_reason"),

    # target resource metrics
    "cpu_limit_ratio_before": _metric_stat(before, service, "container_resources", "cpu_usage_to_limit_ratio"),
    "cpu_limit_ratio_after": _metric_stat(after, service, "container_resources", "cpu_usage_to_limit_ratio"),
    "cpu_throttle_before": _metric_stat(before, service, "container_resources", "cpu_throttle_ratio"),
    "cpu_throttle_after": _metric_stat(after, service, "container_resources", "cpu_throttle_ratio"),

    "memory_limit_ratio_before": _metric_stat(before, service, "container_resources", "memory_usage_to_limit_ratio"),
    "memory_limit_ratio_after": _metric_stat(after, service, "container_resources", "memory_usage_to_limit_ratio"),
    "memory_ws_bytes_before": _metric_stat(before, service, "container_resources", "memory_working_set_bytes"),
    "memory_ws_bytes_after": _metric_stat(after, service, "container_resources", "memory_working_set_bytes"),

    # app symptoms
    "request_rate_before": _metric_stat(before, service, "application_api", "request_rate"),
    "request_rate_after": _metric_stat(after, service, "application_api", "request_rate"),
    "latency_p95_before": _metric_stat(before, service, "application_api", "latency_p95"),
    "latency_p95_after": _metric_stat(after, service, "application_api", "latency_p95"),
    "error_5xx_before": _metric_stat(before, service, "application_api", "error_rate_5xx"),
    "error_5xx_after": _metric_stat(after, service, "application_api", "error_rate_5xx"),

    "fs_read_bps_before": _metric_stat(before, service, "container_resources", "fs_read_bytes_per_sec"),
    "fs_read_bps_after": _metric_stat(after, service, "container_resources", "fs_read_bytes_per_sec"),

    "fs_write_bps_before": _metric_stat(before, service, "container_resources", "fs_write_bytes_per_sec"),
    "fs_write_bps_after": _metric_stat(after, service, "container_resources", "fs_write_bytes_per_sec"),

    "fs_read_ops_before": _metric_stat(before, service, "container_resources", "fs_read_ops_per_sec"),
    "fs_read_ops_after": _metric_stat(after, service, "container_resources", "fs_read_ops_per_sec"),

    "fs_write_ops_before": _metric_stat(before, service, "container_resources", "fs_write_ops_per_sec"),
    "fs_write_ops_after": _metric_stat(after, service, "container_resources", "fs_write_ops_per_sec"),

    "fs_usage_ratio_before": _metric_stat(before, service, "container_resources", "fs_usage_to_limit_ratio"),
    "fs_usage_ratio_after": _metric_stat(after, service, "container_resources", "fs_usage_to_limit_ratio"),

    # pod/deployment symptoms
    "running_pods_before": _pod_phase(before, service, "Running"),
    "running_pods_after": _pod_phase(after, service, "Running"),
    "pending_pods_before": _pod_phase(before, service, "Pending"),
    "pending_pods_after": _pod_phase(after, service, "Pending"),
    "failed_pods_before": _pod_phase(before, service, "Failed"),
    "failed_pods_after": _pod_phase(after, service, "Failed"),

    # actual infra change
    "scale_out_occurred": result.get("scale_out_occurred"),
    "scale_up_occurred": result.get("scale_up_occurred"),
    "cpu_limit_per_pod_before_millicores": result.get("cpu_limit_per_pod_before_millicores"),
    "cpu_limit_per_pod_after_millicores": result.get("cpu_limit_per_pod_after_millicores"),
    "memory_limit_per_pod_before_bytes": result.get("memory_limit_per_pod_before_bytes"),
    "memory_limit_per_pod_after_bytes": result.get("memory_limit_per_pod_after_bytes"),
    "pod_count_before": result.get("pod_count_before"),
    "pod_count_after": result.get("pod_count_after"),
    "pod_count_delta": result.get("pod_count_delta"),
}

    file_exists = global_csv_path.exists()

    with open(global_csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))

        if not file_exists:
            writer.writeheader()

        writer.writerow({k: json_safe(v) for k, v in row.items()})

    print(f"[INFO] Appended compact experiment row to {global_csv_path}")

# -----------------------------
# Configurations
# -----------------------------
# TIMING RATIONALE (validated by systems-research-evaluator agent):
# REACTIVE MODE - Act immediately when fault is detected (production-realistic incident response)
# - Fault initialization: 20s for kubectl exec + stress-ng startup (5-14s typical)
# - Metric scraping buffer: 30s for Prometheus (15s) + cAdvisor export (10-15s) lag
# - Observation window: 45s (3-4 Prometheus scrapes @ 15s intervals - minimum for trend detection)
# - NO WAITING for fault completion - remediation acts while fault is still active
# This mimics production incident response where you act on early symptoms, not complete fault lifecycle
FAULT_INIT_WAIT = 30  # Default only; override with metric_collection.fault_init_wait_seconds
METRIC_SCRAPING_BUFFER = 60  # Default only; override with metric_collection.fault_observation_wait_seconds
REACTIVE_OBSERVATION_WINDOW = "45s"  # Legacy default; currently not used by the multi-service collector path
OBS_WINDOW_AFTER = "120s"  # Legacy default; currently not used by the multi-service collector path
OBS_WINDOW_INTERVAL = "1s"  # Legacy default; currently not used by the multi-service collector path
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
ROLLOUT_TIMEOUT = 300  # Default only; override with metric_collection.rollout_timeout_seconds
WARMUP_PERIOD = 60  # Default only; override with metric_collection.warmup_seconds


def json_safe(value: Any) -> str:
    """Serialize nested metric values safely for CSV cells."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    return str(value)



def first_present(*values):
    """Return the first value that is not None and not an empty string."""
    for value in values:
        if value is not None and value != "":
            return value
    return None


def build_target_changes_summary(
    *,
    infra_before: Optional[Dict[str, Any]],
    infra_after: Optional[Dict[str, Any]],
    infra_comparison: Optional[Dict[str, Any]],
    feedback: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Compact before -> after resource/action summary for:
    - result.json
    - summary.csv
    - retrieved planner experiences

    It intentionally focuses on stable target/deployment fields rather than
    noisy namespace-wide metrics.
    """
    infra_before = infra_before or {}
    infra_after = infra_after or {}
    infra_comparison = infra_comparison or {}
    feedback = feedback or {}
    plan = plan or {}

    target_changes = plan.get("target_changes") if isinstance(plan.get("target_changes"), dict) else {}
    normalized_action = feedback.get("normalized_action") or {}

    before = {
        "replicas": first_present(
            infra_before.get("target_replicas"),
            infra_before.get("deployment_replicas"),
            infra_before.get("deployment_ready_replicas"),
            infra_before.get("target_running_pods"),
            infra_before.get("running_pods"),
        ),
        "cpu_limit_millicores": first_present(
            infra_before.get("target_cpu_limit_per_pod_millicores"),
            infra_before.get("cpu_limit_per_pod_millicores"),
        ),
        "cpu_limit_cores": first_present(
            infra_before.get("target_cpu_limit_per_pod"),
            infra_before.get("cpu_limit_per_pod"),
        ),
        "memory_limit_bytes": first_present(
            infra_before.get("target_memory_limit_per_pod_bytes"),
            infra_before.get("memory_limit_per_pod_bytes"),
        ),
        "memory_limit_mb": None,
        "fs_usage_bytes": infra_before.get("target_fs_usage_bytes"),
        "fs_limit_bytes": infra_before.get("target_fs_limit_bytes"),
        "fs_usage_to_limit_ratio": infra_before.get("target_fs_usage_to_limit_ratio"),
    }

    after = {
        "replicas": first_present(
            infra_after.get("target_replicas"),
            infra_after.get("deployment_replicas"),
            infra_after.get("deployment_ready_replicas"),
            infra_after.get("target_running_pods"),
            infra_after.get("running_pods"),
        ),
        "cpu_limit_millicores": first_present(
            infra_after.get("target_cpu_limit_per_pod_millicores"),
            infra_after.get("cpu_limit_per_pod_millicores"),
        ),
        "cpu_limit_cores": first_present(
            infra_after.get("target_cpu_limit_per_pod"),
            infra_after.get("cpu_limit_per_pod"),
        ),
        "memory_limit_bytes": first_present(
            infra_after.get("target_memory_limit_per_pod_bytes"),
            infra_after.get("memory_limit_per_pod_bytes"),
        ),
        "memory_limit_mb": None,
        "fs_usage_bytes": infra_after.get("target_fs_usage_bytes"),
        "fs_limit_bytes": infra_after.get("target_fs_limit_bytes"),
        "fs_usage_to_limit_ratio": infra_after.get("target_fs_usage_to_limit_ratio"),
    }

    try:
        if before["memory_limit_bytes"] is not None:
            before["memory_limit_mb"] = round(float(before["memory_limit_bytes"]) / (1024 * 1024), 2)
    except Exception:
        before["memory_limit_mb"] = None

    try:
        if after["memory_limit_bytes"] is not None:
            after["memory_limit_mb"] = round(float(after["memory_limit_bytes"]) / (1024 * 1024), 2)
    except Exception:
        after["memory_limit_mb"] = None

    def delta(key: str):
        try:
            b = before.get(key)
            a = after.get(key)
            if b is None or a is None:
                return None
            return float(a) - float(b)
        except Exception:
            return None

    changes = {
        "replica_delta": first_present(
            infra_comparison.get("target_running_pods_delta"),
            infra_comparison.get("deployment_replicas_delta"),
            infra_comparison.get("namespace_running_pods_delta"),
            delta("replicas"),
        ),
        "cpu_limit_millicores_delta": first_present(
            infra_comparison.get("target_cpu_limit_per_pod_delta_millicores"),
            infra_comparison.get("cpu_limit_per_pod_delta_millicores"),
            delta("cpu_limit_millicores"),
        ),
        "cpu_limit_cores_delta": first_present(
            infra_comparison.get("target_cpu_limit_per_pod_delta"),
            infra_comparison.get("cpu_limit_per_pod_delta"),
            delta("cpu_limit_cores"),
        ),
        "memory_limit_bytes_delta": first_present(
            infra_comparison.get("target_memory_limit_per_pod_delta_bytes"),
            infra_comparison.get("memory_limit_per_pod_delta_bytes"),
            delta("memory_limit_bytes"),
        ),
        "memory_limit_mb_delta": delta("memory_limit_mb"),
        "scale_out_occurred": infra_comparison.get("scale_out_occurred"),
        "scale_up_occurred": infra_comparison.get("scale_up_occurred"),
        
        "fs_usage_bytes_delta": infra_comparison.get("target_fs_usage_bytes_delta"),
        "fs_usage_to_limit_ratio_delta": infra_comparison.get("target_fs_usage_to_limit_ratio_delta"),
        "disk_io_increased": infra_comparison.get("disk_io_increased"),
        "disk_usage_increased": infra_comparison.get("disk_usage_increased"),
        "disk_pressure_detected_after": infra_comparison.get("disk_pressure_detected_after"),
    }

    return {
        "normalized_action": normalized_action,
        "action_type": (
            normalized_action.get("action_type")
            if isinstance(normalized_action, dict)
            else feedback.get("plan_action")
        ),
        "target_changes": {
            "type": target_changes.get("type"),
            "previous_value": target_changes.get("previous_value"),
            "target_value": target_changes.get("target_value"),
        },
        "before": before,
        "after": after,
        "changes": changes,
    }


def build_outcome_summary(feedback: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compact outcome block aligned with fault-aware feedback.
    This is what should be easy to inspect in result.json and retrieved examples.
    """
    feedback = feedback or {}
    return {
        "FRQ": feedback.get("FRQ"),
        "RQ": feedback.get("RQ"),
        "SHS_before": feedback.get("SHS_before"),
        "SHS_after": feedback.get("SHS_after"),
        "delta_SHS": feedback.get("delta_SHS"),
        "PS": feedback.get("PS"),
        "ES": feedback.get("ES"),
        "reward": feedback.get("reward"),
        "resource_cost": feedback.get("resource_cost"),
        "resource_penalty": feedback.get("resource_penalty"),
        "primary_metric_fixed": feedback.get("primary_metric_fixed"),
        "primary_metrics": feedback.get("primary_metrics"),
        "improved_metrics": feedback.get("improved_metrics"),
        "degraded_metrics": feedback.get("degraded_metrics"),
        "fault_recovery_components": feedback.get("fault_recovery_components"),
        "fault_recovery_weights": feedback.get("fault_recovery_weights"),
        "recovery_success": feedback.get("recovery_success"),
        "fault_success_reason": feedback.get("fault_success_reason"),
        "regression": feedback.get("regression"),
            "fault_success_reason": feedback.get("fault_success_reason"),
    }

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


def strip_timestamps(data: dict) -> dict:
    for key, items in data.items():
        if not isinstance(items, list):
            continue

        for item in items:
            if "value" in item and isinstance(item["value"], list):
                item["value"] = float(item["value"][1])
    return data


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


def n(metrics: dict, infra_state: dict, infra_comparison: Optional[dict] = None) -> dict:
    """
    Keep application/system/infrastructure metrics together for the LLM.
    This avoids hiding root-cause signals such as unavailable replicas,
    ImagePullBackOff, OOMKilled, throttling, and node pressure.
    """
    enriched = dict(metrics or {})
    enriched["infrastructure_state"] = infra_state or {}
    if infra_comparison is not None:
        enriched["infrastructure_comparison"] = infra_comparison
    return enriched


def write_json(path: Path, obj: Any) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)

def execution_required_from_plan(plan: dict) -> tuple[bool, str]:
    """
    Decide whether executor should run based on planner output.
    Defaults to True for backward compatibility.
    """
    if not isinstance(plan, dict):
        return True, "invalid_plan_default_execute"

    required = plan.get("execution_required", True)

    if isinstance(required, str):
        required = required.strip().lower() in ["true", "yes", "1"]

    reason = plan.get("execution_reason", "")
    strategy = str(plan.get("strategy", "")).lower()

    noop_keywords = [
        "monitor",
        "no action",
        "no_action",
        "self-healed",
        "self healed",
        "already recovered",
        "no remediation",
    ]

    if required is False:
        return False, reason or "planner_marked_no_execution"

    if any(k in strategy for k in noop_keywords):
        return False, reason or "noop_strategy_detected"

    return True, reason or "planner_marked_execution_required"

APP_TO_NAMESPACE = {
    "robot-shop": "robot-shop",
    "sock-shop": "sock-shop",
    "online-boutique": "online-boutique",
}


def run_single_experiment(
    fault_type: str,
    service: str,
    duration: str,
    client: str,
    model_id: str,
    api_key: str,
    endpoint: str,
    temperature: float,
    max_tokens: int,
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
    use_normalized_feedback: bool = False,  # kept for backward-compatible signature; no longer used
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
    """
    Run one EV-AIM experiment, save local/global summaries, and return the result dict.
    """
    namespace = namespace or APP_TO_NAMESPACE.get(app, app)

    fault = {
        "type": fault_type,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment or service,
    }

    optional_fault_fields = {
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
    }
    for key, value in optional_fault_fields.items():
        if value is not None and value != "":
            fault[key] = value

    model_config = ModelConfig(
        client=client,
        model_id=model_id,
        api_key=api_key,
        endpoint=endpoint,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    run_id = f"llm-{namespace}-{fault_type}-{service}-{int(time.time())}"
    exp_result_dir = exp_results_path / run_id
    exp_result_dir.mkdir(parents=True, exist_ok=True)

    print("\n========== EV-AIM SINGLE EXPERIMENT ==========")
    print(f"[INFO] Run ID: {run_id}")
    print(f"[INFO] App={app}, namespace={namespace}, service={service}, deployment={fault['deployment']}")
    print(f"[INFO] Fault type={fault_type}, duration={duration}")
    print(f"[INFO] Results directory: {exp_result_dir}")

    result = run_experiment(
        fault=fault,
        model_config=model_config,
        exp_dir=exp_result_dir,
        metrics_to_fetch=metrics_to_fetch,
        use_normalized_feedback=use_normalized_feedback,
        slo_thresholds=slo_thresholds,
    )

    summary_jsonl_file_path = exp_result_dir / "summary.jsonl"
    with open(summary_jsonl_file_path, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")
    print(f"[INFO] Experiment result appended to {summary_jsonl_file_path}")

    summary_csv_file_path = exp_result_dir / "summary.csv"
    flat_result = flatten_for_csv("", result)
    with open(summary_csv_file_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat_result.keys()))
        writer.writeheader()
        writer.writerow({k: json_safe(v) for k, v in flat_result.items()})
    print(f"[INFO] Experiment result saved to {summary_csv_file_path}")

    global_csv_path = exp_results_path / "all_experiments_summary.csv"
    append_experiment_to_global_csv(result, global_csv_path)
    print(f"[INFO] Global summary updated: {global_csv_path}")

    return result
# -----------------------------
# Main Experiment Function
# -----------------------------
def run_experiment(fault: dict, model_config: ModelConfig, exp_dir: Path, metrics_to_fetch: list[str],
                   use_normalized_feedback: bool = False,
                   slo_thresholds: Optional[Dict[str, float]] = None):

    tracker = LatencyTracker()
    tracker.mark("detect")

    service = fault["service"]
    fault_type = fault["type"]
    app = fault.get("app", "robot-shop")
    namespace = fault.get("namespace") or fault.get("target_namespace") or app
    deployment = fault.get("deployment", service)

    fault_id = f"custom-{fault_type}-{service}-{int(time.time())}"

    fault_duration = fault.get("duration", 120)
    if isinstance(fault_duration, str):
        fault_duration = int(fault_duration)

    MIN_FAULT_DURATION = 60
    if fault_duration < MIN_FAULT_DURATION:
        raise ValueError(
            f"Fault duration must be >= {MIN_FAULT_DURATION}s for meaningful metrics "
            f"(got {fault_duration}s)"
        )

    metric_cfg = fault.get("metric_collection", {})
    metric_groups = metric_groups_from_config(metric_cfg)
    observe_services = metric_cfg.get("observe_services") or [fault["service"]]

    fault_init_wait = timing_from_config(metric_cfg, "fault_init_wait_seconds", FAULT_INIT_WAIT)
    fault_observation_wait = timing_from_config(
        metric_cfg,
        "fault_observation_wait_seconds",
        METRIC_SCRAPING_BUFFER,
    )
    warmup_seconds = timing_from_config(metric_cfg, "warmup_seconds", WARMUP_PERIOD)
    rollout_timeout = timing_from_config(metric_cfg, "rollout_timeout_seconds", ROLLOUT_TIMEOUT)
    recovery_wait = int(metric_cfg.get("recovery_wait_seconds", 30))

    before_window = build_collection_window(metric_cfg, "before")
    # fault_window = build_collection_window(metric_cfg, "fault")
    after_window = build_collection_window(metric_cfg, "after")

    baseline_window = CollectionWindow(
                                        lookback_seconds=int(metric_cfg.get("baseline_lookback_seconds", 120)),
                                        step_seconds=int(metric_cfg.get("baseline_step_seconds", 15)),
                                        rate_interval=str(metric_cfg.get("rate_interval", "30s")),
                                    )

    effective_config = {
        "fault": fault,
        "metrics_requested": metrics_to_fetch,
        "metric_groups_used": metric_groups,
        "observe_services_used": observe_services,
        "fault_init_wait_seconds": fault_init_wait,
        "fault_observation_wait_seconds": fault_observation_wait,
        "warmup_seconds": warmup_seconds,
        "rollout_timeout_seconds": rollout_timeout,
        "before_window": before_window.__dict__,
        # "fault_window": fault_window.__dict__,
        "after_window": after_window.__dict__,
        "baseline_window": baseline_window.__dict__,
    }
    write_json(exp_dir / "effective_config.json", effective_config)

    print("\n========== EV-AIM PLANNER-ONLY TEST ==========")
    print(f"[INFO] Fault ID: {fault_id}")
    print(f"[INFO] App: {app}")
    print(f"[INFO] Namespace: {namespace}")
    print(f"[INFO] Service: {service}")
    print(f"[INFO] Deployment: {deployment}")
    print(f"[INFO] Fault type: {fault_type}")
    print(f"[INFO] Fault duration: {fault_duration}s")
    print(f"[INFO] Metrics requested: {metrics_to_fetch}")
    print(f"[INFO] Metric groups used: {metric_groups}")
    print(f"[INFO] Observed services: {observe_services}")
    print(f"[INFO] Before window: lookback={before_window.lookback_seconds}s, step={before_window.step_seconds}s")
    # print(f"[INFO] Fault window: lookback={fault_window.lookback_seconds}s, step={fault_window.step_seconds}s")
    print(f"[INFO] After window: lookback={after_window.lookback_seconds}s, step={after_window.step_seconds}s")
    print(f"[INFO] Baseline window: lookback={baseline_window.lookback_seconds}s, step={baseline_window.step_seconds}s")
    print(f"[INFO] Fault init wait: {fault_init_wait}s")
    print(f"[INFO] Metric ingestion wait: {fault_observation_wait}s")
    print(f"[INFO] Effective config saved to: {exp_dir / 'effective_config.json'}")

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
    print(f"[INFO] Injecting {fault_type} into service={service}, deployment={deployment}, namespace={namespace}")
    fault_injection_result = inject_fault(fault)
    tracker.mark("fault_injected")

    with open(exp_dir / "fault_injection_result.json", "w") as f:
        json.dump(fault_injection_result, f, indent=2)
    print("[INFO] Fault injection result saved to fault_injection_result.json")
    # print(f"[INFO] Fault injection result: {fault_injection_result}")

    print("\n[STAGE 2] Waiting for fault initialization...")
    print(f"[WAIT] Sleeping {fault_init_wait}s to allow kubectl/Locust/stress process to initialize.")
    time.sleep(fault_init_wait)
    tracker.mark("fault_initialized")
    print("[INFO] Fault initialization wait completed.")

    print("\n[STAGE 3] Waiting for metric ingestion...")
    print(f"[WAIT] Sleeping {fault_observation_wait}s so metrics are scraped and queryable.")
    print("[INFO] Fault is expected to still be active during this period.")
    time.sleep(fault_observation_wait)
    tracker.mark("metrics_available")
    print("[INFO] Metric ingestion wait completed.")

    print("\n[STAGE 4] Collecting pre-mitigation/fault-stage metrics...")
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
        metric_groups=metric_groups
    )

    tracker.mark("metrics_before")
    with open(exp_dir / "metrics_before.json", "w") as f:
        json.dump(metrics_before, f, indent=2)
    print("[INFO] Pre-mitigation metrics saved to metrics_before.json")

    print("\n[STAGE 5] Building infrastructure snapshot...")
    infra_state_before = build_infrastructure_snapshot(metrics_before, target_service=service)
    with open(exp_dir / "infrastructure_before.json", "w") as f:
        json.dump(infra_state_before, f, indent=2)
    print("[INFO] Infrastructure snapshot saved to infrastructure_before.json")
    print("[INFO] Infrastructure snapshot summary:")
    print_infrastructure_snapshot(infra_state_before)
    tracker.mark("infra_state_before")

    print("\n[STAGE 6] Building planner metrics/context...")
    planner_context = build_planner_metrics(metrics=metrics_before, infra_state=infra_state_before)

    write_json(exp_dir / "planner_metrics_before.json", planner_context)
    tracker.mark("planner_context_built")

    print("[INFO] Planner metrics saved to planner_metrics_before.json")
    print("[INFO] Planner context is ready for mitigation planning.")

    print("\n[STAGE 7] Retrieving past experience for planner ICL...")

    experience_records = []
    
    # retrieve_experience(
    #     namespace=namespace,
    #     fault_type=fault_type,
    #     metrics=metrics_before,
    #     service=service,
    #     top_k=5,
    #     retrieval_mode="evaim",
    # )

    experience_prompt = format_for_prompt(experience_records)

    tracker.mark("experience_retrieved")
    planner_icl_samples = len(experience_records)
    print(f"[INFO] Retrieved planner ICL examples: {planner_icl_samples}")

    print("\n[STAGE 8] Generating mitigation plan via LLM...")
    print(f"[INFO] Planner model: {model_config.model_id}")
    print(f"[INFO] Fault type given to planner: {fault_type}")
    print(f"[INFO] Service given to planner: {service}")

    planner = MitigationPlanner.from_config(model_config.to_dict())
    plan = planner.plan(
        fault_type=fault_type,
        exp_dir=exp_dir,
        metrics=planner_context,
        service=service,
        experience=experience_prompt
    )
    tracker.mark("plan_generated")
    with open(exp_dir / "plan.json", "w") as f:
        json.dump(plan, f, indent=2)

    print("[INFO] Mitigation plan generated successfully.")
    print(f"[INFO] Plan saved to: {exp_dir / 'plan.json'}")
    print("[INFO] Generated plan:")
    print(json.dumps(plan, indent=2))

    print("\n[STAGE 9-1] Checking whether remediation execution is required...")
    execution_required, execution_reason = execution_required_from_plan(plan)
    write_json(exp_dir / "execution_decision.json", {
        "execution_required": execution_required,
        "execution_reason": execution_reason,
        "plan_strategy": plan.get("strategy"),
        "plan_severity": plan.get("severity"),
        "target_changes": plan.get("target_changes"),
    })
    print(f"[INFO] execution_required={execution_required}")
    print(f"[INFO] execution_reason={execution_reason}")

    if not execution_required:
        print("[INFO] Planner selected no-op/monitoring strategy. Skipping executor.")
        print("[INFO] No remediation executor will run.")

        exec_status = "skipped_noop"
        exec_error = None
        stdout = ""
        playbook_yaml = ""
        attempt = 0
        icl_examples = []
        executor_icl_samples = 0
        rollout_result = {
            "rollout_completed": None,
            "rollout_duration_seconds": None,
            "timeout_occurred": None,
            "final_pod_count": None,
        }
        ansible_recap = None

        print("[INFO] Waiting warmup interval before no-op post-observation.")
        if warmup_seconds > 0:
            print(f"[WAIT] Sleeping {warmup_seconds}s before collecting post-no-op metrics.")
            time.sleep(warmup_seconds)

        print("\n[STAGE 9-2-1] Collecting post-decision metrics for no-op plan...")
        metrics_after = collect_multi_service_observation(
            prometheus_url=PROMETHEUS_URL,
            fault=fault,
            services=observe_services,
            window=after_window,
            metric_groups=metric_groups,
        )
        tracker.mark("metrics_after")
        write_json(exp_dir / "metrics_after.json", metrics_after)

        print("\n[STAGE 9-2-2] Capturing post-decision infrastructure state...")
        infra_state_after = build_infrastructure_snapshot(metrics_after, target_service=service)
        write_json(exp_dir / "infrastructure_after.json", infra_state_after)
        infra_comparison = compare_infrastructure_states(infra_state_before, infra_state_after)
        write_json(exp_dir / "infrastructure_comparison.json", infra_comparison)

        print("\n[STAGE 9-2-3] Computing unified EV-AIM feedback for no-op plan...")
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
            execution_required=False,
            execution_status=exec_status,
            execution_error=exec_error,
            rollout_result=rollout_result,
            ansible_log=stdout,
            playbook_retries=attempt,
            slo_thresholds=slo_thresholds,
        )
        write_json(exp_dir / "feedback.json", feedback)

        target_changes_summary = build_target_changes_summary(
            infra_before=infra_state_before,
            infra_after=infra_state_after,
            infra_comparison=infra_comparison,
            feedback=feedback,
            plan=plan,
        )
        outcome_summary = build_outcome_summary(feedback)
        write_json(exp_dir / "target_changes_summary.json", target_changes_summary)
        write_json(exp_dir / "outcome_summary.json", outcome_summary)

        tracker.mark("feedback_computed")
        print("[INFO] Unified feedback saved to feedback.json")
        print("[INFO] Target before/after change summary saved to target_changes_summary.json")
        print("[INFO] Fault-aware outcome summary saved to outcome_summary.json")
        print("\n========== FEEDBACK SUMMARY ==========")
        print(f"SHS:      {feedback['SHS_before']:.3f} -> {feedback['SHS_after']:.3f}")
        print(f"ΔSHS:     {feedback['delta_SHS']:.3f}")
        print(f"RQ:       {feedback['RQ']:.3f}")
        if feedback.get("FRQ") is not None:
            print(f"FRQ:      {feedback['FRQ']:.3f}")
        print(f"Reward:   {feedback['reward']:.3f}")
        print(f"PS/ES:    {feedback['PS']:.2f}/{feedback['ES']:.2f}")
        print(f"Action:   {feedback['plan_action']}")
        if feedback.get("normalized_action"):
            print(f"NormAct:  {feedback['normalized_action']}")
        print(f"Success:  {feedback['recovery_success']}")
        print(f"Cost:     {feedback['resource_cost']:.3f}")
        print("======================================\n")
        comparison_rows = build_paper_comparison_rows(
            fault_id=fault_id,
            method="llm",
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
            command_result={
                "status": exec_status,
                "error": exec_error,
                "stdout": stdout,
                "ansible_recap": ansible_recap,
            },
        )

        write_rows_csv(exp_dir / "paper_comparison.csv", comparison_rows)

        store_unified_experience(
                                incident={
                                    "fault_id": fault_id,
                                    "app": app,
                                    "namespace": namespace,
                                    "service": service,
                                    "deployment": deployment,
                                    "fault_type": fault_type,
                                },
                                plan=plan,
                                playbook_yaml="",
                                metrics_before=metrics_before,
                                metrics_after=metrics_after,
                                infrastructure_before=infra_state_before,
                                infrastructure_after=infra_state_after,
                                infrastructure_comparison=infra_comparison,
                                feedback=feedback,
                                execution_status=exec_status,
                                execution_error=exec_error,
                                ansible_stdout=stdout,
                                ansible_recap=ansible_recap,
                                rollout_result=rollout_result,
                                playbook_retries=attempt,
                                exp_dir=str(exp_dir),
                            )
        tracker.mark("experience_stored")

        print("\n[STAGE 9-2-4] Recovering injected fault before return stop...")
        print("[INFO] Running recover_fault() so the system returns to normal state.")

        recovery_wait = int(metric_cfg.get("recovery_wait_seconds", 30))

        try:
            if background_load_result:
                try:
                    print("\n[CLEANUP] Stopping experiment background/baseline traffic...")
                    stop_load(background_load_result["app"])
                except Exception as e:
                    print(f"[WARNING] Could not stop experiment traffic: {type(e).__name__}: {e}")
            recovery_result = recover_fault(fault, fault_injection_result)
        except Exception as e:
            recovery_result = {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
            }

        write_json(exp_dir / "final_recovery.json", recovery_result)

        if recovery_wait > 0:
            print(f"[WAIT] Sleeping {recovery_wait}s after recovery for stabilization.")
            time.sleep(recovery_wait)

        tracker.mark("executor_skipped")
        latencies = tracker.summary()
        write_json(exp_dir / "latencies.json", latencies)

        print("[INFO] Recovery result:")
        # print(json.dumps(recovery_result, indent=2, default=str))

        result = {
            "app": app,
            "namespace": namespace,
            "service": service,
            "deployment": deployment,
            "fault_type": fault_type,
            "fault_id": fault_id,

            "latencies": latencies,

            "metrics_before": metrics_before,
            "metrics_after": metrics_after,

            "planner_icl_samples": planner_icl_samples,
            "executor_icl_samples": executor_icl_samples,

            # New primary outcome metrics
            "SHS_before": feedback.get("SHS_before"),
            "SHS_after": feedback.get("SHS_after"),
            "delta_SHS": feedback.get("delta_SHS"),
            "RQ": feedback.get("RQ"),
            "FRQ": feedback.get("FRQ"),
            "primary_metric_fixed": feedback.get("primary_metric_fixed"),
            "primary_metrics": feedback.get("primary_metrics"),
            "improved_metrics": feedback.get("improved_metrics"),
            "degraded_metrics": feedback.get("degraded_metrics"),
            "fault_recovery_components": feedback.get("fault_recovery_components"),
            "fault_recovery_weights": feedback.get("fault_recovery_weights"),
            "reward": feedback.get("reward"),
            "resource_cost": feedback.get("resource_cost"),
            "resource_penalty": feedback.get("resource_penalty"),

            # Diagnostic attribution metrics
            "PS": feedback.get("PS"),
            "ES": feedback.get("ES"),
            "plan_action": feedback.get("plan_action"),
            "normalized_action": feedback.get("normalized_action"),
            "action_type": (
                feedback.get("normalized_action", {}).get("action_type")
                if isinstance(feedback.get("normalized_action"), dict)
                else feedback.get("plan_action")
            ),
            "expected_actions": feedback.get("expected_actions"),
            "code_changed_system": feedback.get("code_changed_system"),
            "execution_failure_reason": feedback.get("execution_failure_reason"),

            # Binary health outcomes
            "recovery_success": feedback.get("recovery_success"),
            "regression": feedback.get("regression"),
            "fault_success_reason": feedback.get("fault_success_reason"),

            # Execution fields
            "execution_required": execution_required,
            "execution_reason": execution_reason,
            "execution_status": exec_status,
            "execution_error": exec_error,
            "playbook_retries": attempt,

            # Rollout fields
            "rollout_completed": rollout_result.get("rollout_completed"),
            "rollout_duration_seconds": rollout_result.get("rollout_duration_seconds"),
            "rollout_timeout_occurred": rollout_result.get("timeout_occurred"),

            # Infrastructure fields
            "pod_count_before": infra_state_before.get("namespace_running_pods"),
            "pod_count_after": infra_state_after.get("namespace_running_pods"),
            "pod_count_delta": infra_comparison.get("namespace_running_pods_delta"),
            "scale_out_occurred": infra_comparison.get("scale_out_occurred"),
            "scale_up_occurred": infra_comparison.get("scale_up_occurred"),
            "cpu_limit_per_pod_before_millicores": infra_state_before.get("cpu_limit_per_pod_millicores"),
            "cpu_limit_per_pod_after_millicores": infra_state_after.get("cpu_limit_per_pod_millicores"),
            "memory_limit_per_pod_before_bytes": infra_state_before.get("memory_limit_per_pod_bytes"),
            "memory_limit_per_pod_after_bytes": infra_state_after.get("memory_limit_per_pod_bytes"),

            "target_changes_summary": target_changes_summary,
            "outcome_summary": outcome_summary,

            "infrastructure_before": infra_state_before,
            "infrastructure_after": infra_state_after,
            "infrastructure_comparison": infra_comparison,

            "ansible_recap": ansible_recap,
            "final_recovery": recovery_result,
            "experiment_dir": str(exp_dir),
            "method": "llm",
            "metrics_baseline": metrics_baseline,
            "infrastructure_baseline": infra_state_baseline,
            "paper_comparison_csv": str(exp_dir / "paper_comparison.csv"),
        }

        write_json(exp_dir / "result.json", result)
        print("\n[SUCCESS] No remediation executor experiment completed with planner-only no-op strategy.")
        print(f"[INFO] Result saved to {exp_dir / 'result.json'}")
        return result
    
    

    print("[INFO] Planner selected executable remediation. Continuing to executor.")
   
    # 6. Generate playbook
    print("\n[STAGE 10] Generating Ansible playbook...")
    MAX_RETRIES = 5

    executor = AnsibleExecutor.from_config(model_config.to_dict())

    icl_examples = []
    
    # retrieve_icl_examples(
    #     service=service,
    #     namespace=namespace,
    #     fault_type=fault_type,
    #     plan=plan,
    #       retrieval_mode = "evaim",
    # )

    executor_icl_samples = len(icl_examples) if icl_examples else 0
    tracker.mark("executor_icl_retrieved")
    print(f"[INFO] Retrieved executor ICL examples: {executor_icl_samples}")

    playbook_yaml = executor.generate_playbook(
        plan=plan,
        service=service,
        namespace=namespace,
        exp_dir=exp_dir,
        icl_examples=icl_examples,
    )

    tracker.mark("playbook_generated")

    playbook_path = exp_dir / "playbook.yaml"

    with open(exp_dir / "playbook_raw_llm_response.yaml", "w") as f:
        f.write(str(playbook_yaml))
    print(f"[INFO] Raw playbook response saved to {exp_dir / 'playbook_raw_llm_response.yaml'}")

    exec_status = "error"
    exec_error = None
    stdout = ""
    feedback = None
    attempt = 0
    last_fixed_playbook_yaml = ""

    # 11. Execute playbook with repair loop
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n[STAGE 11] Executing Ansible playbook attempt {attempt}/{MAX_RETRIES}")

        try:
            if playbook_yaml is None:
                raise ValueError("LLM returned None for playbook_yaml")

            if not isinstance(playbook_yaml, str):
                raise TypeError(f"playbook_yaml must be string, got {type(playbook_yaml)}")

            if not playbook_yaml.strip():
                raise ValueError("LLM returned empty playbook_yaml")

            cleaned_playbook_yaml = strip_markdown_fences(playbook_yaml)
            fixed_playbook_yaml = fix_playbook_types(cleaned_playbook_yaml)

            playbook_yaml = fixed_playbook_yaml
            last_fixed_playbook_yaml = fixed_playbook_yaml

            # print("[INFO] Validating generated playbook...")
            # validate_ansible_playbook(fixed_playbook_yaml)
            # print("[INFO] Playbook validation passed.")

            with open(exp_dir / f"playbook_attempt_{attempt}.yaml", "w") as f:
                f.write(fixed_playbook_yaml)

            with open(playbook_path, "w") as f:
                f.write(fixed_playbook_yaml)

            print(f"[INFO] Playbook written to: {playbook_path}")

        except Exception as e:
            print(f"[ERROR] Failed to process playbook: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

            exec_status = "error"
            exec_error = f"Playbook processing failed: {e}"
            break

        try:
            env = os.environ.copy()
            python_path = sys.executable

            print("[INFO] Running ansible-playbook...")
            result = subprocess.run(
                [
                    "ansible-playbook",
                    "playbook.yaml",
                    "-e",
                    f"ansible_python_interpreter={python_path}",
                ],
                cwd=exp_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            exec_status = "success"
            exec_error = None
            stdout = result.stdout

            print("[INFO] Ansible playbook executed successfully.")
            print(stdout)
            break

        except subprocess.CalledProcessError as e:
            exec_status = "error"
            exec_error = e.stderr or ""
            stdout = e.stdout or ""

            with open(exp_dir / f"ansible_stdout_attempt_{attempt}.log", "w") as f:
                f.write(stdout)

            with open(exp_dir / f"ansible_stderr_attempt_{attempt}.log", "w") as f:
                f.write(exec_error)

            print("[ERROR] Playbook failed.")
            print(exec_error)
            print(stdout)

            if attempt == MAX_RETRIES:
                print("[ERROR] Retry limit reached.")
                break

            print("[INFO] Sending failed playbook and error log to LLM for repair...")
            playbook_yaml = executor.regenerate_playbook(
                    service=service,
                    namespace=namespace,
                    plan=plan,
                    failed_yaml=playbook_yaml,
                    stdout=stdout,
                    error_log=exec_error,
                )
            print("[INFO] Repaired playbook generated. Retrying...")

    with open(exp_dir / "ansible_output.log", "w") as f:
        f.write(stdout)
        if exec_error:
            f.write("\n=== exec_error ===\n")
            f.write(exec_error)

    tracker.mark("playbook_executed")

    ansible_recap = parse_ansible_recap(stdout)
    write_json(exp_dir / "ansible_recap.json", ansible_recap)
    print("[INFO] Ansible recap parsed and saved to ansible_recap.json")
    print(json.dumps(ansible_recap, indent=2, default=str))


    # 12. Wait for rollout only if Ansible ran successfully; otherwise record skipped rollout.
    print("\n[STAGE 12] Waiting for deployment rollout after remediation...")
    if exec_status == "success":
        print(f"[WAIT] Waiting up to {rollout_timeout}s for deployment={deployment} in namespace={namespace}")
        rollout_result = wait_for_rollout_completion(
            service=deployment,
            namespace=namespace,
            timeout=rollout_timeout,
        )
    else:
        print("[WARNING] Execution did not succeed; rollout wait is skipped.")
        rollout_result = {
            "rollout_completed": False,
            "rollout_duration_seconds": None,
            "timeout_occurred": False,
            "final_pod_count": None,
            "skipped": True,
            "reason": "execution_failed",
        }

    tracker.mark("rollout_complete")
    write_json(exp_dir / "rollout_status.json", rollout_result)

    if not rollout_result.get("rollout_completed"):
        print(f"[WARNING] Rollout did not complete or was skipped. Result: {rollout_result}")
        failure_reasons = get_pod_failure_reasons(deployment, namespace)
        if failure_reasons:
            print(f"[ERROR] Pod failures detected: {failure_reasons}")
            write_json(exp_dir / "pod_failures.json", failure_reasons)
    else:
        print(f"[INFO] Rollout completed in {rollout_result.get('rollout_duration_seconds'):.1f}s")
        print(f"[INFO] Final ready pod count: {rollout_result.get('final_pod_count')}")

    # 8.5 Warmup
    print("\n[STAGE 13] Waiting for post-remediation warmup...")
    print(f"[WAIT] Sleeping {warmup_seconds}s for new pods/caches/connections to stabilize.")
    time.sleep(warmup_seconds)
    tracker.mark("warmup_complete")
    print("[INFO] Warmup completed.")

    # 9. Observe metrics after remediation
    after_window = build_collection_window(metric_cfg, "after")

    print("\n[STAGE 14] Collecting post-remediation metrics...")
    print(
        f"[INFO] Querying Prometheus: namespace={namespace}, service={service}, "
        f"lookback={after_window.lookback_seconds}s, step={after_window.step_seconds}s, "
        f"rate_interval={after_window.rate_interval}"
    )

    metrics_after = collect_multi_service_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        services=observe_services,
        window=after_window,
        metric_groups=metric_groups,
    )

    tracker.mark("metrics_after")

    with open(exp_dir / "metrics_after.json", "w") as f:
        json.dump(metrics_after, f, indent=2)

    print("[INFO] Post-remediation metrics saved to metrics_after.json")

    # 10. Infrastructure after
    print("\n[STAGE 15] Capturing post-remediation infrastructure state...")
    infra_state_after = build_infrastructure_snapshot(metrics_after, target_service=service)

    with open(exp_dir / "infrastructure_after.json", "w") as f:
        json.dump(infra_state_after, f, indent=2)

    print_infrastructure_snapshot(infra_state_after)

    infra_comparison = compare_infrastructure_states(infra_state_before, infra_state_after)

    with open(exp_dir / "infrastructure_comparison.json", "w") as f:
        json.dump(infra_comparison, f, indent=2)

    print("[INFO] Infrastructure comparison saved.")
    print(json.dumps(infra_comparison, indent=2, default=str))
    tracker.mark("infra_comparison_done")

    # 11. Feedback
    print("\n[STAGE 16] Computing unified EV-AIM feedback...")

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
        execution_required=execution_required,
        execution_status=exec_status,
        execution_error=exec_error,
        rollout_result=rollout_result,
        ansible_log=stdout,
        playbook_retries=attempt,
        slo_thresholds=slo_thresholds,
    )

    write_json(exp_dir / "feedback.json", feedback)

    target_changes_summary = build_target_changes_summary(
        infra_before=infra_state_before,
        infra_after=infra_state_after,
        infra_comparison=infra_comparison,
        feedback=feedback,
        plan=plan,
    )
    outcome_summary = build_outcome_summary(feedback)
    write_json(exp_dir / "target_changes_summary.json", target_changes_summary)
    write_json(exp_dir / "outcome_summary.json", outcome_summary)

    tracker.mark("feedback_computed")

    print("[INFO] Unified feedback saved to feedback.json")
    print("[INFO] Target before/after change summary saved to target_changes_summary.json")
    print("[INFO] Fault-aware outcome summary saved to outcome_summary.json")
    print("\n========== FEEDBACK SUMMARY ==========")
    print(f"SHS:      {feedback['SHS_before']:.3f} -> {feedback['SHS_after']:.3f}")
    print(f"ΔSHS:     {feedback['delta_SHS']:.3f}")
    print(f"RQ:       {feedback['RQ']:.3f}")
    if feedback.get("FRQ") is not None:
        print(f"FRQ:      {feedback['FRQ']:.3f}")
    print(f"Reward:   {feedback['reward']:.3f}")
    print(f"PS/ES:    {feedback['PS']:.2f}/{feedback['ES']:.2f}")
    print(f"Action:   {feedback['plan_action']}")
    if feedback.get("normalized_action"):
        print(f"NormAct:  {feedback['normalized_action']}")
    print(f"Success:  {feedback['recovery_success']}")
    print(f"Cost:     {feedback['resource_cost']:.3f}")
    print("======================================\n")

    comparison_rows = build_paper_comparison_rows(
        fault_id=fault_id,
        method="llm",
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
        command_result={
            "status": exec_status,
            "error": exec_error,
            "stdout": stdout,
            "ansible_recap": ansible_recap,
        },
    )

    write_rows_csv(exp_dir / "paper_comparison.csv", comparison_rows)

    # 12. Store experience
    print("\n[STAGE 17] Storing planner and executor experience...")

    store_unified_experience(
                        incident={
                            "fault_id": fault_id,
                            "app": app,
                            "namespace": namespace,
                            "service": service,
                            "deployment": deployment,
                            "fault_type": fault_type,
                        },
                        plan=plan,
                        playbook_yaml=last_fixed_playbook_yaml or playbook_yaml,
                        metrics_before=metrics_before,
                        metrics_after=metrics_after,
                        infrastructure_before=infra_state_before,
                        infrastructure_after=infra_state_after,
                        infrastructure_comparison=infra_comparison,
                        feedback=feedback,
                        execution_status=exec_status,
                        execution_error=exec_error,
                        ansible_stdout=stdout,
                        ansible_recap=ansible_recap,
                        rollout_result=rollout_result,
                        playbook_retries=attempt,
                        exp_dir=str(exp_dir),
                    )
    tracker.mark("experience_stored")

    print("[INFO] Experience stored.")

    # 13. Final cleanup
    print("\n[STAGE 18] Final fault cleanup/recovery...")
    print("[INFO] Running best-effort recovery to ensure cluster returns to normal state.")

    try:
        if background_load_result:
            try:
                print("\n[CLEANUP] Stopping experiment background/baseline traffic...")
                stop_load(background_load_result["app"])
            except Exception as e:
                print(f"[WARNING] Could not stop experiment traffic: {type(e).__name__}: {e}")
        recovery_result = recover_fault(fault, fault_injection_result)
    except Exception as e:
        recovery_result = {
            "status": "error",
            "error": str(e),
        }

    write_json(exp_dir / "final_recovery.json", recovery_result)
    print("[INFO] Final recovery result saved to final_recovery.json")
    # print(json.dumps(recovery_result, indent=2, default=str))
    tracker.mark("final_recovery_done")
    if recovery_wait > 0:
            print(f"[WAIT] Sleeping {recovery_wait}s after final recovery for stabilization.")
            time.sleep(recovery_wait)

    # 14. Final result
    print("\n[SUCCESS] Experiment completed.")

    latencies = tracker.summary()
    write_json(exp_dir / "latencies.json", latencies)
   

    result = {
            "app": app,
            "namespace": namespace,
            "service": service,
            "deployment": deployment,
            "fault_type": fault_type,
            "fault_id": fault_id,

            "latencies": latencies,

            "metrics_before": metrics_before,
            "metrics_after": metrics_after,

            "planner_icl_samples": planner_icl_samples,
            "executor_icl_samples": executor_icl_samples,


            # New primary outcome metrics
            "SHS_before": feedback.get("SHS_before"),
            "SHS_after": feedback.get("SHS_after"),
            "delta_SHS": feedback.get("delta_SHS"),
            "RQ": feedback.get("RQ"),
            "FRQ": feedback.get("FRQ"),
            "primary_metric_fixed": feedback.get("primary_metric_fixed"),
            "primary_metrics": feedback.get("primary_metrics"),
            "improved_metrics": feedback.get("improved_metrics"),
            "degraded_metrics": feedback.get("degraded_metrics"),
            "fault_recovery_components": feedback.get("fault_recovery_components"),
            "fault_recovery_weights": feedback.get("fault_recovery_weights"),
            "reward": feedback.get("reward"),
            "resource_cost": feedback.get("resource_cost"),
            "resource_penalty": feedback.get("resource_penalty"),

            # Diagnostic attribution metrics
            "PS": feedback.get("PS"),
            "ES": feedback.get("ES"),
            "plan_action": feedback.get("plan_action"),
            "normalized_action": feedback.get("normalized_action"),
            "action_type": (
                feedback.get("normalized_action", {}).get("action_type")
                if isinstance(feedback.get("normalized_action"), dict)
                else feedback.get("plan_action")
            ),
            "expected_actions": feedback.get("expected_actions"),
            "code_changed_system": feedback.get("code_changed_system"),
            "execution_failure_reason": feedback.get("execution_failure_reason"),

            # Binary health outcomes
            "recovery_success": feedback.get("recovery_success"),
            "regression": feedback.get("regression"),
            "fault_success_reason": feedback.get("fault_success_reason"),

            # Execution fields
            "execution_required": execution_required,
            "execution_reason": execution_reason,
            "execution_status": exec_status,
            "execution_error": exec_error,
            "playbook_retries": attempt,

            # Rollout fields
            "rollout_completed": rollout_result.get("rollout_completed"),
            "rollout_duration_seconds": rollout_result.get("rollout_duration_seconds"),
            "rollout_timeout_occurred": rollout_result.get("timeout_occurred"),

            # Infrastructure fields
            "pod_count_before": infra_state_before.get("namespace_running_pods"),
            "pod_count_after": infra_state_after.get("namespace_running_pods"),
            "pod_count_delta": infra_comparison.get("namespace_running_pods_delta"),
            "scale_out_occurred": infra_comparison.get("scale_out_occurred"),
            "scale_up_occurred": infra_comparison.get("scale_up_occurred"),
            "cpu_limit_per_pod_before_millicores": infra_state_before.get("cpu_limit_per_pod_millicores"),
            "cpu_limit_per_pod_after_millicores": infra_state_after.get("cpu_limit_per_pod_millicores"),
            "memory_limit_per_pod_before_bytes": infra_state_before.get("memory_limit_per_pod_bytes"),
            "memory_limit_per_pod_after_bytes": infra_state_after.get("memory_limit_per_pod_bytes"),

            "target_changes_summary": target_changes_summary,
            "outcome_summary": outcome_summary,

            "infrastructure_before": infra_state_before,
            "infrastructure_after": infra_state_after,
            "infrastructure_comparison": infra_comparison,

            "ansible_recap": ansible_recap,
            "final_recovery": recovery_result,
            "experiment_dir": str(exp_dir),
            "method": "llm",
            "metrics_baseline": metrics_baseline,
            "infrastructure_baseline": infra_state_baseline,
            "paper_comparison_csv": str(exp_dir / "paper_comparison.csv"),
        }
    return result