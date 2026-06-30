#!/usr/bin/env python3
"""
Quick test script to iterate on executor prompt without running full experiments.
Tests whether LLM generates correct unquoted replicas: replicas: {{ value | int }}
instead of the buggy quoted version: replicas: "{{ value | int }}"
"""

import sys
import re
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from clients.llm_client import GPTLLMClient
from executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, EXECUTOR_USER_TEMPLATE


def create_test_prompt():
    """Create a simplified but realistic test prompt that should trigger replica scaling."""

    # Simplified strategy that includes scaling replicas
    strategy = """1. Temporarily increase the CPU limit per pod to provide more headroom for handling the current load.
2. Gradually increase the number of replicas for the dispatch service to distribute the load more evenly.
3. Once the service stabilizes, gradually revert any temporary increases."""

    # No previous examples to keep it simple and fast
    examples = "None available for this test."

    service = "dispatch"

    user_prompt = EXECUTOR_USER_TEMPLATE.format(
        strategy=strategy,
        service=service,
        examples=examples
    )

    return user_prompt


def check_for_replicas_bug(playbook_yaml: str) -> tuple[bool, list[str]]:
    """
    Check if the playbook has the replicas quoting bug.

    Returns:
        (has_bug, error_lines): True if bug found, with list of problematic lines
    """
    if not playbook_yaml or not playbook_yaml.strip():
        return True, ["ERROR: Empty playbook returned"]

    lines = playbook_yaml.split('\n')
    errors = []

    # Pattern 1: replicas: "{{ ... }}" (WRONG - quoted)
    quoted_pattern = re.compile(r'^\s*replicas:\s*"{{.*}}"', re.IGNORECASE)

    # Pattern 2: replicas: {{ ... }} (CORRECT - unquoted)
    unquoted_pattern = re.compile(r'^\s*replicas:\s*{{.*}}', re.IGNORECASE)

    for i, line in enumerate(lines, 1):
        if quoted_pattern.search(line):
            errors.append(f"Line {i}: {line.strip()} (BUG: replicas is QUOTED - will fail with type error)")
        elif unquoted_pattern.search(line):
            print(f"✓ Line {i}: {line.strip()} (CORRECT: replicas is unquoted)")

    return len(errors) > 0, errors


def main():
    """Run the quick test."""

    print("=" * 80)
    print("QUICK PROMPT TEST - Replicas Type Bug")
    print("=" * 80)
    print()

    # Initialize LLM client
    print("Initializing GPT-4o client...")
    model_config = {
        "model_id": "gpt-4o",
        "api_key": "***",
        "temperature": 0.0,
        "max_tokens": 4096
    }
    client = GPTLLMClient(model_config)

    # Create test prompt
    print("Creating test prompt...")
    user_prompt = create_test_prompt()

    print(f"System prompt length: {len(EXECUTOR_SYSTEM_PROMPT)} chars")
    print(f"User prompt length: {len(user_prompt)} chars")
    print()

    # Call LLM
    print("Calling GPT-4o (temperature=0.0)...")
    print("-" * 80)

    try:
        playbook_yaml = client.generate(
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            user_prompt=user_prompt
        )

        print("Response received:")
        print(f"Length: {len(playbook_yaml)} chars")
        print()

        # Check for bug
        has_bug, errors = check_for_replicas_bug(playbook_yaml)

        print("-" * 80)
        print()

        if has_bug:
            print("❌ TEST FAILED - Replicas bug detected!")
            print()
            for error in errors:
                print(f"  {error}")
            print()

            # Show the problematic section
            if "Empty playbook" not in str(errors):
                print("First 100 lines of generated playbook:")
                print("-" * 80)
                for i, line in enumerate(playbook_yaml.split('\n')[:100], 1):
                    print(f"{i:3d} | {line}")
                print("-" * 80)

            return 1
        else:
            # Check if replicas field exists at all
            if 'replicas:' in playbook_yaml.lower():
                print("✅ TEST PASSED - Replicas field is correctly unquoted!")
                print()
                print("First 50 lines of generated playbook:")
                print("-" * 80)
                for i, line in enumerate(playbook_yaml.split('\n')[:50], 1):
                    print(f"{i:3d} | {line}")
                print("-" * 80)
                return 0
            else:
                print("⚠️  WARNING - No replicas field found in playbook")
                print("The strategy mentions scaling but LLM didn't generate replica changes")
                print()
                print("First 50 lines of generated playbook:")
                print("-" * 80)
                for i, line in enumerate(playbook_yaml.split('\n')[:50], 1):
                    print(f"{i:3d} | {line}")
                print("-" * 80)
                return 2

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
