#!/usr/bin/env python3
"""
Quick test for Gemini 2.0 Flash - Check if it fixes the replicas bug.
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from clients.llm_client import GeminiLLMClient
from executor.executor_prompt import EXECUTOR_SYSTEM_PROMPT, EXECUTOR_USER_TEMPLATE


def create_test_prompt():
    """Create a simplified test prompt that should trigger replica scaling."""
    strategy = """1. Temporarily increase the CPU limit per pod to provide more headroom for handling the current load.
2. Gradually increase the number of replicas for the dispatch service to distribute the load more evenly.
3. Once the service stabilizes, gradually revert any temporary increases."""

    examples = "None available for this test."
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

    # Pattern 1: replicas: "{{ ... }}" (WRONG - quoted)
    quoted_pattern = re.compile(r'^\s*replicas:\s*"{{.*}}"', re.IGNORECASE)
    # Pattern 2: replicas: {{ ... }} (CORRECT - unquoted)
    unquoted_pattern = re.compile(r'^\s*replicas:\s*{{.*}}', re.IGNORECASE)

    for i, line in enumerate(lines, 1):
        if quoted_pattern.search(line):
            errors.append(f"Line {i}: {line.strip()} (BUG: replicas is QUOTED)")
        elif unquoted_pattern.search(line):
            print(f"✓ Line {i}: {line.strip()} (CORRECT: unquoted)")

    return len(errors) > 0, errors


def main():
    print("=" * 80)
    print("GEMINI 2.0 FLASH TEST - Replicas Type Bug")
    print("=" * 80)
    print()

    print("Initializing Gemini 2.0 Flash client...")
    model_config = {
        "model_id": "models/gemini-2.5-flash",
        "api_key": "YOUR_GEMINI_API_KEY",
        "temperature": 0.1,
        "max_tokens": 8192
    }
    client = GeminiLLMClient(model_config)

    print("Creating test prompt...")
    user_prompt = create_test_prompt()

    print(f"System prompt: {len(EXECUTOR_SYSTEM_PROMPT)} chars")
    print(f"User prompt: {len(user_prompt)} chars")
    print()

    print("Calling Gemini 2.0 Flash (temperature=0.1)...")
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
            print("❌ TEST FAILED - Gemini also produces the replicas bug!")
            print()
            for error in errors:
                print(f"  {error}")
            print()
            print("First 100 lines:")
            print("-" * 80)
            for i, line in enumerate(playbook_yaml.split('\n')[:100], 1):
                print(f"{i:3d} | {line}")
            print("-" * 80)
            return 1
        else:
            if 'replicas:' in playbook_yaml.lower():
                print("✅ TEST PASSED - Gemini correctly generates unquoted replicas!")
                print()
                print("First 50 lines:")
                print("-" * 80)
                for i, line in enumerate(playbook_yaml.split('\n')[:50], 1):
                    print(f"{i:3d} | {line}")
                print("-" * 80)
                return 0
            else:
                print("⚠️  WARNING - No replicas field found")
                print()
                print("First 50 lines:")
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
