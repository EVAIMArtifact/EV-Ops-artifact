````markdown
# EV-Ops: Execution-Validated Autonomous Incident Management for Cloud-Native Systems

EV-Ops is a closed-loop autonomous incident management framework for Kubernetes-based cloud-native microservices. It integrates online root cause analysis, LLM-assisted mitigation planning, deterministic safety validation, execution monitoring, post-execution recovery validation, and continual learning from validated operational experience.

EV-Ops consists of two main components:

- **EV-RCA**: Execution-Validated Root Cause Analysis for online fault localization.
- **EV-AIM**: Execution-Validated Adaptive Incident Mitigation for safe autonomous recovery.

---

## Overview

Traditional LLM-based incident management systems often generate mitigation plans from static observations and assume that successful execution implies recovery. EV-Ops instead closes the operational loop by validating whether executed actions actually improve system health.

```text
Fault Injection / Runtime Incident
        ↓
Multi-Modal Observability
        ↓
EV-RCA: Fault Localization
        ↓
Runtime Incident Context
        ↓
EV-AIM: Experience Retrieval + Mitigation Planning
        ↓
Safety Validation
        ↓
Kubernetes Execution
        ↓
Post-Execution Recovery Validation
        ↓
Feedback + Reward
        ↓
Experience Store
````

EV-Ops learns from execution outcomes rather than generated plans alone.

---

## Key Features

* Online fault localization from multi-modal observability
* Support for single and concurrent faults
* LLM-based mitigation planning
* Execution-validated experience retrieval
* Deterministic safety validation before deployment
* Kubernetes-native remediation actions
* Post-execution recovery validation
* Feedback-driven Experience Store
* Rule-based and LLM-only baselines
* Evaluation on Robot-Shop, Sock-Shop, and Online Boutique

---

## Supported Applications

| Application     | Namespace         |
| --------------- | ----------------- |
| Robot-Shop      | `robot-shop`      |
| Sock-Shop       | `sock-shop`       |
| Online Boutique | `online-boutique` |

---

## Architecture

```text
┌──────────────────────────────┐
│ Multi-Modal Observability    │
│ Prometheus + OpenTelemetry   │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ EV-RCA                       │
│ Online Fault Localization    │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Runtime Incident Context     │
│ Fault + Service + Symptoms   │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Experience Retrieval         │
│ Similarity + Execution Quality│
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ EV-AIM Planner               │
│ LLM-based Mitigation Strategy│
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Safety Validation            │
│ Schema + Policy + Bounds     │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Kubernetes Execution         │
│ Ansible / kubectl Actions    │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Recovery Validation          │
│ SHS / FRQ / ES / RC / Reward │
└──────────────┬───────────────┘
               ↓
┌──────────────────────────────┐
│ Experience Store             │
│ Validated Mitigation Episodes│
└──────────────────────────────┘
```

---

# EV-RCA: Online Fault Localization

EV-RCA constructs a Runtime Incident Context from multi-modal observability before mitigation is attempted. It is designed for low-latency online root cause analysis in Kubernetes microservice environments.

## EV-RCA Pipeline

```text
Telemetry Window
      ↓
Orthogonal Feature Projection
      ↓
Lightweight Temporal Forecasting
      ↓
Residual-Based Fault Ranking
      ↓
Localized Faults
      ↓
Runtime Incident Context
```

## Inputs

EV-RCA consumes telemetry from:

* Application metrics
* Container resource metrics
* Kubernetes health signals
* Deployment state
* Node-level infrastructure state
* Distributed traces

## Output

EV-RCA produces a Runtime Incident Context containing:

```text
fault_type
affected_service
target_deployment
anomalous_metrics
resource_symptoms
kubernetes_health
dependency_context
confidence_score
supporting_observability
```

## Model Details

Current EV-RCA settings used in the paper:

| Parameter                 | Value                           |
| ------------------------- | ------------------------------- |
| Sampling interval         | `1s`                            |
| Input window length       | `T = 32`                        |
| Telemetry dimensions      | `D = 99`                        |
| Forecast horizon          | `h = 32`                        |
| Low-rank hidden dimension | `32`                            |
| Training data             | 60 minutes of healthy telemetry |

> Placeholder: add exact EV-RCA command-line scripts, model checkpoint paths, and training configuration files once finalized.

---

# EV-AIM: Execution-Validated Adaptive Incident Mitigation

EV-AIM performs autonomous mitigation using the Runtime Incident Context generated by EV-RCA. It retrieves validated operational experience, generates mitigation strategies using an LLM, validates generated playbooks before execution, and stores recovery outcomes in an Experience Store.

## EV-AIM Pipeline

```text
Runtime Incident Context
      ↓
Execution-Validated Experience Retrieval
      ↓
LLM Mitigation Planning
      ↓
Playbook Generation
      ↓
Safety Validation
      ↓
Kubernetes Execution
      ↓
Post-Execution Validation
      ↓
Feedback + Reward
      ↓
Experience Store Update
```

---

## Experience Store

Each mitigation episode contains:

```text
Runtime Incident Context
Mitigation Strategy
Executable Playbook
Normalized Action
Execution Result
Recovery Metrics
Feedback Vector
Reward
```

Each action is normalized as:

```text
(action, target, value)
```

Examples:

```text
(scale_out, frontend, 3 replicas)
(scale_up_cpu, cart, 1000m)
(scale_up_memory, carts, 1Gi)
(rollback, shipping, previous revision)
```

---

## Experience Retrieval

EV-AIM retrieves mitigation episodes using both contextual similarity and execution quality.

Contextual similarity considers:

* Fault type
* Affected service
* Kubernetes symptoms
* Deployment state
* CPU utilization
* Memory utilization
* Disk pressure
* Application-level symptoms

Execution quality considers:

* Reward
* Execution Success
* Fault Recovery Quality
* Resource Cost
* Regression indicators

During cold start, EV-AIM falls back to prompt-guided mitigation. As validated episodes accumulate, planning becomes experience-guided.

---

## Safety Validation

Before execution, every generated playbook is validated against deterministic constraints.

Validation checks include:

* YAML/schema correctness
* Supported Kubernetes remediation actions
* Namespace consistency
* Target deployment existence
* Resource-bound constraints
* Rollout timeout handling
* Failure detection

Playbooks failing validation are rejected before deployment.

---

## Execution Validation

EV-AIM validates recovery using post-execution observability rather than rollout status alone.

Feedback metrics include:

| Metric      | Meaning                                  |
| ----------- | ---------------------------------------- |
| `SHS`       | System Health Score                      |
| `delta_SHS` | Change in system health after mitigation |
| `FRQ`       | Fault Recovery Quality                   |
| `ES`        | Execution Success                        |
| `RC`        | Resource Cost                            |
| `Reward`    | Execution-grounded mitigation quality    |

Reward balances recovery quality, execution success, and resource efficiency.

---

# Supported Faults

## Resource Faults

| Fault         | Description                                  |
| ------------- | -------------------------------------------- |
| `cpu_hog`     | CPU pressure injected into target service    |
| `mem_stress`  | Memory pressure injected into target service |
| `disk_stress` | Disk I/O or disk pressure fault              |

## Kubernetes-Native Faults

| Fault                | Description                                |
| -------------------- | ------------------------------------------ |
| `pod_crash`          | Pod is killed or restarted                 |
| `dependency_failure` | Dependency replica or service is disrupted |
| `bad_image`          | Deployment is updated to an invalid image  |
| `stuck_deployment`   | Deployment rollout is paused or stuck      |

## Workload Faults

| Fault         | Description                     |
| ------------- | ------------------------------- |
| `load_spike`  | Sudden increase in user traffic |
| `db_overload` | Database pressure or overload   |

---

# Supported Recovery Actions

| Action                      | Description                          |
| --------------------------- | ------------------------------------ |
| `scale_out`                 | Increase replica count               |
| `scale_up_cpu`              | Increase CPU request/limit           |
| `scale_up_memory`           | Increase memory request/limit        |
| `rollout_restart`           | Restart deployment                   |
| `rollout_undo`              | Roll back to previous revision       |
| `rollout_resume`            | Resume paused rollout                |
| `restore_original_replicas` | Restore expected replica count       |
| `stop_load`                 | Stop injected workload pressure      |
| `none`                      | No action when service self-recovers |

---

# Experiment Modes

## 1. Observation Mode

Used to validate fault injection and metric collection.

```text
Fault → Observe → Recover → Collect Metrics
```

## 2. Planner-Only Mode

Used to evaluate planning quality without executing remediation.

```text
Fault → Metrics → Planner
```

## 3. Full EV-Ops Mode

Closed-loop diagnosis and mitigation.

```text
Fault → EV-RCA → EV-AIM → Execution → Validation → Feedback
```

## 4. Rule-Based Baseline

Uses deterministic recovery rules for each known fault type.

```text
Fault → Fixed Recovery Rule → Execution → Validation → Feedback
```

## 5. LLM-Only Baseline

Uses the current Runtime Incident Context without retrieved experience.

```text
Fault → Runtime Incident Context → LLM Planner → Execution → Validation
```

## 6. LLM+Random Ablation

Retrieves random mitigation episodes instead of execution-validated episodes.

```text
Fault → Random Experience Retrieval → LLM Planner → Execution → Validation
```

---

# Running Experiments

## Full EV-Ops / EV-AIM

```bash
python3 -m src.run_batch \
  --file experiment_args/test_gpt_fixed.json
```

## Rule-Based Baseline

```bash
python3 -m src.run_batch \
  --mode rule_based \
  --file experiment_args/rule_based.json
```

## Planner-Only

```bash
python3 -m src.run_batch \
  --mode planner_only \
  --file experiment_args/test_gpt_fixed.json
```

## Observation Mode

```bash
python3 -m src.run_batch \
  --mode observe \
  --file experiment_args/observe.json
```

> Adjust config filenames according to your local experiment setup.

---

# Output Artifacts

Each experiment writes results under `experiment_results/`.

Typical outputs include:

```text
experiment_results/
├── planner_metrics_before.json
├── planner_metrics_after.json
├── infrastructure_before.json
├── infrastructure_after.json
├── feedback.json
├── rollout_result.json
├── planner_response.json
├── remediation_plan.json
├── execution_log.txt
├── latency.json
├── summary.csv
└── comparison_rows.csv
```

Global experiment summaries may also be appended to:

```text
analyzed.csv
summary.csv
```

---

# Repository Structure

```text
EV-AIM/
├── src/
│   ├── clients/
│   ├── experiment/
│   ├── executor/
│   ├── fault_injection/
│   ├── feedback/
│   ├── monitoring/
│   ├── planner/
│   ├── utils/
│   └── run_batch.py
│
EV-RCA/
experiment_args/
experiment_results/
knowledge/
docs/
```

---

# Evaluation Design

## RQ1: Fault Localization

Evaluates EV-RCA against representative forecasting- and anomaly-based RCA methods.

Metrics:

* Metric-level AV@5
* Service-level AV@5
* Service-level MRR
* Training time
* Inference latency
* GPU memory
* Energy consumption

## RQ2: Mitigation Effectiveness

Compares EV-AIM with deterministic rule-based remediation.

Metrics:

* Recovery Success
* SHS
* delta_SHS
* FRQ
* Resource Cost
* Reward

## RQ3: Impact of Execution-Validated Retrieval

Compares:

* LLM-only
* LLM+Random
* EV-AIM

## RQ4: Cross-Application Generalization

Tests whether experiences collected from Robot-Shop and Sock-Shop transfer to Online Boutique.

## RQ5: Reward Alignment, Policy Diversity, and Overhead

Evaluates:

* Reward alignment with recovery indicators
* Policy entropy
* Dominant action frequency
* Retrieval/planning/execution overhead

---

# Configuration Notes

Common configuration parameters include:

```text
PROMETHEUS_URL
FAULT_INIT_WAIT
METRIC_SCRAPING_BUFFER
ROLLOUT_TIMEOUT
WARMUP_PERIOD
```

LLM configuration used in the evaluation:

```text
Model: GPT-4o
Temperature: 0.3
Top-p: 1.0
```

EV-RCA configuration:

```text
Sampling interval: 1s
Input window: 32 steps
Forecast horizon: 32 steps
Telemetry dimensions: 99
Low-rank hidden dimension: 32
Healthy training data: 60 minutes
```

---

# Requirements

Placeholder requirements:

```text
python>=3.10
kubectl
helm
ansible
prometheus-api-client
pyyaml
pandas
numpy
torch
langgraph
openai
```

> Replace this list with the final `requirements.txt` once the environment is finalized.

---

# Citation

If you use EV-Ops in academic research, please cite the corresponding publication once available.

```bibtex
@inproceedings{evops2026,
  title     = {EV-Ops: Execution-Validated Autonomous Incident Management for Cloud-Native Systems},
  author    = {Anonymous},
  booktitle = {Proceedings of the International Conference on Software Engineering},
  year      = {2026}
}
```

---

# License

MIT License.

```
```
