# Action Plan: AIM-EVM Thesis Experiments

## Priority 1: Fix Jinja2 Type Issue (BLOCKING)

**Estimated Time:** 2-4 hours

**Problem:** Ansible playbook generates `replicas: '{{ expr }}'` which produces string, but Kubernetes expects int32.

**Solution Options:**

### Option A: Post-processing Validation (Recommended)
```python
# In src/executor/playbook_generator.py

def validate_playbook(playbook: dict) -> dict:
    """Validate and fix common type issues in generated playbooks."""

    for task in playbook.get('tasks', []):
        if 'k8s' in task or 'kubernetes' in task:
            spec = task.get('k8s', {}).get('definition', {}).get('spec', {})

            # Fix replica count type
            if 'replicas' in spec:
                replicas = spec['replicas']
                if isinstance(replicas, str) and replicas.startswith('{{'):
                    # Convert Jinja2 expression to int filter
                    spec['replicas'] = f"{{{{ ({replicas[2:-2].strip()}) | int }}}}"
                elif isinstance(replicas, str):
                    spec['replicas'] = int(replicas)

    return playbook
```

### Option B: Prompt Engineering
Add explicit type instructions to LLM prompt:
```
IMPORTANT: When generating Kubernetes manifests:
- replicas MUST be an integer, not a string
- Use Jinja2 filter: {{ variable | int }} for numeric fields
- resource limits should use proper units (e.g., "256Mi", "500m")
```

### Option C: Schema Validation
```python
from kubernetes import client
from kubernetes.client.rest import ApiException

def validate_k8s_manifest(manifest: dict) -> Tuple[bool, str]:
    """Validate manifest against K8s schema before execution."""
    try:
        # Use kubectl --dry-run=client for validation
        # Or use kubernetes-validate library
        pass
    except ApiException as e:
        return False, str(e)
    return True, ""
```

**Recommendation:** Implement Option A + Option B together

---

## Priority 2: Re-run Cart-CPU-Stress Experiment

**After fixing Jinja2 issue:**

```bash
# Run single experiment to verify fix
python -m src.run_batch \
    --fault cpu_stress \
    --service cart \
    --duration 180 \
    --mode reactive \
    --dry-run

# If dry-run succeeds, run live
python -m src.run_batch \
    --fault cpu_stress \
    --service cart \
    --duration 180 \
    --mode reactive
```

**Success Criteria:**
- ansible_score = 1.0
- EVS can be 0 or 1 (depends on remediation effectiveness)
- No Jinja2 type errors in logs

---

## Priority 3: Establish Baselines (Week 1)

### No Remediation Baseline
```bash
for i in {1..30}; do
    python -m src.run_batch \
        --fault cpu_stress \
        --service cart \
        --duration 180 \
        --mode baseline_no_remediation \
        --trial $i
done
```

### Rule-Based Baseline
```bash
for i in {1..30}; do
    python -m src.run_batch \
        --fault cpu_stress \
        --service cart \
        --duration 180 \
        --mode baseline_rule_based \
        --trial $i
done
```

---

## Priority 4: Main Experiment Batch (Week 2-3)

### Experiment Matrix

| Fault Type | Service | Duration | n per condition |
|------------|---------|----------|-----------------|
| cpu_stress | cart | 180s | 30 |
| cpu_stress | payment | 180s | 30 |
| memory_stress | cart | 180s | 30 |
| memory_stress | catalogue | 180s | 30 |
| pod_crash | shipping | N/A | 30 |
| pod_crash | user | N/A | 30 |

**Total: 180 experiments (30 x 6 scenarios)**

### Execution Script
```bash
#!/bin/bash
# run_experiment_batch.sh

FAULTS=("cpu_stress" "memory_stress" "pod_crash")
SERVICES=("cart" "payment" "catalogue" "shipping" "user")
N_TRIALS=30

for fault in "${FAULTS[@]}"; do
    for service in "${SERVICES[@]}"; do
        for trial in $(seq 1 $N_TRIALS); do
            echo "Running: $fault on $service, trial $trial"
            python -m src.run_batch \
                --fault "$fault" \
                --service "$service" \
                --duration 180 \
                --mode reactive \
                --trial "$trial" \
                --output-dir "results/${fault}_${service}"

            # Cool-down period between experiments
            sleep 60
        done
    done
done
```

---

## Priority 5: Data Analysis (Week 4)

### Analysis Scripts to Create

1. **aggregate_results.py** - Combine all experiment JSONs
2. **statistical_analysis.py** - Run hypothesis tests
3. **visualization.py** - Generate publication figures
4. **learning_curve.py** - Analyze feedback improvement over time

### Key Analyses

```python
# 1. Primary comparison: AIM-EVM vs No Remediation
from scipy.stats import mannwhitneyu, chi2_contingency

# EVS comparison (binary)
contingency = [[evs_aim_success, evs_aim_fail],
               [evs_baseline_success, evs_baseline_fail]]
chi2, p_value, dof, expected = chi2_contingency(contingency)

# TTR comparison (continuous)
stat, p_value = mannwhitneyu(ttr_aim, ttr_baseline, alternative='less')

# 2. Effect sizes
from scipy.stats import norm
cliffs_delta = compute_cliffs_delta(ttr_aim, ttr_baseline)
cohens_h = compute_cohens_h(evs_aim_rate, evs_baseline_rate)

# 3. Learning curve analysis
# Plot EVS over sequential trials to show improvement
```

---

## Timeline

| Week | Tasks | Deliverables |
|------|-------|--------------|
| W1 | Fix Jinja2, verify fix, run baselines | Working pipeline, 60 baseline experiments |
| W2 | Run AIM-EVM experiments (batch 1) | 90 treatment experiments |
| W3 | Run AIM-EVM experiments (batch 2) | 90 treatment experiments |
| W4 | Statistical analysis | Results tables, figures |
| W5 | Write results section | Thesis chapter draft |

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| More execution bugs | Medium | High | Dry-run all new playbooks first |
| Cluster instability | Low | High | Reset cluster daily, use namespaces |
| Insufficient power | Medium | Medium | Start analysis early, add trials if needed |
| LLM API rate limits | Low | Medium | Implement exponential backoff |

---

## Definition of Done

The thesis experiments are complete when:

- [ ] Jinja2 type issue fixed and verified
- [ ] At least 30 experiments per condition completed
- [ ] Execution success rate > 80% (ansible_score)
- [ ] Statistical tests show p < 0.05 for primary hypotheses
- [ ] Effect sizes computed and reported
- [ ] Publication-quality figures generated
- [ ] Threats to validity section written
