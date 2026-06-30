"""
Test script to verify LLM plan generation with REAL OpenAI API calls and complete remediation pipeline.

This test validates the full experimental pipeline by executing:
- Steps 1-2: Fault injection with validated timing (20s init + fault duration + 30s buffer)
- Step 3: Pre-remediation metrics and infrastructure state capture
- Step 4: Experience retrieval from knowledge base
- Step 5: **REAL LLM API CALL** to generate mitigation plan (OpenAI GPT-4o)
- Step 6: Remediation via kubectl scale (simplified, no Ansible)
- Step 8: Rollout completion monitoring
- Step 9: Warmup period + post-remediation metrics collection
- Step 10: Simplified feedback computation (infrastructure comparison)

TIMING RATIONALE (validated by promql-k8s-observability and systems-research-evaluator agents):
- 20s initialization wait: kubectl exec + stress-ng startup (5-14s typical)
- 120s fault duration: full stress-ng execution
- 30s scraping buffer: Prometheus (15s) + cAdvisor export (10-15s) ingestion lag
- Total: 170s for 120s fault

REQUIREMENTS:
- Set OPENAI_API_KEY environment variable before running
- Robot Shop must be deployed in 'robot-shop' namespace
- Prometheus accessible at localhost:9090
"""
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional

from prometheus_api_client import PrometheusConnect

from src.fault_injection import inject_fault
from src.monitoring.metrics import fetch_metrics
from src.monitoring.infrastructure_state import (
    capture_infrastructure_state,
    compare_infrastructure_states
)
from src.planner.llm_planner import MitigationPlanner
from src.planner.planner_prompt import PLANNER_SYSTEM_PROMPT
from src.planner.retrieval import retrieve_experience, format_for_prompt
from src.executor.rollout_monitor import wait_for_rollout_completion
from src.experiment.model_config import ModelConfig


# Constants
PROMETHEUS_URL = "http://localhost:9090"
NAMESPACE = "robot-shop"
OBS_WINDOW_INTERVAL = "5s"
OBS_WINDOW_AFTER = "120s"
FAULT_INIT_WAIT = 20  # seconds
METRIC_SCRAPING_BUFFER = 30  # seconds
ROLLOUT_TIMEOUT = 300  # Max time to wait for rollout completion
WARMUP_PERIOD = 60  # Time to wait after rollout for pods to warm up
OUTPUT_DIR = Path(__file__).parent.parent / "test_results"


@dataclass
class PromptTestResult:
    """Structured result from prompt generation and remediation test."""
    # Experiment metadata
    fault_type: str
    service: str
    duration: int

    # Step 3: Pre-remediation metrics and state
    metrics_before: Dict[str, Any]
    infrastructure_state_before: Dict[str, Any]

    # Step 4: Experience retrieval
    experience_prompt: str

    # Step 5: LLM prompt and plan generation (REAL LLM CALL)
    system_prompt: str
    user_prompt: str
    plan: Optional[Dict[str, Any]]
    plan_generation_error: Optional[str]

    # Step 6: Remediation execution
    remediation_action: str
    remediation_executed: bool
    remediation_error: Optional[str]

    # Step 8: Rollout monitoring
    rollout_result: Dict[str, Any]

    # Step 9: Post-remediation metrics and state
    metrics_after: Optional[Dict[str, Any]]
    infrastructure_state_after: Optional[Dict[str, Any]]
    infrastructure_comparison: Optional[Dict[str, Any]]

    # Step 10: Feedback (simplified)
    feedback: Optional[Dict[str, Any]]

    # Timing
    total_duration_seconds: float

    def save_to_json(self, filepath: Path) -> None:
        """Save test result to JSON file."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(asdict(self), f, indent=2, default=str)
        print(f"✓ Saved: {filepath}")


def strip_timestamps(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert Prometheus timestamp-value pairs to scalar values."""
    for key, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if "value" in item and isinstance(item["value"], list):
                item["value"] = float(item["value"][1])
    return data


def extract_user_prompt(
    fault_type: str,
    metrics: Dict[str, Any],
    experience: str = "None"
) -> str:
    """Extract user_prompt using MitigationPlanner's private __build_prompt method."""
    planner = MitigationPlanner(llm_client=None)
    return planner._MitigationPlanner__build_prompt(
        fault_type=fault_type,
        metrics=metrics,
        experience=experience
    )


def execute_scale_remediation(service: str, target_replicas: int) -> Dict[str, Any]:
    """Execute kubectl scale command as remediation action."""
    command = [
        "kubectl", "scale",
        f"deployment/{service}",
        f"--replicas={target_replicas}",
        "-n", NAMESPACE
    ]
    command_str = " ".join(command)

    print(f"[STEP 6] Executing remediation: {command_str}")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            print(f"✓ Remediation executed successfully\n")
            return {
                "success": True,
                "command": command_str,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": None
            }
        else:
            print(f"✗ Remediation failed: {result.stderr}\n")
            return {
                "success": False,
                "command": command_str,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "error": result.stderr
            }

    except subprocess.TimeoutExpired:
        error_msg = "kubectl scale command timed out after 30s"
        print(f"✗ {error_msg}\n")
        return {
            "success": False,
            "command": command_str,
            "stdout": "",
            "stderr": "",
            "error": error_msg
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"✗ {error_msg}\n")
        return {
            "success": False,
            "command": command_str,
            "stdout": "",
            "stderr": "",
            "error": error_msg
        }


def reset_deployment_replicas(service: str, original_replicas: int = 1) -> bool:
    """Reset deployment to original replica count after test."""
    print(f"\n[CLEANUP] Resetting {service} to {original_replicas} replica(s)...")

    try:
        result = subprocess.run(
            [
                "kubectl", "scale",
                f"deployment/{service}",
                f"--replicas={original_replicas}",
                "-n", NAMESPACE
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            print(f"✓ Reset successful\n")
            return True
        else:
            print(f"✗ Reset failed: {result.stderr}\n")
            return False

    except Exception as e:
        print(f"✗ Reset error: {e}\n")
        return False


def run_full_remediation_test(
    fault_type: str,
    service: str,
    duration: int,
    model_config: ModelConfig,
    exp_dir: Path,
    target_replicas: int = 2
) -> PromptTestResult:
    """
    Execute full fault injection, prompt generation, and remediation pipeline.

    Steps executed:
    1. Inject fault
    2. Wait for fault completion with proper timing
    3. Collect metrics_before and infrastructure_state_before
    4. Retrieve experience from knowledge base
    5. Generate mitigation plan via REAL LLM API call
    6. Execute kubectl scale remediation
    8. Wait for rollout completion
    9. Warmup + collect metrics_after
    10. Compute simplified feedback
    """
    start_time = time.time()

    print(f"\n{'='*80}")
    print(f"Test: {fault_type} on {service} ({duration}s) → scale to {target_replicas}")
    print(f"{'='*80}\n")

    prom_client = PrometheusConnect(url=PROMETHEUS_URL, disable_ssl=True)

    # Step 1: Inject Fault
    print("[STEP 1] Injecting fault...")
    fault = {"type": fault_type, "service": service, "duration": duration}
    inject_fault(fault)
    print(f"✓ Injected: {fault}\n")

    # Step 2-1: Wait for initialization
    print(f"[STEP 2-1] Waiting {FAULT_INIT_WAIT}s for fault initialization...")
    time.sleep(FAULT_INIT_WAIT)
    print("✓ Fault process started\n")

    # Step 2-2: Wait for fault completion
    print(f"[STEP 2-2] Waiting {duration}s for fault to complete...")
    time.sleep(duration)
    print("✓ Fault completed\n")

    # Step 2-3: Wait for metric ingestion
    print(f"[STEP 2-3] Waiting {METRIC_SCRAPING_BUFFER}s for metric ingestion...")
    time.sleep(METRIC_SCRAPING_BUFFER)
    print("✓ Metrics ingested\n")

    # Step 3-1: Fetch metrics_before
    print(f"[STEP 3-1] Fetching metrics for last {duration}s...")
    metrics_raw = fetch_metrics(
        service=service,
        window=f"{duration}s",
        interval=OBS_WINDOW_INTERVAL,
        prometheus_url=PROMETHEUS_URL
    )
    metrics_before = strip_timestamps(metrics_raw.copy())
    print(f"✓ Metrics collected: {len(metrics_raw)} keys\n")

    # Step 3-2: Capture infrastructure_state_before
    print("[STEP 3-2] Capturing infrastructure state before remediation...")
    infra_state_before = capture_infrastructure_state(prom_client, service, NAMESPACE)
    print(f"✓ Pre-remediation: {infra_state_before['pod_count_ready']} pods ready\n")

    # Step 4: Retrieve experience from knowledge base
    print("[STEP 4] Retrieving experience from knowledge base...")
    experience_raw = retrieve_experience(fault_type, metrics_before)
    experience_prompt = format_for_prompt(experience_raw)
    print(f"✓ Experience retrieved: {len(experience_prompt)} chars\n")

    # Step 5: Generate mitigation plan via REAL LLM API call
    print("[STEP 5] Generating mitigation plan via LLM (REAL API CALL)...")
    plan = None
    plan_generation_error = None
    user_prompt = ""

    try:
        # Create planner with real LLM client
        planner = MitigationPlanner.from_config(model_config.to_dict())

        # Extract user_prompt for debugging (before LLM call)
        user_prompt = extract_user_prompt(fault_type, metrics_raw, experience_prompt)

        # Generate plan via LLM
        plan = planner.plan(
            fault_type=fault_type,
            exp_dir=exp_dir,
            metrics=metrics_before,
            experience=experience_prompt,
        )

        print(f"✓ Plan generated successfully")
        print(f"  - Actions: {len(plan.get('actions', []))} steps")
        print(f"  - Reasoning: {plan.get('reasoning', 'N/A')[:100]}...")
        print(f"  - User prompt: {len(user_prompt)} chars\n")

        # Save plan to experiment directory
        with open(exp_dir / "plan.json", "w") as f:
            json.dump(plan, f, indent=2)

    except Exception as e:
        plan_generation_error = str(e)
        print(f"✗ LLM plan generation failed: {e}\n")

    # Step 6: Execute remediation
    remediation_result = execute_scale_remediation(service, target_replicas)
    remediation_action = remediation_result["command"]
    remediation_executed = remediation_result["success"]
    remediation_error = remediation_result["error"]

    if not remediation_executed:
        print("[ERROR] Remediation failed, skipping remaining steps\n")
        return PromptTestResult(
            fault_type=fault_type,
            service=service,
            duration=duration,
            metrics_before=metrics_before,
            infrastructure_state_before=infra_state_before,
            experience_prompt=experience_prompt,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            plan=plan,
            plan_generation_error=plan_generation_error,
            remediation_action=remediation_action,
            remediation_executed=remediation_executed,
            remediation_error=remediation_error,
            rollout_result={},
            metrics_after=None,
            infrastructure_state_after=None,
            infrastructure_comparison=None,
            feedback=None,
            total_duration_seconds=time.time() - start_time
        )

    # Step 8: Wait for rollout completion
    print(f"[STEP 8] Waiting for deployment rollout (timeout: {ROLLOUT_TIMEOUT}s)...")
    rollout_result = wait_for_rollout_completion(
        service=service,
        namespace=NAMESPACE,
        timeout=ROLLOUT_TIMEOUT
    )

    if rollout_result["rollout_completed"]:
        print(f"✓ Rollout completed in {rollout_result['rollout_duration_seconds']:.1f}s")
        print(f"✓ Final pod count: {rollout_result['final_pod_count']}\n")
    else:
        print(f"✗ Rollout failed: {rollout_result.get('error_message', 'unknown')}\n")

    # Step 8.5: Warmup period
    print(f"[STEP 8.5] Waiting {WARMUP_PERIOD}s for pods to warm up...")
    time.sleep(WARMUP_PERIOD)
    print("✓ Warmup complete\n")

    # Step 9-1: Fetch metrics_after
    print(f"[STEP 9-1] Fetching metrics after remediation (last {OBS_WINDOW_AFTER})...")
    metrics_after_raw = fetch_metrics(
        service=service,
        window=OBS_WINDOW_AFTER,
        interval=OBS_WINDOW_INTERVAL,
        prometheus_url=PROMETHEUS_URL
    )
    metrics_after = strip_timestamps(metrics_after_raw.copy())
    print(f"✓ Metrics collected: {len(metrics_after)} keys\n")

    # Step 9-2: Capture infrastructure_state_after
    print("[STEP 9-2] Capturing infrastructure state after remediation...")
    infra_state_after = capture_infrastructure_state(prom_client, service, NAMESPACE)
    print(f"✓ Post-remediation: {infra_state_after['pod_count_ready']} pods ready\n")

    # Compare infrastructure states
    infra_comparison = compare_infrastructure_states(infra_state_before, infra_state_after)

    # Step 10: Compute simplified feedback
    print("[STEP 10] Computing simplified feedback...")
    feedback = {
        "remediation_type": "scale_out",
        "pod_count_before": infra_state_before["pod_count_ready"],
        "pod_count_after": infra_state_after["pod_count_ready"],
        "pod_count_delta": infra_comparison["pod_count_delta"],
        "scale_out_occurred": infra_comparison["scale_out_occurred"],
        "rollout_successful": rollout_result["rollout_completed"],
        "rollout_duration_seconds": rollout_result["rollout_duration_seconds"],
    }
    print(f"✓ Feedback: scale_out={feedback['scale_out_occurred']}, "
          f"delta={feedback['pod_count_delta']}\n")

    # Verification
    print("[VERIFICATION]")
    if infra_state_after["pod_count_ready"] == target_replicas:
        print(f"  ✓ Pod count: {infra_state_after['pod_count_ready']}")
    else:
        print(f"  ✗ Expected {target_replicas} pods, got {infra_state_after['pod_count_ready']}")

    if infra_comparison["scale_out_occurred"]:
        print(f"  ✓ Scale-out detected: +{infra_comparison['pod_count_delta']} pods")
    else:
        print("  ✗ Scale-out not detected")

    if rollout_result["rollout_completed"]:
        print("  ✓ Rollout completed")
    else:
        print("  ✗ Rollout failed")
    print()

    return PromptTestResult(
        fault_type=fault_type,
        service=service,
        duration=duration,
        metrics_before=metrics_before,
        infrastructure_state_before=infra_state_before,
        experience_prompt=experience_prompt,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        plan=plan,
        plan_generation_error=plan_generation_error,
        remediation_action=remediation_action,
        remediation_executed=remediation_executed,
        remediation_error=remediation_error,
        rollout_result=rollout_result,
        metrics_after=metrics_after,
        infrastructure_state_after=infra_state_after,
        infrastructure_comparison=infra_comparison,
        feedback=feedback,
        total_duration_seconds=time.time() - start_time
    )


def main() -> None:
    """Run full remediation tests for CPU and memory stress scenarios with REAL LLM API calls."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load configuration from test1.json
    config_path = Path(__file__).parent.parent / "experiment_args" / "test1.json"

    if not config_path.exists():
        print(f"\n[ERROR] Configuration file not found: {config_path}")
        print("Please create experiment_args/test1.json with LLM configuration")
        return

    print(f"[INFO] Loading configuration from: {config_path}")
    with open(config_path, "r") as f:
        config = json.load(f)

    # Extract LLM model configuration
    llm_config = config.get("llm_model", {})

    model_config = ModelConfig(
        client=llm_config.get("client", "gpt"),
        model_id=llm_config.get("model_id", "gpt-4o"),
        api_key=llm_config.get("api_key"),
        endpoint=llm_config.get("endpoint", "https://api.anthropic.com"),
        temperature=llm_config.get("temperature", 0.3),
        max_tokens=llm_config.get("max_tokens", 3500),
    )

    print(f"\n[INFO] LLM Configuration:")
    print(f"  - Client: {model_config.client}")
    print(f"  - Model: {model_config.model_id}")
    print(f"  - Temperature: {model_config.temperature}")
    print(f"  - Max tokens: {model_config.max_tokens}\n")

    # Test 1: CPU stress on cart with scale-out
    print("\n" + "="*80)
    print("TEST 1: CPU STRESS ON CART + SCALE-OUT REMEDIATION")
    print("="*80)

    exp_dir_cpu = OUTPUT_DIR / "test_cart_cpu_full_remediation"
    exp_dir_cpu.mkdir(parents=True, exist_ok=True)

    result_cpu = run_full_remediation_test(
        fault_type="cpu_stress",
        service="cart",
        duration=120,
        model_config=model_config,
        exp_dir=exp_dir_cpu,
        target_replicas=2
    )
    result_cpu.save_to_json(OUTPUT_DIR / "test_cart_cpu_full_remediation.json")

    print(f"\n[RESULTS - Test 1]")
    print(f"  - Plan generated: {'✓' if result_cpu.plan else '✗'}")
    print(f"  - Remediation executed: {'✓' if result_cpu.remediation_executed else '✗'}")
    print(f"  - User prompt: {len(result_cpu.user_prompt)} chars")
    if result_cpu.plan:
        print(f"  - Plan actions: {len(result_cpu.plan.get('actions', []))} steps")
    print(f"\nUser prompt preview:\n{result_cpu.user_prompt[:300]}...\n")

    # Cleanup
    reset_deployment_replicas("cart", 1)
    print("Waiting 30s before next test...")
    time.sleep(30)

    # Test 2: Memory stress on catalogue with scale-out
    print("\n" + "="*80)
    print("TEST 2: MEMORY STRESS ON CATALOGUE + SCALE-OUT REMEDIATION")
    print("="*80)

    exp_dir_memory = OUTPUT_DIR / "test_catalogue_memory_full_remediation"
    exp_dir_memory.mkdir(parents=True, exist_ok=True)

    result_memory = run_full_remediation_test(
        fault_type="memory_stress",
        service="catalogue",
        duration=120,
        model_config=model_config,
        exp_dir=exp_dir_memory,
        target_replicas=2
    )
    result_memory.save_to_json(OUTPUT_DIR / "test_catalogue_memory_full_remediation.json")

    print(f"\n[RESULTS - Test 2]")
    print(f"  - Plan generated: {'✓' if result_memory.plan else '✗'}")
    print(f"  - Remediation executed: {'✓' if result_memory.remediation_executed else '✗'}")
    print(f"  - User prompt: {len(result_memory.user_prompt)} chars")
    if result_memory.plan:
        print(f"  - Plan actions: {len(result_memory.plan.get('actions', []))} steps")
    print(f"\nUser prompt preview:\n{result_memory.user_prompt[:300]}...\n")

    # Cleanup
    reset_deployment_replicas("catalogue", 1)

    print("="*80)
    print("ALL TESTS COMPLETE")
    print(f"Results saved to: {OUTPUT_DIR}")
    print("="*80)


if __name__ == "__main__":
    main()
