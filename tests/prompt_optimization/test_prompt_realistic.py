#!/usr/bin/env python3
"""
Realistic test with full context (long strategy + 5 previous examples).
Tests if the replicas bug reappears with production-level prompt complexity.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from clients.llm_client import GPTLLMClient
from executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, EXECUTOR_USER_TEMPLATE


def create_realistic_test_prompt():
    """Create a realistic prompt with full complexity (like production runs)."""

    # Real strategy from experiment results (8 steps)
    strategy = """1. Identify and terminate any non-essential processes or workloads that are consuming CPU resources on the dispatch service to reduce CPU stress. 2. Temporarily increase the CPU limit per pod to provide more headroom for handling the current load, ensuring it remains within the node's capacity. 3. Gradually increase the number of replicas for the dispatch service to distribute the load more evenly, ensuring the infrastructure can support the additional replicas without causing resource contention. 4. Ensure that all replicas are configured consistently in terms of resource limits and environment to maintain balanced load distribution. 5. Temporarily disable or degrade any optional or low-priority features that are CPU-intensive to reduce the per-request CPU cost. 6. Adjust any internal concurrency or worker-pool settings within the dispatch service to prevent individual pods from over-saturating CPU, prioritizing predictable latency over maximum throughput. 7. Implement or tighten upstream backpressure or rate limiting to control incoming request volume, ensuring it remains within the capacity of the adjusted replicas and CPU limits. 8. Once the service stabilizes, gradually revert any temporary increases in CPU limits and replica counts to their baseline values, maintaining sufficient headroom to prevent reintroducing CPU saturation."""

    # Simplified previous examples (3 short examples instead of 5 long ones)
    examples = """Example 1:
Service: dispatch
Fault: custom-cpu_stress-dispatch-123
Strategy: Increase CPU limit and scale replicas.
Status: error (EVS=1) (MU=N/A) (reward=0.08) (ansible_score=0.5)
Error: Type error on replicas field
Playbook YAML:
  - name: Scale up
    kubernetes.core.k8s:
      state: present
      definition:
        spec:
          replicas: "{{ target_replicas | int }}"  # BUG: This was quoted!

Example 2:
Service: cart
Fault: custom-memory_stress-cart-456
Strategy: Increase memory limit and scale.
Status: error (EVS=1) (MU=N/A) (reward=0.09) (ansible_score=1.0)
Error: Replicas type error
Playbook YAML:
  - name: Scale cart
    kubernetes.core.k8s:
      definition:
        spec:
          replicas: {{ current_replicas.stdout | int + 1 }}  # This needed quotes for Ansible syntax!

Example 3:
Service: payment
Fault: custom-cpu_stress-payment-789
Strategy: Simple CPU increase.
Status: success (EVS=1) (MU=0.15) (reward=0.95) (ansible_score=1.0)
Playbook YAML:
  - name: Apply CPU limit
    kubernetes.core.k8s:
      definition:
        spec:
          template:
            spec:
              containers:
                - resources:
                    limits:
                      cpu: "{{ new_cpu | trim }}m"
"""

    service = "dispatch"

    user_prompt = EXECUTOR_USER_TEMPLATE.format(
        strategy=strategy,
        service=service,
        examples=examples
    )

    return user_prompt


def check_for_replicas_bug(playbook_yaml: str) -> tuple[bool, list[str]]:
    """Check if playbook has the replicas quoting bug."""
    if not playbook_yaml or not playbook_yaml.strip():
        return True, ["ERROR: Empty playbook returned"]

    lines = playbook_yaml.split('\n')
    errors = []

    # Pattern: replicas: "{{ ... }}" (WRONG - quoted)
    quoted_pattern = re.compile(r'^\s*replicas:\s*"{{.*}}"', re.IGNORECASE)
    # Pattern: replicas: {{ ... }} (CORRECT - unquoted)
    unquoted_pattern = re.compile(r'^\s*replicas:\s*{{.*}}', re.IGNORECASE)

    for i, line in enumerate(lines, 1):
        if quoted_pattern.search(line):
            errors.append(f"Line {i}: {line.strip()} (BUG: replicas is QUOTED)")
        elif unquoted_pattern.search(line):
            print(f"✓ Line {i}: {line.strip()} (CORRECT: unquoted)")

    return len(errors) > 0, errors


def main():
    print("=" * 80)
    print("REALISTIC PROMPT TEST - Full Context Complexity")
    print("=" * 80)
    print()

    print("Initializing GPT-4o client...")
    model_config = {
        "model_id": "gpt-4o",
        "api_key": "***",
        "temperature": 0.0,
        "max_tokens": 4096
    }
    client = GPTLLMClient(model_config)

    print("Creating realistic test prompt (long strategy + 3 examples)...")
    user_prompt = create_realistic_test_prompt()

    print(f"System prompt: {len(EXECUTOR_SYSTEM_PROMPT)} chars")
    print(f"User prompt: {len(user_prompt)} chars")
    print(f"Total context: ~{len(EXECUTOR_SYSTEM_PROMPT) + len(user_prompt)} chars")
    print()

    print("Calling GPT-4o...")
    print("-" * 80)

    try:
        playbook_yaml = client.generate(
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt
        )

        print(f"Response: {len(playbook_yaml)} chars")
        print()

        has_bug, errors = check_for_replicas_bug(playbook_yaml)

        print("-" * 80)
        print()

        if has_bug:
            print("❌ TEST FAILED - Replicas bug detected with realistic context!")
            print()
            for error in errors:
                print(f"  {error}")
            print()
            print("This suggests the bug reappears with production-level prompt complexity.")
            print("Solution: Add EVEN MORE emphasis on replicas rule in the prompt.")
            print()
            return 1
        else:
            if 'replicas:' in playbook_yaml.lower():
                print("✅ TEST PASSED - Replicas correctly unquoted even with realistic context!")
                print()
                print("The current prompt is sufficient for production runs.")
                print()
                return 0
            else:
                print("⚠️  No replicas field found")
                print()
                return 2

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
