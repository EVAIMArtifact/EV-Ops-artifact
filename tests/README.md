# Tests

This directory contains test scripts for validating AIM-EVM components.

## Test Scripts

### `test_prompt_generation.py`

Tests the full remediation pipeline by executing real fault injection, metrics collection, kubectl scale remediation, and post-remediation metrics.

**Purpose:**
- Validate prompt structure for RQ2 (feedback-aware ICL)
- Verify metrics collection timing accuracy
- Test kubectl scale remediation execution
- Validate rollout monitoring
- Verify metrics_before vs metrics_after collection

**Usage:**
```bash
# Run from project root
python -m tests.test_prompt_generation

# Results saved to test_results/
ls test_results/
# → test_cart_cpu_full_remediation.json
# → test_catalogue_memory_full_remediation.json
```

**Test Scenarios:**
1. CPU stress on cart service → scale to 2 replicas
2. Memory stress on catalogue service → scale to 2 replicas

**Timing:**
- ~13 minutes total per test
- Steps: 20s init + 120s fault + 30s ingestion + scale + rollout + 60s warmup + 120s metrics_after

**Pipeline Steps Tested:**
1. Fault injection (real stress-ng execution)
2. Metrics collection before remediation
3. Infrastructure state capture before
4. User prompt generation (no LLM call)
5. Remediation via kubectl scale
6. Rollout completion monitoring
7. Warmup period
8. Metrics collection after remediation
9. Infrastructure state capture after
10. Simplified feedback computation

**Output:**
Each test generates a JSON file containing:
- `fault_type`: Type of fault injected
- `service`: Target service
- `metrics_before`: Prometheus metrics during fault
- `infrastructure_state_before`: Pre-remediation pod counts
- `user_prompt`: Generated user prompt (what LLM would receive)
- `remediation_action`: Executed kubectl command
- `rollout_result`: Rollout monitoring data
- `metrics_after`: Prometheus metrics post-remediation
- `infrastructure_state_after`: Post-remediation pod counts
- `infrastructure_comparison`: Scale-out detection
- `feedback`: Simplified feedback (no EVS/MU, just infrastructure changes)

## Directory Structure

```
tests/
├── __init__.py                           # Package marker
├── README.md                             # This file
└── test_prompt_generation.py             # Prompt generation test
```

## Test Results

Results are saved to `test_results/` in the project root (gitignored).

## Running Tests

### Prerequisites
- Kubernetes cluster with Robot Shop deployed
- Prometheus accessible at http://localhost:9090
- `kubectl` configured and accessible

### Execution
```bash
# From project root
python -m tests.test_prompt_generation
```

### Expected Output
```
================================================================================
STARTING TEST 1: CPU STRESS ON CART
================================================================================
[STEP 1] Injecting fault...
✓ Injected: {'type': 'cpu_stress', 'service': 'cart', 'duration': 120}
[STEP 2-1] Waiting 20s for fault initialization...
✓ Fault process confirmed started
...
✓ Saved: test_results/test_cart_cpu_prompt.json
```

## Notes

- Tests execute REAL fault injection (stress-ng on actual pods)
- Timing model ensures >95% metric accuracy
- Experience retrieval is mocked (returns "None")
- LLM API is never called (prompt extracted via private method access)
