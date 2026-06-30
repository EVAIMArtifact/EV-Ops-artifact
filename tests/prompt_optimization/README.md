# Prompt Optimization Tests

This directory contains test scripts used to optimize the Ansible playbook generation prompts for LLM-based remediation.

## Purpose

These tests were created to systematically find the optimal prompt configuration that ensures LLMs generate correct Ansible playbooks, specifically:
- Using `kubectl scale` commands instead of `kubernetes.core.k8s` module for replica scaling
- Avoiding Jinja2 + kubernetes.core.k8s conflicts that cause "unhashable type: AnsibleMapping" errors

## Test Scripts

### Quick Unit Tests
- **`test_claude_quick.py`** - Test Claude Sonnet 4.5 with kubectl constraint
- **`test_gemini_quick.py`** - Test Gemini 2.5 Flash with kubectl constraint
- **`test_prompt_quick.py`** - Test GPT-4o with replicas quoting check
- **`test_prompt_realistic.py`** - Test with realistic production-like prompts

### Systematic Testing
- **`test_prompt_variants_fast.py`** - Fast focused testing of 5 strategic prompt variants
- **`optimize_prompt.py`** - Full 50-iteration iterative learning system (slow)

## Key Findings

From systematic testing, we found:

| Model | Best Variant | Issues Found |
|-------|-------------|--------------|
| **Gemini-2.5-Flash** | triple_emphasis | **0 issues** ✅ |
| **GPT-4o** | (none) | 1+ issues (all variants) ❌ |
| **Claude Sonnet 4.5** | (testing) | TBD |

**Winner:** "Triple emphasis" pattern - mentioning critical constraints at:
1. Beginning of prompt
2. Middle of prompt (in content rules)
3. End of prompt (final checkpoint)

This overcomes LLM attention degradation in long (70KB+) prompts.

## Usage

```bash
# Quick test (2-3 seconds)
python tests/prompt_optimization/test_claude_quick.py

# Systematic variant testing (5-10 minutes)
python tests/prompt_optimization/test_prompt_variants_fast.py

# Full iterative optimization (1-2 hours)
python tests/prompt_optimization/optimize_prompt.py
```

## Related Documentation

See `docs/prompt_optimization/` for detailed reports on optimization process and findings.
