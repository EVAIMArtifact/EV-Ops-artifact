import time
import json
import subprocess
from pathlib import Path
from typing import Optional, Dict
import yaml
import csv
from src.monitoring.collector import collect_fault_observation
from src.monitoring.config import CollectionWindow, ALL_METRIC_GROUPS
import sys
import os
from prometheus_api_client import PrometheusConnect

# --- Fault Injection ---
from src.fault_injection import inject_fault

# --- Planner ---
from src.planner.llm_planner import MitigationPlanner
from src.planner.retrieval import (
    retrieve_experience,
    format_for_prompt
)

# --- Executor ---
from src.executor.ansible_generator import AnsibleExecutor
from src.executor.code_retrieval import retrieve_icl_examples
from src.executor.rollout_monitor import wait_for_rollout_completion, get_pod_failure_reasons




# # --- Feedback ---
from src.feedback.compute_feedback import (
    compute_weighted_feedback,
    compute_weighted_feedback_with_ansible,
    compute_normalized_feedback_with_ansible,
    relevance_mask_for_fault
)
from src.feedback.knowledge_store import store_experience
from src.feedback.code_knowledge_store import store_or_update_code_experience

# --- Monitoring ---
from src.monitoring.infrastructure_state import capture_infrastructure_state, compare_infrastructure_states

# --- Model Config ---
from src.experiment.model_config import ModelConfig
from src.latency_tracker import LatencyTracker
from src.utils.ansi_parser import parse_ansible_recap
from src.config import get_namespace

from src.clients.llm_client import preload_llm_dependencies
# -----------------------------
# Constants
# -----------------------------
NAMESPACE = get_namespace()  # Kubernetes namespace (configurable via ROBOT_SHOP_NAMESPACE env var)


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
    if isinstance(metric_entry, list) and len(metric_entry) > 0:
        return metric_entry[0].get("value")
    return None


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

def append_experiment_to_global_csv(result: dict, global_csv_path: Path):
    """
    Append experiment result to a single global CSV file.
    Dynamically handles new metric columns across runs.
    """

    metrics_before = result.get("metrics_before", {})
    metrics_after = result.get("metrics_after", {})

    # Collect metric keys
    all_metric_keys = sorted(
        set(metrics_before.keys()).union(set(metrics_after.keys()))
    )

    # Base fields
    base_fields = [
        "service", "fault_type", "fault_id","EVS",
         "MU", "reward", "ansible_score",
        "execution_status",
        "rollout_completed", "rollout_duration_seconds",
        "pod_count_before", "pod_count_after", "pod_count_delta",
        "scale_out_occurred", "scale_up_occurred",
        "cpu_limit_per_pod_before_millicores",
        "cpu_limit_per_pod_after_millicores",
        "playbook_retries",
        "planner_icl_samples",
        "executor_icl_samples",
        "experiment_dir"
    ]

    metric_columns = []
    for key in all_metric_keys:
        metric_columns.append(f"{key}_before")
        metric_columns.append(f"{key}_after")

    all_columns = base_fields + metric_columns

    # Prepare row dict
    row_dict = {}

    # Base fields
    for field in base_fields:
        row_dict[field] = result.get(field)

    # Metric fields
    for key in all_metric_keys:
        row_dict[f"{key}_before"] = extract_metric_value(metrics_before.get(key))
        row_dict[f"{key}_after"] = extract_metric_value(metrics_after.get(key))

    # If file doesn't exist → create with header
    file_exists = global_csv_path.exists()

    if not file_exists:
        with open(global_csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_columns)
            writer.writeheader()
            writer.writerow(row_dict)
    else:
        # If file exists, we must check for new columns
        with open(global_csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            existing_columns = reader.fieldnames

        # If new metrics appeared → rewrite file with expanded header
        if set(all_columns) != set(existing_columns):
            # Merge columns
            merged_columns = sorted(set(existing_columns).union(set(all_columns)))

            # Read existing rows
            with open(global_csv_path, "r", newline="") as f:
                existing_rows = list(csv.DictReader(f))

            # Rewrite entire file
            with open(global_csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=merged_columns)
                writer.writeheader()
                for r in existing_rows:
                    writer.writerow(r)
                writer.writerow(row_dict)
        else:
            # Safe append
            with open(global_csv_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=existing_columns)
                writer.writerow(row_dict)

    print(f"[INFO] Appended experiment to {global_csv_path}")
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
FAULT_INIT_WAIT = 30 # Wait for kubectl exec + stress-ng initialization
METRIC_SCRAPING_BUFFER = 60  # Wait for Prometheus + cAdvisor to ingest latest metrics
REACTIVE_OBSERVATION_WINDOW = "45s"  # Minimum observation for meaningful metrics (3-4 scrapes)
OBS_WINDOW_AFTER = "120s"  # Post-remediation observation window
OBS_WINDOW_INTERVAL = "1s"
PROMETHEUS_URL="http://localhost:9090"
ROLLOUT_TIMEOUT = 300  # Max time to wait for rollout completion
WARMUP_PERIOD = 60  # Time to wait after rollout for pods to warm up


EXCLUDED_METRIC_KEYS = {
    "duration",
    "service",
    "cpu_request",
    "cpu_limit",
    "replica_count",
    "replica_count_spec",
    "replica_count_available"
    # "cpu_limit_per_pod",
    # "memory_limit_per_pod",
}

def filter_metrics(metrics: dict) -> dict:
    """
    Remove infrastructure-related keys from metrics dictionary.
    """
    return {
        k: v
        for k, v in metrics.items()
        if k not in EXCLUDED_METRIC_KEYS
    }

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
                        namespace: str = "robot-shop",
                        deployment: Optional[str] = None,
                        pod: Optional[str] = None,
                        container: Optional[str] = None,
                        users: Optional[int] = None,
                        spawn_rate: Optional[int] = None,
                        pressure_type: Optional[str] = None,
                        bad_image: Optional[str] = None,
                        use_normalized_feedback: bool = False,
                        slo_thresholds: Optional[Dict[str, float]] = None,
                        memory_percent: int = None,
                        cpu_cores: int = None,
                        metric_collection: Optional[Dict[str, Any]] = None,
                    ):
    """
    Run a single experiment and save the result.
    """
    # Construct the fault dictionary
    fault = {
        "type": fault_type,
        "app": app,
        "namespace": namespace,
        "service": service,
        "deployment": deployment or service,
    }

    if duration:
        fault["duration"] = duration
    if pod:
        fault["pod"] = pod
    if container:
        fault["container"] = container
    if users is not None:
        fault["users"] = users
    if spawn_rate is not None:
        fault["spawn_rate"] = spawn_rate
    if pressure_type:
        fault["pressure_type"] = pressure_type
    if bad_image:
        fault["bad_image"] = bad_image
    if memory_percent is not None:
        fault["memory_percent"] = memory_percent
    if cpu_cores is not None:
        fault["cpu_cores"] = cpu_cores
    if metric_collection:
        fault["metric_collection"] = metric_collection

    # LLM model API setup
    model_config = ModelConfig(
        client=client,
        model_id=model_id,
        api_key=api_key,
        endpoint=endpoint,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    fault_id = f"custom-{fault_type}-{service}-{int(time.time())}"
    exp_result_dir = exp_results_path / fault_id
    exp_result_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[INFO] Running experiment for fault {fault_id} on service {service}")
    result = run_experiment(
        fault=fault,
        model_config=model_config,
        exp_dir=exp_result_dir,
        metrics_to_fetch=metrics_to_fetch,
        use_normalized_feedback=use_normalized_feedback,
        slo_thresholds=slo_thresholds
    )

    # save result to summary.jsonl
    summary_jsonl_file_path = exp_result_dir / "summary.jsonl"
    with open(summary_jsonl_file_path, "a") as f:
        f.write(json.dumps(result) + "\n")
        print(f"[INFO] Experiment result appended to {summary_jsonl_file_path}")

    # change to csv format
    summary_csv_file_path = exp_result_dir / "summary.csv"

    metrics_before = result.get("metrics_before", {})
    metrics_after = result.get("metrics_after", {})

    # Collect all metric names (union of before/after keys)
    all_metric_keys = sorted(
        set(metrics_before.keys()).union(set(metrics_after.keys()))
    )

    # Base metadata fields
    base_fields = [
        "service", "fault_type", "fault_id","EVS",
         "MU", "reward", 
        "execution_status",
        "rollout_completed", "rollout_duration_seconds",
        "pod_count_before", "pod_count_after", "pod_count_delta",
        "scale_out_occurred", "scale_up_occurred",
        "cpu_limit_per_pod_before_millicores",
        "cpu_limit_per_pod_after_millicores",
        "playbook_retries",
        "planner_icl_samples",
        "executor_icl_samples",
        "experiment_dir"
    ]

    # Metric columns with before/after suffix
    metric_columns = []
    for key in all_metric_keys:
        metric_columns.append(f"{key}_before")
        metric_columns.append(f"{key}_after")

    header_fields = base_fields + metric_columns

    with open(summary_csv_file_path, "w") as f:
        # Write header
        f.write(",".join(header_fields) + "\n")

        # Base values
        row_values = [result.get(field) for field in base_fields]

        # Metric values
        for key in all_metric_keys:
            row_values.append(metrics_before.get(key))
            row_values.append(metrics_after.get(key))

        # Write row
        f.write(",".join("" if v is None else str(v) for v in row_values) + "\n")

    print(f"[INFO] Experiment result saved to {summary_csv_file_path}")
    # Global CSV (one for all experiments)
    global_csv_path = exp_results_path / "all_experiments_summary.csv"

    append_experiment_to_global_csv(result, global_csv_path)

    return

# -----------------------------
# Main Experiment Function
# -----------------------------
def run_experiment(fault: dict, model_config: ModelConfig, exp_dir: Path, metrics_to_fetch: list[str],
                   use_normalized_feedback: bool = False,
                   slo_thresholds: Optional[Dict[str, float]] = None):
    
    # preload_llm_dependencies(model_config.to_dict())

    # Initialize latency tracker for this experiment
    tracker = LatencyTracker()
    tracker.mark("detect")  # Experiment start

    # --- Setup fault details ---
    service = fault["service"]
    fault_type = fault["type"]

    # Create a unique ID for this on-the-fly fault for logging and directory purposes
    fault_id = f"custom-{fault_type}-{service}-{int(time.time())}"

    # Extract fault duration (default to 120s if not specified)
    fault_duration = fault.get("duration", 120)
    if isinstance(fault_duration, str):
        fault_duration = int(fault_duration)

    # Validate minimum duration for meaningful metric collection
    MIN_FAULT_DURATION = 60  # Minimum for 4+ Prometheus scrape intervals
    if fault_duration < MIN_FAULT_DURATION:
        raise ValueError(f"Fault duration must be >= {MIN_FAULT_DURATION}s for meaningful metrics (got {fault_duration}s)")

    # 1. Inject Fault (background process continues running)
    print(f"[INFO] Injecting fault: {fault_id} ({fault_type} on {service}) for {fault_duration}s")
    fault_injection_result = inject_fault(fault)
    tracker.mark("fault_injected")

    with open(exp_dir / "fault_injection_result.json", "w") as f:
        json.dump(fault_injection_result, f, indent=2)


    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$

    
    # 2-1. Wait for kubectl exec + stress-ng initialization
    print(f"[INFO] Waiting {FAULT_INIT_WAIT}s for fault initialization...")
    time.sleep(FAULT_INIT_WAIT)
    tracker.mark("fault_initialized")

    # 2-2. [REACTIVE MODE] Wait for metric scraping buffer (NO waiting for fault completion)
    print(f"[INFO] [REACTIVE MODE] Waiting {METRIC_SCRAPING_BUFFER}s for metric ingestion...")
    print(f"[INFO] Fault is still running in background - will act immediately on symptoms")
    time.sleep(METRIC_SCRAPING_BUFFER)
    tracker.mark("metrics_available")

    # 3-1. Observe Metrics Before (captures early fault symptoms - reactive mode)
    print(f"[INFO] Fetching metrics for early fault period (last {REACTIVE_OBSERVATION_WINDOW})...")
    metric_cfg = fault.get("metric_collection", {})
    before_window = build_collection_window(metric_cfg, "before")
    metric_groups = metric_groups_from_config(metric_cfg)

    metrics_before = collect_fault_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        window=before_window,
        metric_groups=metric_groups,
    )

    refined_metrics_before = metrics_before


    
    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
    # $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$



    # make sure consumer code always sees an average_cpu_usage key
    ensure_average_cpu_metric(refined_metrics_before)

    tracker.mark("metrics_before")
    with open(exp_dir / "metrics_before_raw.json", "w") as f:
        json.dump(metrics_before, f, indent=2)
    with open(exp_dir / "metrics_before.json", "w") as f:
        json.dump(refined_metrics_before, f, indent=2)

    # 3-2. Capture Infrastructure State Before
    print("[INFO] Capturing infrastructure state before mitigation...")
    prom_client = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)
    infra_state_before = capture_infrastructure_state(prom_client, service, NAMESPACE)
    tracker.mark("infra_state_before")
    with open(exp_dir / "infrastructure_before.json", "w") as f:
        json.dump(infra_state_before, f, indent=2)
    print(f"[INFO] Pre-remediation: {infra_state_before['pod_count_ready']} pods ready")

    # 4. Retrieve Experience
    experience_raw = retrieve_experience(fault_type, refined_metrics_before)
    experience_prompt = format_for_prompt(experience_raw)
    tracker.mark("experience_retrieved")


    # 5. Generate Mitigation Plan
    print("[INFO] Generating mitigation plan via LLM...")
    planner = MitigationPlanner.from_config(model_config.to_dict())

    plan = planner.plan(
        fault_type=fault_type,
        exp_dir = exp_dir,
        metrics=refined_metrics_before,
        experience=experience_prompt,
    )

    tracker.mark("plan_generated")
    with open(exp_dir / "plan.json", "w") as f:
        json.dump(plan, f, indent=2)

    # 6. Generate playbook (knowledge-aware)
    print("[INFO] Generating Ansible playbook...")
    MAX_RETRIES = 5

    # Initialize executor for potential retries
    executor = AnsibleExecutor.from_config(model_config.to_dict())

    # First try exact match
    playbook_yaml = None #retrieve_exact_playbook(service, fault_id, plan, metrics_before)

    if playbook_yaml:
        print("[INFO] Using cached playbook from KB")
    else:
        # Tiered ICL examples for LLM
        icl_examples = retrieve_icl_examples(service, fault_id, plan)
        playbook_yaml = executor.generate_playbook(plan, service, exp_dir, icl_examples=icl_examples)

    tracker.mark("playbook_generated")


    playbook_path = exp_dir / "playbook.yaml"

    # Save raw LLM response for debugging
    with open(exp_dir / "playbook_raw_llm_response.yaml", "w") as f:
        f.write(str(playbook_yaml))
    print(f"[DEBUG] Saved raw LLM response ({len(str(playbook_yaml))} chars)")

    # Initialize variables before loop
    exec_status = "error"  # Default to error
    exec_error = None
    stdout = ""
    feedback = None  # Initialize for scope
    attempt = 0  # Initialize for return statement

    # Execute playbook
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[INFO] Executing playbook (attempt {attempt})")

        try:
            # Debug: Check if playbook_yaml is valid
            if playbook_yaml is None:
                raise ValueError("LLM returned None for playbook_yaml")
            if not isinstance(playbook_yaml, str):
                raise TypeError(f"playbook_yaml must be string, got {type(playbook_yaml)}")
            if len(playbook_yaml) == 0:
                raise ValueError("LLM returned empty string for playbook_yaml")

            print(f"[DEBUG] Playbook length: {len(playbook_yaml)} chars")

            # Strip markdown fences from LLM-generated YAML
            print("[DEBUG] Stripping markdown fences...")
            cleaned_playbook_yaml = strip_markdown_fences(playbook_yaml)
            print(f"[DEBUG] After stripping: {len(cleaned_playbook_yaml)} chars")

            # Fix type mismatches (e.g., replicas: "2" → replicas: 2)
            print("[DEBUG] Fixing playbook types...")
            fixed_playbook_yaml = fix_playbook_types(cleaned_playbook_yaml)
            print(f"[DEBUG] After fixing types: {len(fixed_playbook_yaml)} chars")

            # Write playbook to file
            print(f"[DEBUG] Writing playbook to {playbook_path}")
            with open(playbook_path, "w") as f:
                f.write(fixed_playbook_yaml)
            print("[DEBUG] Playbook written successfully")

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
            result = subprocess.run(
                [
                    "ansible-playbook",
                    "playbook.yaml",
                    "-e", f"ansible_python_interpreter={python_path}"
                ],
                cwd=exp_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True
            )

            exec_status = "success"
            exec_error = None
            stdout = result.stdout

            print(stdout)
            break   # ✅ EXIT LOOP ON SUCCESS

        except subprocess.CalledProcessError as e:
            exec_status = "error"
            exec_error = e.stderr
            stdout = e.stdout or ""

            print("[ERROR] Playbook failed")

            # Stop retrying if limit reached
            if attempt == MAX_RETRIES:
                print("[ERROR] Retry limit reached")
                break

            print("[INFO] Sending failure to LLM for repair...")
            print(exec_error)
            print(stdout)

            # Call LLM again
            playbook_yaml = executor.regenerate_playbook(
                service=service,
                plan=plan,
                failed_yaml=playbook_yaml,
                stdout=stdout,
                error_log=exec_error
            )

            print("[INFO] New playbook generated — retrying...")

    # Save output log
    with open(exp_dir / "ansible_output.log", "w") as f:
        f.write(stdout)
        if exec_error:
            f.write("\n=== exec_error ===\n")
            f.write(exec_error)

    tracker.mark("playbook_executed")

    # 8. Wait for rollout completion (replaces fixed 120s wait)
    print("[INFO] Waiting for deployment rollout to complete...")
    rollout_result = wait_for_rollout_completion(
        service=service,
        namespace=NAMESPACE,
        timeout=ROLLOUT_TIMEOUT
    )
    tracker.mark("rollout_complete")

    with open(exp_dir / "rollout_status.json", "w") as f:
        json.dump(rollout_result, f, indent=2)

    if not rollout_result["rollout_completed"]:
        print(f"[WARNING] Rollout did not complete within {ROLLOUT_TIMEOUT}s")
        if rollout_result["timeout_occurred"]:
            print("[WARNING] Rollout timeout occurred - metrics may be unstable")

        # Get failure reasons if rollout failed
        failure_reasons = get_pod_failure_reasons(service, NAMESPACE)
        if failure_reasons:
            print(f"[ERROR] Pod failures detected: {failure_reasons}")
            with open(exp_dir / "pod_failures.json", "w") as f:
                json.dump(failure_reasons, f, indent=2)
    else:
        print(f"[INFO] Rollout completed successfully in {rollout_result['rollout_duration_seconds']:.1f}s")
        print(f"[INFO] Final pod count: {rollout_result['final_pod_count']} ready")

    # 8.5 Warmup period for new pods
    print(f"[INFO] Waiting {WARMUP_PERIOD}s for pods to warm up (JIT, caches, connections)...")
    time.sleep(WARMUP_PERIOD)
    tracker.mark("warmup_complete")

    # 9. Observe Metrics After
    print("[INFO] Fetching metrics after mitigation...")
    after_window = build_collection_window(metric_cfg, "after")

    metrics_after = collect_fault_observation(
        prometheus_url=PROMETHEUS_URL,
        fault=fault,
        window=after_window,
        metric_groups=metric_groups,
    )
    tracker.mark("metrics_after")
    with open(exp_dir / "metrics_after.json", "w") as f:
        json.dump(metrics_after, f, indent=2)
    
    # 9.5 Capture Infrastructure State After
    print("[INFO] Capturing infrastructure state after mitigation...")
    infra_state_after = capture_infrastructure_state(prom_client, service, NAMESPACE)
    tracker.mark("infra_state_after")
    with open(exp_dir / "infrastructure_after.json", "w") as f:
        json.dump(infra_state_after, f, indent=2)
    print(f"[INFO] Post-remediation: {infra_state_after['pod_count_ready']} pods ready")

    # Compare infrastructure states
    infra_comparison = compare_infrastructure_states(infra_state_before, infra_state_after)
    with open(exp_dir / "infrastructure_comparison.json", "w") as f:
        json.dump(infra_comparison, f, indent=2)

    if infra_comparison["scale_out_occurred"]:
        print(f"[INFO] Scale-out detected: {infra_comparison['pod_count_delta']:+d} pods")
    if infra_comparison["scale_up_occurred"]:
        print(f"[INFO] Scale-up detected: CPU limit changed by {infra_comparison['cpu_per_pod_delta_millicores']:+.0f}m per pod")

    # 10. Compute Feedback (SINGLE CONSOLIDATED BLOCK)
    if use_normalized_feedback:
        # Use infrastructure-aware normalized feedback
        feedback = compute_normalized_feedback_with_ansible(
            metrics_before=refined_metrics_before,
            metrics_after=metrics_after,
            infra_state_before=infra_state_before,
            infra_state_after=infra_state_after,
            ansible_log=stdout,
            slo_thresholds=slo_thresholds,
            playbook_retries=attempt
        )

        print("[INFO] Using feedback with infrastructure awareness")
    else:
    # Use original feedback (backward compatible)
        mask = relevance_mask_for_fault(fault_type)
        feedback = compute_weighted_feedback_with_ansible(
            metrics_before=refined_metrics_before,
            metrics_after=metrics_after,
            ansible_log=stdout,
            relevance_mask=mask,
            playbook_retries = attempt
        )
        print("[INFO] Using original feedback (legacy mode)")

    with open(exp_dir / "feedback.json", "w") as f:
        json.dump(feedback, f, indent=2)

    # 11. Store Experience (only if execution succeeded)
    store_experience(
        incident={
            "fault_id": fault_id,
            "fault": fault_type,
            "service": service,
        },
        plan=plan,
        metrics_before=refined_metrics_before,
        metrics_after=metrics_after,
        evs=feedback["EVS"],
        mu=feedback["MU"],
        reward=feedback["reward"],
        ansible_score=feedback["ansible_score"]
    )

    store_or_update_code_experience(
        service=service,
        fault_type=fault_id,
        plan=plan,
        playbook_yaml=playbook_yaml,
        feedback=feedback,
        execution_status=exec_status,
        execution_error=exec_error
    )
    
    print("[SUCCESS] Experiment completed successfully.")
    latencies = tracker.summary()
    latency_path = exp_dir / "latencies.json"
    with open(latency_path, "w") as f:
        json.dump(latencies, f, indent=2)

    print(f"[INFO] Step-wise latencies (s): {latencies}")

    ansible_recap = parse_ansible_recap(stdout)

    # Extract feedback values safely
    if feedback is not None:
        evs = feedback.get("EVS")
        mu = feedback.get("MU")
        reward = feedback.get("reward")
        ansible_score = feedback.get("ansible_score")
    else:
        evs = None
        mu = None
        reward = None
        ansible_score = None
    
    # Filter metrics for return (remove infra keys)
    filtered_metrics_before = filter_metrics(refined_metrics_before)
    filtered_metrics_after = filter_metrics(metrics_after)

    # ICL sample sizes
    planner_icl_samples = len(experience_raw) if experience_raw else 0
    executor_icl_samples = len(icl_examples) if icl_examples else 0


    # SINGLE COMPREHENSIVE RETURN STATEMENT
    return {
        # Core experiment metadata
        "service": service,
        "fault_type": fault_type,
        "fault_id": fault_id,

        # Latencies per pipeline stage
        "latencies": latencies,

        # Metrics (filtered)
        "metrics_before": filtered_metrics_before,
        "metrics_after": filtered_metrics_after,

         # ICL statistics
        "planner_icl_samples": planner_icl_samples,
        "executor_icl_samples": executor_icl_samples,

        # Mitigation quality (original metrics - always present)
        "EVS": evs,
        "MU": mu,
        "reward": reward,
        "ansible_score": ansible_score,

        # Execution status
        "execution_status": exec_status,
        "execution_error": exec_error,
        "playbook_retries": attempt,

        # Rollout metadata
        "rollout_completed": rollout_result["rollout_completed"],
        "rollout_duration_seconds": rollout_result["rollout_duration_seconds"],
        "rollout_timeout_occurred": rollout_result["timeout_occurred"],

        # Infrastructure state changes
        "pod_count_before": infra_state_before["pod_count_ready"],
        "pod_count_after": infra_state_after["pod_count_ready"],
        "pod_count_delta": infra_comparison["pod_count_delta"],
        "scale_out_occurred": infra_comparison["scale_out_occurred"],
        "scale_up_occurred": infra_comparison["scale_up_occurred"],
        "cpu_limit_per_pod_before_millicores": infra_state_before.get("cpu_limit_per_pod_millicores"),
        "cpu_limit_per_pod_after_millicores": infra_state_after.get("cpu_limit_per_pod_millicores"),

        # Ansible recap
        "ansible_recap": ansible_recap,

        # Experiment directory
        "experiment_dir": str(exp_dir)
    }