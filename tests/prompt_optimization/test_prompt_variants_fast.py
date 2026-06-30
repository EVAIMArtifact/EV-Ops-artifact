#!/usr/bin/env python3
"""
Fast Prompt Variant Testing - Tests 5 key prompt variations quickly

Tests focused variants based on learned patterns instead of 50 blind iterations.
Total time: ~5-10 minutes (5 variants × 2 models × ~30s per call)
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

from clients.llm_client import GPTLLMClient, GeminiLLMClient
from executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, EXECUTOR_USER_TEMPLATE


def create_prompt_variants():
    """Create 5 focused prompt variants based on learned patterns."""

    base_prompt = EXECUTOR_SYSTEM_PROMPT

    variants = {
        "baseline": {
            "name": "Baseline (Current Prompt)",
            "prompt": base_prompt
        },

        "kubectl_emphasis": {
            "name": "Strong kubectl scale emphasis",
            "prompt": base_prompt.replace(
                "- Use kubectl commands if necessary",
                "- **CRITICAL**: For replica scaling, ALWAYS use kubectl scale command, NEVER kubernetes.core.k8s\n"
                "  - Reason: Ansible kubernetes.core.k8s has Jinja2 quoting issues with replicas field\n"
                "  - ✅ CORRECT: shell: kubectl scale deployment {{ name }} --replicas={{ count }} -n {{ ns }}\n"
                "  - ❌ WRONG: kubernetes.core.k8s with definition.spec.replicas: \"{{ count }}\"\n"
                "- Use kubectl commands if necessary"
            )
        },

        "kubectl_with_example": {
            "name": "kubectl emphasis + concrete example",
            "prompt": base_prompt + "\n\n" +
                "**MANDATORY PATTERN FOR REPLICA SCALING:**\n"
                "```yaml\n"
                "- name: Scale replicas using kubectl\n"
                "  shell: >\n"
                "    kubectl scale deployment {{ service_name }}\n"
                "    --replicas={{ target_replicas | int }}\n"
                "    -n robot-shop\n"
                "  register: scale_result\n"
                "  changed_when: \"'scaled' in scale_result.stdout\"\n"
                "```\n"
                "DO NOT use kubernetes.core.k8s for replica changes.\n"
        },

        "learned_from_failures": {
            "name": "Learned from previous failures",
            "prompt": base_prompt + "\n\n" +
                "**CRITICAL LESSONS FROM PREVIOUS FAILURES:**\n\n"
                "1. NEVER use kubernetes.core.k8s with Jinja2 in replicas field\n"
                "   - Causes: 'unhashable type: AnsibleMapping' error\n"
                "   - Solution: Use kubectl scale command\n\n"
                "2. ALWAYS use | trim filter for cpu/memory values\n"
                "   - Without trim: \"{{ value }}Mi\" produces \" 356 Mi\" (spaces cause K8s API error)\n"
                "   - With trim: \"{{ value | trim }}Mi\" produces \"356Mi\" (correct)\n\n"
                "3. Complete all Jinja2 expressions\n"
                "   - Wrong: failed_when: deployment_json.rc !=\n"
                "   - Right: failed_when: deployment_json.rc != 0\n\n"
                "Use kubectl for ALL replica operations.\n"
        },

        "triple_emphasis": {
            "name": "Triple emphasis (beginning, middle, end)",
            "prompt": (
                "**RULE #1: USE KUBECTL FOR REPLICAS (NOT kubernetes.core.k8s)**\n\n" +
                base_prompt.replace(
                    "Content rules:",
                    "Content rules:\n"
                    "- **MANDATORY**: For replica scaling, use kubectl scale command\n"
                    "  Example: shell: kubectl scale deployment {{ name }} --replicas={{ count }} -n {{ ns }}\n"
                ) + "\n\n" +
                "**FINAL CHECKPOINT BEFORE OUTPUTTING:**\n"
                "Search your generated playbook for 'kubernetes.core.k8s' + 'replicas:'\n"
                "If found together, REPLACE with kubectl scale command.\n"
                "This pattern causes 'unhashable type' errors 100% of the time.\n"
            )
        }
    }

    return variants


def test_prompt_variant(variant_name, prompt, model_name, model_config):
    """Test a single prompt variant with a model."""

    # Create test scenario
    strategy = """1. Temporarily increase the CPU limit per pod to provide more headroom.
2. Gradually increase the number of replicas to distribute the load evenly.
3. Once stabilized, gradually revert temporary increases."""

    user_prompt = EXECUTOR_USER_TEMPLATE.format(
        strategy=strategy,
        service="dispatch",
        examples="None available"
    )

    # Generate playbook
    if "gpt" in model_name.lower():
        client = GPTLLMClient(model_config)
    else:
        client = GeminiLLMClient(model_config)

    try:
        playbook = client.generate(prompt, user_prompt)
    except Exception as e:
        return {
            "variant": variant_name,
            "model": model_name,
            "success": False,
            "error": str(e),
            "playbook": None
        }

    # Analyze playbook
    issues = analyze_playbook(playbook)

    result = {
        "variant": variant_name,
        "model": model_name,
        "success": len(issues) == 0,
        "issues": issues,
        "playbook_length": len(playbook),
        "uses_kubectl_scale": "kubectl scale" in playbook.lower(),
        "uses_k8s_module_replicas": (
            "kubernetes.core.k8s" in playbook and
            "replicas:" in playbook
        ),
        "trim_filter_count": playbook.count("| trim"),
        "playbook_snippet": playbook[:500]
    }

    return result


def analyze_playbook(playbook):
    """Analyze playbook for known issues."""
    issues = []

    # Issue 1: kubernetes.core.k8s with replicas Jinja2
    if "kubernetes.core.k8s" in playbook:
        k8s_pattern = r'kubernetes\.core\.k8s:.*?definition:.*?replicas:\s*["\']?\{\{.*?\}\}["\']?'
        if re.search(k8s_pattern, playbook, re.DOTALL):
            issues.append("CRITICAL: kubernetes.core.k8s with Jinja2 replicas")

    # Issue 2: Missing | trim in resource values
    resource_pattern = r'(cpu|memory):\s*"\{\{[^}]*\}\}(m|Mi|Gi)"'
    trim_missing = []
    for match in re.finditer(resource_pattern, playbook):
        if "| trim" not in match.group(0):
            trim_missing.append(match.group(0)[:50])

    if trim_missing:
        issues.append(f"Missing | trim in {len(trim_missing)} resource value(s)")

    # Issue 3: Incomplete Jinja2
    incomplete_pattern = r'(failed_when|when):\s*\S+\s*(!=|==)\s*$'
    for match in re.finditer(incomplete_pattern, playbook, re.MULTILINE):
        issues.append(f"Incomplete Jinja2: {match.group(0).strip()}")

    return issues


def run_fast_test():
    """Run fast variant testing."""

    print("="*80)
    print("FAST PROMPT VARIANT TESTING")
    print("="*80)
    print(f"Started at: {datetime.now().strftime('%H:%M:%S')}")
    print()

    # Model configurations
    models = {
        "GPT-4o": {
            "model_id": "gpt-4o",
            "api_key": "***",
            "temperature": 0.0,
            "max_tokens": 4096
        },
        "Gemini-2.5-Flash": {
            "model_id": "models/gemini-2.5-flash",
            "api_key": "YOUR_GEMINI_API_KEY",
            "temperature": 0.1,
            "max_tokens": 8192
        }
    }

    # Create variants
    variants = create_prompt_variants()

    print(f"Testing {len(variants)} prompt variants × {len(models)} models = {len(variants) * len(models)} total tests")
    print()

    # Track results
    all_results = []
    best_per_model = {model: {"variant": None, "issues": 999} for model in models}

    # Test each variant
    for i, (variant_id, variant_info) in enumerate(variants.items(), 1):
        print(f"\n{'='*80}")
        print(f"VARIANT {i}/{len(variants)}: {variant_info['name']}")
        print(f"{'='*80}")

        for model_name, model_config in models.items():
            print(f"\n🧪 Testing with {model_name}...", end=" ", flush=True)

            result = test_prompt_variant(
                variant_id,
                variant_info['prompt'],
                model_name,
                model_config
            )

            all_results.append(result)

            # Check if best for this model
            issue_count = len(result.get("issues", []))
            if issue_count < best_per_model[model_name]["issues"]:
                best_per_model[model_name] = {
                    "variant": variant_id,
                    "variant_name": variant_info["name"],
                    "issues": issue_count,
                    "result": result
                }

            # Print result
            if result["success"]:
                print("✅ PASS")
                print(f"   Uses kubectl scale: {result['uses_kubectl_scale']}")
                print(f"   | trim count: {result['trim_filter_count']}")
            else:
                print(f"❌ FAIL ({issue_count} issues)")
                for issue in result["issues"]:
                    print(f"   - {issue}")

    # Report best results
    print(f"\n\n{'='*80}")
    print("BEST RESULTS PER MODEL")
    print(f"{'='*80}")

    for model_name, best in best_per_model.items():
        print(f"\n{model_name}:")
        print(f"  Best variant: {best['variant']} ({best['variant_name']})")
        print(f"  Issue count: {best['issues']}")
        if best['issues'] == 0:
            print(f"  ✅ PERFECT - No issues found!")
        else:
            print(f"  Issues: {best['result']['issues']}")

    # Save results
    results_file = Path("prompt_variant_test_results.json")
    with open(results_file, "w") as f:
        json.dump({
            "timestamp": str(datetime.now()),
            "all_results": all_results,
            "best_per_model": {
                model: {
                    "variant": best["variant"],
                    "variant_name": best["variant_name"],
                    "issues": best["issues"]
                }
                for model, best in best_per_model.items()
            }
        }, f, indent=2)

    print(f"\n✓ Results saved to {results_file}")

    # Find overall best variant
    variant_scores = {}
    for result in all_results:
        variant = result["variant"]
        if variant not in variant_scores:
            variant_scores[variant] = {"total_issues": 0, "count": 0}
        variant_scores[variant]["total_issues"] += len(result["issues"])
        variant_scores[variant]["count"] += 1

    best_variant = min(variant_scores.items(), key=lambda x: x[1]["total_issues"])

    print(f"\n{'='*80}")
    print("RECOMMENDATION")
    print(f"{'='*80}")
    print(f"Best overall variant: {best_variant[0]}")
    print(f"Total issues across both models: {best_variant[1]['total_issues']}")
    print(f"\nNext steps:")
    print(f"1. Review the '{best_variant[0]}' variant prompt")
    print(f"2. Update src/executor/executor_prompt.py with this prompt")
    print(f"3. Run production experiments")
    print(f"4. Validate with systems-research-evaluator agent")

    print(f"\nCompleted at: {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    run_fast_test()
