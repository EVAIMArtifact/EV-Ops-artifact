#!/usr/bin/env python3
"""
Iterative Prompt Optimization System for Ansible Playbook Generation

This script:
1. Tests current prompt with both GPT-4o and Gemini 2.5 Flash
2. Analyzes failures from previous experiments
3. Automatically improves the prompt based on learned patterns
4. Runs up to 50 iterations per model to find optimal prompt
"""

import sys
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent / "src"))

from clients.llm_client import GPTLLMClient, GeminiLLMClient
from executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, EXECUTOR_USER_TEMPLATE


class PromptOptimizer:
    """Iterative prompt optimization with learning from failures."""

    def __init__(self):
        self.results_dir = Path("src/experiment_results")
        self.optimization_log = Path("prompt_optimization_log.jsonl")

        # Track learning
        self.successful_patterns = []
        self.failure_patterns = []
        self.error_frequencies = defaultdict(int)

        # Current prompt versions
        self.current_system_prompt = EXECUTOR_SYSTEM_PROMPT
        self.iteration = 0

    def analyze_previous_experiments(self) -> Dict:
        """Analyze all previous experiment results to learn patterns."""
        print("\n" + "="*80)
        print("ANALYZING PREVIOUS EXPERIMENT RESULTS")
        print("="*80)

        successes = []
        failures = []

        for exp_dir in self.results_dir.iterdir():
            if not exp_dir.is_dir():
                continue

            feedback_file = exp_dir / "feedback.json"
            playbook_file = exp_dir / "playbook.yaml"
            ansible_log = exp_dir / "ansible_output.log"

            if not all([feedback_file.exists(), playbook_file.exists()]):
                continue

            # Load feedback
            with open(feedback_file) as f:
                feedback = json.load(f)

            # Load playbook
            with open(playbook_file) as f:
                playbook = f.read()

            # Load errors if available
            errors = []
            if ansible_log.exists():
                with open(ansible_log) as f:
                    log = f.read()
                    if "FAILED" in log or "ERROR" in log:
                        errors = self._extract_errors(log)

            result = {
                "exp_name": exp_dir.name,
                "playbook": playbook,
                "playbook_length": len(playbook),
                "feedback": feedback,
                "errors": errors,
                "success": feedback.get("ansible_score", 0) > 0.5
            }

            if result["success"]:
                successes.append(result)
            else:
                failures.append(result)

        print(f"\n✅ Successful experiments: {len(successes)}")
        print(f"❌ Failed experiments: {len(failures)}")

        # Analyze patterns
        self._learn_from_successes(successes)
        self._learn_from_failures(failures)

        return {
            "total_experiments": len(successes) + len(failures),
            "success_count": len(successes),
            "failure_count": len(failures),
            "success_rate": len(successes) / max(1, len(successes) + len(failures))
        }

    def _extract_errors(self, log: str) -> List[str]:
        """Extract error messages from Ansible log."""
        errors = []

        # Pattern 1: Ansible FAILED! messages
        for match in re.finditer(r'fatal:.*FAILED! => ({.*?})', log, re.DOTALL):
            errors.append(match.group(1)[:200])

        # Pattern 2: ERROR! messages
        for match in re.finditer(r'ERROR! (.+)', log):
            errors.append(match.group(1)[:200])

        # Pattern 3: Common Ansible errors
        error_patterns = [
            r"unhashable type",
            r"cannot unmarshal string",
            r"unexpected 'end of statement block'",
            r"Invalid value:",
            r"Syntax Error while loading YAML"
        ]

        for pattern in error_patterns:
            if re.search(pattern, log, re.IGNORECASE):
                # Find context around error
                for line in log.split('\n'):
                    if re.search(pattern, line, re.IGNORECASE):
                        errors.append(line.strip())

        return errors

    def _learn_from_successes(self, successes: List[Dict]):
        """Extract successful patterns."""
        print("\n📚 Learning from successful experiments...")

        for result in successes:
            playbook = result["playbook"]

            # Check if uses kubectl scale
            if "kubectl scale" in playbook.lower():
                self.successful_patterns.append("kubectl_scale_for_replicas")
                print("  ✓ Found: kubectl scale for replica management")

            # Check if uses kubernetes.core.k8s for resources
            if "kubernetes.core.k8s" in playbook and "resources:" in playbook:
                self.successful_patterns.append("k8s_module_for_resources")
                print("  ✓ Found: kubernetes.core.k8s for resource limits")

            # Check for | trim usage
            trim_count = playbook.count("| trim")
            if trim_count > 0:
                self.successful_patterns.append(f"trim_filter_used_{trim_count}_times")
                print(f"  ✓ Found: | trim filter used {trim_count} times")

    def _learn_from_failures(self, failures: List[Dict]):
        """Extract failure patterns and errors."""
        print("\n🔍 Learning from failed experiments...")

        for result in failures:
            playbook = result["playbook"]
            errors = result["errors"]

            # Track error types
            for error in errors:
                if "unhashable type" in error:
                    self.error_frequencies["unhashable_type_ansible"] += 1
                    # Check if it's replicas related
                    if "replicas:" in playbook and "kubernetes.core.k8s" in playbook:
                        self.failure_patterns.append("k8s_module_with_replicas_jinja")
                        print("  ✗ Found: kubernetes.core.k8s with Jinja2 replicas")

                elif "cannot unmarshal string" in error:
                    self.error_frequencies["k8s_type_error"] += 1
                    self.failure_patterns.append("quoted_numeric_field")
                    print("  ✗ Found: Quoted numeric field causing type error")

                elif "end of statement block" in error:
                    self.error_frequencies["jinja_syntax_error"] += 1
                    self.failure_patterns.append("incomplete_jinja_expression")
                    print("  ✗ Found: Incomplete Jinja2 expression")

        print(f"\n📊 Error frequency distribution:")
        for error_type, count in sorted(self.error_frequencies.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {error_type}: {count} occurrences")

    def generate_improved_prompt(self, iteration: int, previous_errors: List[str]) -> str:
        """Generate improved prompt based on learned patterns."""
        print(f"\n🔧 Generating improved prompt (iteration {iteration})...")

        improvements = []

        # Rule 1: If k8s_module_with_replicas_jinja is common failure
        if "k8s_module_with_replicas_jinja" in self.failure_patterns:
            improvements.append(
                "\n**CRITICAL RULE #{}: KUBECTL FOR REPLICAS**\n"
                "NEVER use kubernetes.core.k8s for replica scaling. ALWAYS use kubectl scale.\n"
                "This is the #1 cause of 'unhashable type' errors.\n"
                "✅ CORRECT: shell: kubectl scale deployment {{{{ name }}}} --replicas={{{{ count }}}} -n {{{{ ns }}}}\n"
                "❌ WRONG: kubernetes.core.k8s: definition: spec: replicas: \"{{{{ count }}}}\"\n"
                .format(len(improvements) + 1)
            )

        # Rule 2: If quoted numeric fields cause errors
        if "quoted_numeric_field" in self.failure_patterns:
            improvements.append(
                "\n**CRITICAL RULE #{}: USE | trim FOR RESOURCE VALUES**\n"
                "ALL Jinja2 expressions for cpu/memory MUST use | trim filter.\n"
                "✅ CORRECT: memory: \"{{{{ value | trim }}}}Mi\"\n"
                "❌ WRONG: memory: \"{{{{ value }}}}Mi\" (causes \" 356 Mi\" with spaces)\n"
                .format(len(improvements) + 1)
            )

        # Rule 3: If Jinja2 syntax errors occur
        if "incomplete_jinja_expression" in self.failure_patterns:
            improvements.append(
                "\n**CRITICAL RULE #{}: COMPLETE JINJA2 EXPRESSIONS**\n"
                "All Jinja2 expressions MUST be complete with proper closing.\n"
                "failed_when: condition == value (correct)\n"
                "failed_when: condition != (WRONG - incomplete!)\n"
                .format(len(improvements) + 1)
            )

        # Add iteration-specific emphasis
        if iteration > 0 and previous_errors:
            improvements.append(
                f"\n**ITERATION {iteration} LEARNING**\n"
                "Previous attempt had these errors:\n" +
                "\n".join(f"- {err[:150]}" for err in previous_errors[:3]) +
                "\n\nAVOID these patterns in this iteration!\n"
            )

        # Construct improved prompt
        if improvements:
            improvement_section = "\n".join(improvements)
            improved_prompt = (
                f"{self.current_system_prompt}\n\n"
                f"===== LEARNED RULES (ITERATION {iteration}) =====\n"
                f"{improvement_section}\n"
                f"===== END LEARNED RULES =====\n"
            )

            print(f"✓ Added {len(improvements)} learned rules to prompt")
            return improved_prompt

        return self.current_system_prompt

    def test_prompt(self, model_name: str, model_config: Dict, system_prompt: str) -> Dict:
        """Test a prompt variant with a model."""
        print(f"\n🧪 Testing with {model_name}...")

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
            playbook = client.generate(system_prompt, user_prompt)
        except Exception as e:
            return {
                "model": model_name,
                "success": False,
                "error": str(e),
                "playbook": None
            }

        # Analyze generated playbook
        issues = self._analyze_playbook(playbook)

        result = {
            "model": model_name,
            "success": len(issues) == 0,
            "issues": issues,
            "playbook_length": len(playbook),
            "uses_kubectl_scale": "kubectl scale" in playbook.lower(),
            "uses_k8s_module_replicas": (
                "kubernetes.core.k8s" in playbook and
                "replicas:" in playbook
            ),
            "trim_filter_count": playbook.count("| trim")
        }

        return result

    def _analyze_playbook(self, playbook: str) -> List[str]:
        """Analyze playbook for known issues."""
        issues = []

        # Issue 1: kubernetes.core.k8s with replicas Jinja2
        if "kubernetes.core.k8s" in playbook:
            # Check for replicas with Jinja2 in definition block
            k8s_pattern = r'kubernetes\.core\.k8s:.*?definition:.*?replicas:\s*["\']?\{\{.*?\}\}["\']?'
            if re.search(k8s_pattern, playbook, re.DOTALL):
                issues.append("ISSUE: kubernetes.core.k8s with Jinja2 replicas (will cause unhashable type error)")

        # Issue 2: Missing | trim in resource values
        resource_pattern = r'(cpu|memory):\s*"\{\{[^}]*\}\}(m|Mi|Gi)"'
        for match in re.finditer(resource_pattern, playbook):
            if "| trim" not in match.group(0):
                issues.append(f"ISSUE: Missing | trim in resource value: {match.group(0)}")

        # Issue 3: Incomplete Jinja2
        incomplete_pattern = r'(failed_when|when):\s*\S+\s*(!=|==)\s*$'
        for match in re.finditer(incomplete_pattern, playbook, re.MULTILINE):
            issues.append(f"ISSUE: Incomplete Jinja2 expression: {match.group(0)}")

        # Issue 4: Quoted replicas in kubectl command (should be unquoted)
        kubectl_pattern = r'kubectl scale.*--replicas=["\']'
        if re.search(kubectl_pattern, playbook):
            issues.append("ISSUE: Quoted replicas in kubectl scale command")

        return issues

    def run_optimization_loop(self, max_iterations: int = 50):
        """Run iterative optimization loop."""
        print("\n" + "="*80)
        print("STARTING PROMPT OPTIMIZATION LOOP")
        print("="*80)

        # Step 1: Analyze previous experiments
        analysis = self.analyze_previous_experiments()

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

        # Track best results
        best_results = {model: {"iteration": 0, "success": False, "issues": 999} for model in models}

        # Optimization loop
        for iteration in range(max_iterations):
            print(f"\n{'='*80}")
            print(f"ITERATION {iteration + 1}/{max_iterations}")
            print(f"{'='*80}")

            # Generate improved prompt
            previous_errors = []
            if iteration > 0:
                # Collect errors from previous iteration
                for model_name in models:
                    if model_name in best_results and "result" in best_results[model_name]:
                        result = best_results[model_name]["result"]
                        if "issues" in result:
                            previous_errors.extend(result.get("issues", []))

            current_prompt = self.generate_improved_prompt(iteration, previous_errors)

            # Test with all models
            iteration_results = {}
            for model_name, model_config in models.items():
                result = self.test_prompt(model_name, model_config, current_prompt)
                iteration_results[model_name] = result

                # Check if this is best for this model
                issue_count = len(result.get("issues", []))
                if issue_count < best_results[model_name].get("issues", 999):
                    best_results[model_name] = {
                        "iteration": iteration,
                        "success": result["success"],
                        "issues": issue_count,
                        "result": result,
                        "prompt": current_prompt
                    }
                    print(f"  🌟 NEW BEST for {model_name}: {issue_count} issues")

                # Print result
                status = "✅ PASS" if result["success"] else f"❌ FAIL ({issue_count} issues)"
                print(f"  {model_name}: {status}")
                if not result["success"]:
                    for issue in result["issues"][:3]:
                        print(f"    - {issue}")

            # Log iteration
            self._log_iteration(iteration, iteration_results, current_prompt)

            # Check if we found perfect prompt for both models
            if all(best_results[m]["issues"] == 0 for m in models):
                print(f"\n🎉 PERFECT PROMPT FOUND at iteration {iteration + 1}!")
                break

        # Report best results
        self._report_best_results(best_results)
        return best_results

    def _log_iteration(self, iteration: int, results: Dict, prompt: str):
        """Log iteration results."""
        log_entry = {
            "iteration": iteration,
            "timestamp": str(Path().absolute()),
            "results": {
                model: {
                    "success": res.get("success"),
                    "issues": res.get("issues", []),
                    "uses_kubectl_scale": res.get("uses_kubectl_scale"),
                    "trim_count": res.get("trim_filter_count")
                }
                for model, res in results.items()
            }
        }

        with open(self.optimization_log, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def _report_best_results(self, best_results: Dict):
        """Report best prompts found."""
        print("\n" + "="*80)
        print("OPTIMIZATION COMPLETE - BEST RESULTS")
        print("="*80)

        for model_name, best in best_results.items():
            print(f"\n{model_name}:")
            print(f"  Best iteration: {best['iteration']}")
            print(f"  Success: {best['success']}")
            print(f"  Issue count: {best['issues']}")

            if best.get("result"):
                result = best["result"]
                print(f"  Uses kubectl scale: {result.get('uses_kubectl_scale')}")
                print(f"  | trim count: {result.get('trim_filter_count')}")

        # Save best prompts
        for model_name, best in best_results.items():
            if best.get("prompt"):
                filename = f"best_prompt_{model_name.lower().replace('-', '_')}.txt"
                with open(filename, "w") as f:
                    f.write(best["prompt"])
                print(f"\n✓ Saved best prompt for {model_name} to {filename}")


def main():
    optimizer = PromptOptimizer()
    best_results = optimizer.run_optimization_loop(max_iterations=50)

    print("\n" + "="*80)
    print("NEXT STEPS:")
    print("="*80)
    print("1. Review best_prompt_*.txt files")
    print("2. Update src/executor/executor_prompt.py with best prompt")
    print("3. Run production experiments with test3_gemini.json")
    print("4. Validate results with systems-research-evaluator agent")


if __name__ == "__main__":
    main()
