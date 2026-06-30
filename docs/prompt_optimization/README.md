# Prompt Optimization Documentation

This directory contains documentation and artifacts from the prompt optimization work to fix LLM-generated Ansible playbook issues.

## Overview

The prompt optimization effort addressed critical bugs in LLM-generated Ansible playbooks:
1. **Primary issue:** LLMs generated `kubernetes.core.k8s` with `replicas` field + Jinja2, causing "unhashable type: AnsibleMapping" errors
2. **Secondary issues:** Various Ansible syntax errors (loop constructs, dict access, etc.)

## Documents

### Summary Reports
- **`PROMPT_OPTIMIZATION_FINAL_REPORT.md`** - Comprehensive final report with all findings
- **`REAL_FIX_SUMMARY.md`** - Root cause analysis and solution
- **`GEMINI_INTEGRATION_SUMMARY.md`** - Gemini API integration notes
- **`PROMPT_FIX_SUMMARY.md`** - Initial fix attempt (superseded by final report)

### Artifacts
- **`executor_prompt.py.backup`** - Backup of prompt before optimization
- **`prompt_optimization_log.jsonl`** - Raw test results log

## Key Findings

### Root Cause
- **Not a Kubernetes API issue** - The error occurred in Ansible, not K8s API
- **Ansible kubernetes.core.k8s limitation** - Module has Jinja2 conflicts with `replicas` field
- **LLM training bias** - GPT-4o strongly biased toward kubernetes.core.k8s "best practice"

### Solution
Use **kubectl scale command** instead of kubernetes.core.k8s module:
```yaml
# CORRECT (works)
- name: Scale deployment
  command: kubectl scale deployment dispatch --replicas=2 -n robot-shop

# WRONG (fails with "unhashable type")
- name: Scale deployment
  kubernetes.core.k8s:
    state: present
    definition:
      spec:
        replicas: "{{ target_replicas | int }}"
```

### Optimal Prompt Strategy: "Triple Emphasis"
Mention critical constraints THREE times:
1. **Beginning** - RULE #1 at start of prompt
2. **Middle** - In content rules section
3. **End** - Final checkpoint before output

This overcomes attention degradation in long prompts (70KB+).

## Model Performance

| Model | Triple Emphasis Result | Recommendation |
|-------|----------------------|----------------|
| Gemini-2.5-Flash | ✅ 0 issues | **Use this** |
| GPT-4o | ❌ Multiple issues | Avoid |
| Claude Sonnet 4.5 | 🔄 Testing | Promising |

## Implementation

The optimized prompt is implemented in:
- `src/executor/executor_prompt.py` (current version)
- Backup available in this directory as `executor_prompt.py.backup`

## Testing

Test scripts available in: `tests/prompt_optimization/`

See `tests/prompt_optimization/README.md` for usage instructions.
