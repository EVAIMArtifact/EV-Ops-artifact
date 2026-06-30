# Baseline Comparison Framework for AIM-EVM

## Overview

This document defines the baselines against which AIM-EVM (LLM-driven remediation) should be compared, along with metrics and analysis methods for each comparison.

---

## Baseline Definitions

### Baseline A: No Remediation (Natural Recovery)

**Description:** System receives fault injection with no intervention. Measures natural recovery through Kubernetes self-healing (restarts, health checks).

**Implementation:**
```yaml
experiment_config:
  name: "baseline_no_remediation"
  fault_injection: enabled
  remediation: disabled
  observation_period: 300s  # 5 minutes
  metrics_collection: enabled
```

**Expected Behavior:**
- Pod restarts via liveness probe failures
- Service degradation until resources release
- Longer recovery time, potential cascading failures

**Key Metrics:**
| Metric | Expected Range | Measurement |
|--------|---------------|-------------|
| TTR | 120-300s | Time until latency < threshold |
| EVS | 0.2-0.4 | Natural stabilization rate |
| MU | -0.5 to 0.0 | Degradation during fault |
| Downtime | 60-180s | Service unavailability |

---

### Baseline B: Manual SRE Response

**Description:** Simulated human operator response with realistic delays.

**Implementation:**
```yaml
experiment_config:
  name: "baseline_manual_sre"
  fault_injection: enabled
  remediation: delayed_manual
  alert_to_response_time: 120s  # 2 min alert fatigue
  diagnosis_time: 180s          # 3 min to understand
  action_time: 60s              # 1 min to execute
  total_expected_ttr: 360s      # 6 min total
```

**Simulation Method:**
1. Wait for alert trigger (simulated monitoring)
2. Add 120s delay (alert acknowledgment)
3. Add 180s delay (diagnosis)
4. Execute predetermined "correct" remediation
5. Measure recovery time from action

**Key Metrics:**
| Metric | Expected Range | Measurement |
|--------|---------------|-------------|
| TTR | 300-600s | Includes human delays |
| EVS | 0.7-0.9 | Humans usually succeed |
| MU | 0.2-0.5 | Improvement post-action |
| Downtime | 180-420s | Long due to delays |

---

### Baseline C: Rule-Based Auto-Remediation

**Description:** Traditional if-then-else remediation rules.

**Implementation:**
```python
# Rule-based remediation engine
class RuleBasedRemediator:
    RULES = {
        "cpu_stress": {
            "condition": "cpu_usage > 80%",
            "action": "scale_out_replicas",
            "parameters": {"increment": 1, "max_replicas": 5}
        },
        "memory_stress": {
            "condition": "memory_usage > 85%",
            "action": "increase_memory_limit",
            "parameters": {"increment": "256Mi"}
        },
        "pod_crash": {
            "condition": "restart_count > 3 in 5m",
            "action": "rollback_deployment",
            "parameters": {"revision": "previous"}
        }
    }
```

**Key Metrics:**
| Metric | Expected Range | Measurement |
|--------|---------------|-------------|
| TTR | 30-90s | Fast rule matching |
| EVS | 0.4-0.6 | Correct for known patterns |
| MU | 0.0-0.3 | Limited adaptation |
| False Positive | 10-20% | Overly aggressive rules |

---

### Treatment: AIM-EVM (LLM-Driven Remediation)

**Description:** Full AIM-EVM system with LLM planning, execution, and feedback.

**Expected Advantages:**
- Context-aware planning (considers cluster state)
- Multi-step remediation sequences
- Learning from feedback

**Key Metrics:**
| Metric | Target Range | Rationale |
|--------|-------------|-----------|
| TTR | 30-60s | Faster than manual, comparable to rules |
| EVS | 0.6-0.8 | Better than rules due to context |
| MU | 0.3-0.6 | Higher utility from targeted actions |
| Execution Failures | <20% | Code generation reliability |

---

## Comparison Matrix

| Metric | No Remediation | Manual SRE | Rule-Based | AIM-EVM |
|--------|---------------|------------|------------|---------|
| **TTR** | 120-300s | 300-600s | 30-90s | 30-60s |
| **EVS** | 0.2-0.4 | 0.7-0.9 | 0.4-0.6 | 0.6-0.8 |
| **MU** | -0.5-0.0 | 0.2-0.5 | 0.0-0.3 | 0.3-0.6 |
| **Adaptability** | None | High | None | High |
| **Speed** | N/A | Slow | Fast | Fast |
| **Novelty Handling** | None | High | None | Medium-High |

---

## Statistical Tests by Comparison

### Comparison 1: AIM-EVM vs No Remediation

**Hypothesis:**
- H0: EVS_AIM-EVM = EVS_NoRemediation
- H1: EVS_AIM-EVM > EVS_NoRemediation (one-tailed)

**Tests:**
- Chi-square test for EVS (proportions)
- Mann-Whitney U for TTR (likely non-normal)
- Report: Effect size (Cliff's delta), 95% CI

**Expected Outcome:** Large effect size, clear improvement

---

### Comparison 2: AIM-EVM vs Manual SRE

**Hypothesis:**
- H0: TTR_AIM-EVM = TTR_Manual
- H1: TTR_AIM-EVM < TTR_Manual (one-tailed)

**Tests:**
- Welch's t-test or Mann-Whitney U for TTR
- McNemar's test for EVS (paired if same faults)
- Report: Time savings in seconds and percentage

**Expected Outcome:** AIM-EVM faster, Manual may have higher EVS

---

### Comparison 3: AIM-EVM vs Rule-Based

**Hypothesis:**
- H0: EVS_AIM-EVM = EVS_RuleBased
- H1: EVS_AIM-EVM > EVS_RuleBased

**Tests:**
- Chi-square or Fisher's exact for EVS
- Compare false positive rates
- Report: NNT (Number Needed to Treat) equivalent

**Expected Outcome:** Similar speed, AIM-EVM better on novel faults

---

## Meaningful Metrics by Comparison

| Comparison | Most Meaningful Metric | Why |
|------------|----------------------|-----|
| vs No Remediation | EVS | Does intervention help at all? |
| vs Manual SRE | TTR | Automation value = time saved |
| vs Rule-Based | EVS on novel faults | LLM value = adaptability |

---

## Experimental Protocol

### Phase 1: Baseline Collection (Week 1-2)
1. Run 30 experiments each for No Remediation and Rule-Based
2. Collect SRE timing data from literature (simulated)

### Phase 2: AIM-EVM Collection (Week 3-4)
1. Run 30 experiments with full AIM-EVM pipeline
2. Ensure same fault scenarios as baselines

### Phase 3: Analysis (Week 5)
1. Perform statistical comparisons
2. Generate publication figures
3. Write results section

---

## Threats to Validity

### Internal Validity
- **Selection bias:** Use same fault scenarios across all baselines
- **Order effects:** Randomize experiment order
- **Cluster state:** Reset cluster between experiments

### External Validity
- **Fault representativeness:** Use realistic fault patterns from literature
- **Workload realism:** Use Robot Shop benchmark (standard)
- **Generalizability:** Limited to Kubernetes, microservices

### Construct Validity
- **EVS definition:** Binary may miss partial recovery
- **TTR measurement:** Dependent on threshold definition
- **MU calculation:** Sensitive to metric selection

### Conclusion Validity
- **Sample size:** Power analysis ensures adequate n
- **Statistical assumptions:** Test normality before parametric tests
- **Multiple comparisons:** Apply Bonferroni correction
