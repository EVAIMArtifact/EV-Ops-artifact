#!/usr/bin/env python3
"""
Quick test for Claude LLM client integration.

This script verifies that the Claude client can:
1. Connect to the Anthropic API
2. Generate a simple Ansible playbook
3. Follow the kubectl scale constraint from the optimized prompt

Usage:
    python test_claude_quick.py
"""

from src.clients.llm_client import ClaudeLLMClient

# Minimal system prompt with kubectl constraint
SYSTEM_PROMPT = """
**RULE #1: USE KUBECTL FOR REPLICAS (NOT kubernetes.core.k8s)**

You are an Ansible playbook generator. When scaling replicas, you MUST use:

```yaml
- name: Scale deployment
  command: kubectl scale deployment my-app --replicas=3 -n my-namespace
```

NEVER use kubernetes.core.k8s module with replicas field.

**FINAL CHECKPOINT:** Before outputting, verify you used kubectl scale for replicas.
"""

USER_PROMPT = """
Generate an Ansible playbook to scale the 'dispatch' deployment in 'robot-shop' namespace from 1 to 2 replicas.

Requirements:
- Use kubectl scale command (NOT kubernetes.core.k8s)
- Set replicas to 2
- Target: deployment/dispatch in namespace robot-shop

Output only the playbook YAML, no markdown fences.
"""


def test_claude_client():
    """Test Claude client with kubectl constraint."""

    # Note: Replace with your actual Anthropic API key
    config = {
        "model_id": "claude-sonnet-4-5-20250929",
        "api_key": "YOUR_API_KEY_HERE",  # Update this
        "temperature": 0.0,
        "max_tokens": 2048
    }

    print("🧪 Testing Claude LLM Client")
    print("=" * 60)
    print(f"Model: {config['model_id']}")
    print(f"Temperature: {config['temperature']}")
    print()

    try:
        client = ClaudeLLMClient(config)
        print("✅ Client initialized successfully")
        print()

        print("📤 Sending test prompt...")
        response = client.generate(SYSTEM_PROMPT, USER_PROMPT)

        print("✅ Response received")
        print()
        print("📋 Generated Playbook:")
        print("-" * 60)
        print(response)
        print("-" * 60)
        print()

        # Check if response follows kubectl constraint
        if "kubectl scale" in response:
            print("✅ SUCCESS: Used kubectl scale command (correct)")
        else:
            print("❌ FAIL: Did not use kubectl scale command")

        if "kubernetes.core.k8s" in response and "replicas:" in response:
            print("❌ FAIL: Used kubernetes.core.k8s with replicas (incorrect)")
        else:
            print("✅ SUCCESS: Avoided kubernetes.core.k8s with replicas")

        print()
        print("🎉 Claude client test completed successfully!")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False


if __name__ == "__main__":
    import sys
    success = test_claude_client()
    sys.exit(0 if success else 1)
