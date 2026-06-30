# EV-AIM: Execution-Validated Adaptive Incident Mitigation

> EV-AIM (Execution-Validated Adaptive Incident Mitigation) is a closed-loop LLM-assisted incident mitigation framework for Kubernetes microservices. EV-AIM injects controlled faults, collects runtime observability signals, generates mitigation plans, executes remediation actions, validates recovery, and continuously improves using execution feedback.

---

## Overview

Modern incident-response systems often evaluate mitigation quality based solely on generated plans. EV-AIM instead evaluates remediation using runtime execution evidence.

EV-AIM closes the loop between:

```text
Fault
  ↓
Observation
  ↓
Diagnosis
  ↓
Mitigation Planning
  ↓
Execution
  ↓
Recovery Validation
  ↓
Feedback
  ↓
Experience Store
```

Unlike plan-only systems, EV-AIM verifies whether executed remediation actions actually improve system health.

---

## Key Features

- Kubernetes-native incident mitigation
- LLM-based remediation planning
- Rule-based recovery baseline
- Runtime execution validation
- Feedback-driven experience learning
- Namespace-level infrastructure awareness
- Support for multiple microservice applications
- Comparative evaluation against deterministic recovery strategies

---

## Supported Applications

| Application | Namespace |
|------------|------------|
| Robot Shop | `robot-shop` |
| Sock Shop | `sock-shop` |
| Online Boutique | `online-boutique` |

---

# Architecture

```text
┌──────────────────────────────┐
│ Fault Injection              │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Metrics Collection           │
│ Prometheus + Kubernetes      │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Planner                      │
│ LLM / Rule-Based             │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Execution Decision           │
│ execution_required?          │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Executor                     │
│ kubectl actions              │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Rollout Monitoring           │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Recovery Validation          │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Feedback Generation          │
│ SHS / PS / ES / Reward       │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Experience Store             │
└──────────────────────────────┘
```

---

# Experiment Modes

EV-AIM currently supports four execution modes.

## 1. Observation Mode

Used for validating fault injections and metric collection.

```text
Fault
 → Observe
 → Recover
 → Collect Metrics
```

No planning or remediation generation is performed.

---

## 2. Planner-Only Mode

Used for evaluating diagnosis and mitigation planning quality.

```text
Fault
 → Metrics
 → Planner
```

No remediation is executed.

---

## 3. Full EV-AIM Mode

Closed-loop remediation workflow.

```text
Fault
 → Metrics
 → Planner
 → Executor
 → Validation
 → Feedback
```

---

## 4. Rule-Based Baseline

Deterministic recovery actions used as a baseline.

```text
Fault
 → Fixed Recovery Rule
 → Validation
 → Feedback
```

Used for comparison against EV-AIM.

---

# Supported Faults

## Runtime Faults

| Fault |
|--------|
| load_spike |
| db_overload |
| cpu_pressure |
| memory_pressure |

---

## Kubernetes-Native Faults

| Fault |
|--------|
| pod_crash |
| dependency_failure |
| bad_image |
| stuck_deployment |

---

# Recovery Actions

| Fault | Recovery Action |
|---------|---------|
| load_spike | stop_load |
| db_overload | stop_load |
| cpu_pressure | stop_load |
| memory_pressure | stop_load |
| pod_crash | rollout_restart |
| dependency_failure | restore_original_replicas |
| bad_image | rollout_undo |
| stuck_deployment | rollout_resume |

---

# Metrics Collection

EV-AIM collects three categories of telemetry.

## Application Metrics

Examples:

- latency_p95
- latency_p99
- request_rate
- throughput
- error_rate
- HTTP 5xx rate

---

## Resource Metrics

Examples:

- cpu_usage
- cpu_limit_ratio
- cpu_throttling
- memory_working_set
- memory_limit_ratio

---

## Infrastructure Metrics

Namespace-level infrastructure state.

Examples:

- running_pods
- pending_pods
- failed_pods
- restart_count
- deployment_ready_replicas
- deployment_available_replicas
- HPA status
- node resource utilization

---

# Execution Validation

A key EV-AIM feature is execution-aware planning.

The planner can determine that no remediation is required.

Example:

```json
{
  "execution_required": false,
  "reason": "Service recovered automatically."
}
```

This prevents unnecessary remediation actions.

---

# Feedback Model

EV-AIM evaluates remediation effectiveness using execution evidence.

---

## SHS (System Health Score)

Absolute post-recovery health.

Measures:

- latency
- availability
- error rate
- resource utilization
- infrastructure health

Higher is better.

---

## PS (Performance Score)

Relative performance improvement.

Measures improvement between:

```text
Before Fault
      ↓
After Recovery
```

Examples:

- latency reduction
- error reduction
- throughput improvement

Higher is better.

---

## ES (Efficiency Score)

Relative efficiency improvement.

Measures:

- CPU efficiency
- Memory efficiency
- Infrastructure efficiency

Higher is better.

---

## Reward

Final remediation quality score.

```text
Reward =
0.4 × SHS +
0.3 × PS +
0.3 × ES
```

Reward is used for experience ranking and retrieval.

---

# Experience Store

EV-AIM maintains an execution-validated experience store.

Each experience contains:

```text
Fault Context
Mitigation Plan
Executed Actions
Recovery Outcome
Feedback Metrics
Reward
```

The experience store supports retrieval-augmented mitigation planning.

---

# Running Experiments

## Full EV-AIM

```bash
python3 -m src.run_batch \
  --file experiment_args/test_gpt_fixed.json
```

---

## Rule-Based Baseline

```bash
python3 -m src.run_batch \
  --mode rule_based \
  --file experiment_args/rule_based.json
```

---

## Planner-Only

```bash
python3 -m src.run_batch \
  --mode planner_only \
  --file experiment_args/test_gpt_fixed.json
```

---

# Output Artifacts

Each experiment produces:

```text
experiment_results/
│
├── planner_metrics_before.json
├── planner_metrics_after.json
├── infrastructure_before.json
├── infrastructure_after.json
├── feedback.json
├── rollout_result.json
├── planner_response.json
├── remediation_plan.json
└── execution_log.txt
```

---

# Current Evaluation Design

## RQ1

How does EV-AIM compare against deterministic rule-based recovery?

---

## RQ2

How do different LLMs affect remediation quality?

Examples:

- GPT-4o
- Claude Sonnet
- Gemini

---

## RQ3

What is the contribution of individual EV-AIM components?

Ablations:

- No Retrieval
- No Feedback
- No Execution Validation
- Full EV-AIM

---

## RQ4

What runtime and operational overhead does EV-AIM introduce?

Metrics:

- Planning latency
- Execution latency
- Rollout latency
- Total mitigation time

---

# Repository Structure

```text
src/
├── fault_injection/
├── monitoring/
├── planner/
├── executor/
├── feedback/
├── experience/
├── experiment/
└── run_batch.py

experiment_args/

experiment_results/

docs/
```

---

# Citation

If you use EV-AIM in academic research, please cite the corresponding publication once available.

---

# License

MIT License