#!/usr/bin/env python3
"""
Test script to verify namespace configuration is working correctly.

Usage:
    # Test with default namespace
    python tests/test_namespace_config.py

    # Test with custom namespace
    ROBOT_SHOP_NAMESPACE="robot-shop1" python tests/test_namespace_config.py
"""

import os
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def test_namespace_configuration():
    """Test that namespace configuration is working across all modules."""

    print("=" * 70)
    print("Testing Namespace Configuration")
    print("=" * 70)

    # Test 1: Config module
    print("\n[Test 1] Testing src.config module...")
    from src.config import get_namespace, _get_kubectl_context_namespace

    configured_namespace = get_namespace()
    env_var = os.getenv('ROBOT_SHOP_NAMESPACE')
    kubectl_ns = _get_kubectl_context_namespace()

    # Determine expected namespace based on priority
    if env_var:
        expected_namespace = env_var
        detection_source = "environment variable"
    elif kubectl_ns:
        expected_namespace = kubectl_ns
        detection_source = "kubectl context"
    else:
        expected_namespace = "robot-shop"
        detection_source = "default fallback"

    print(f"  Environment variable: {env_var or '(not set)'}")
    print(f"  kubectl context namespace: {kubectl_ns or '(not detected)'}")
    print(f"  get_namespace() returns: {configured_namespace}")
    print(f"  Detection source: {detection_source}")

    assert configured_namespace == expected_namespace, \
        f"Config mismatch: got {configured_namespace}, expected {expected_namespace}"
    print("  ✓ Config module working correctly")

    # Test 2: Fault injection module
    print("\n[Test 2] Testing fault injection module...")
    from src.fault_injection.fault_inject import NAMESPACE as FAULT_NAMESPACE

    print(f"  Fault injection namespace: {FAULT_NAMESPACE}")
    assert FAULT_NAMESPACE == configured_namespace, \
        f"Fault injection mismatch: got {FAULT_NAMESPACE}, expected {configured_namespace}"
    print("  ✓ Fault injection module configured correctly")

    # Test 3: PromQL module
    print("\n[Test 3] Testing PromQL queries module...")
    from src.monitoring.promql import DEFAULT_NAMESPACE

    print(f"  PromQL default namespace: {DEFAULT_NAMESPACE}")
    assert DEFAULT_NAMESPACE == configured_namespace, \
        f"PromQL mismatch: got {DEFAULT_NAMESPACE}, expected {configured_namespace}"
    print("  ✓ PromQL module configured correctly")

    # Test 4: Infrastructure state module
    print("\n[Test 4] Testing infrastructure state module...")
    try:
        from src.monitoring.infrastructure_state import DEFAULT_NAMESPACE as INFRA_NAMESPACE

        print(f"  Infrastructure state namespace: {INFRA_NAMESPACE}")
        assert INFRA_NAMESPACE == expected_namespace, \
            f"Infrastructure state mismatch: got {INFRA_NAMESPACE}, expected {expected_namespace}"
        print("  ✓ Infrastructure state module configured correctly")
    except ImportError as e:
        print(f"  ⚠ Skipped (missing dependency: {e.name})")

    # Test 5: Experiment module
    print("\n[Test 5] Testing experiment module...")
    try:
        from src.experiment.run_experiment import NAMESPACE as EXP_NAMESPACE

        print(f"  Experiment namespace: {EXP_NAMESPACE}")
        assert EXP_NAMESPACE == expected_namespace, \
            f"Experiment mismatch: got {EXP_NAMESPACE}, expected {expected_namespace}"
        print("  ✓ Experiment module configured correctly")
    except ImportError as e:
        print(f"  ⚠ Skipped (missing dependency: {e.name})")

    # Test 6: Executor prompt module
    print("\n[Test 6] Testing executor prompt module...")
    try:
        from src.executor.executor_prompt import get_executor_prompts

        _, user_template = get_executor_prompts(expected_namespace)
        namespace_count = user_template.count(expected_namespace)
        print(f"  Namespace appears {namespace_count} times in user template")
        assert namespace_count >= 3, \
            f"Expected namespace to appear at least 3 times in template, found {namespace_count}"
        print("  ✓ Executor prompt module configured correctly")
    except ImportError as e:
        print(f"  ⚠ Skipped (missing dependency: {e.name})")

    # Summary
    print("\n" + "=" * 70)
    print("All Tests Passed! ✓")
    print("=" * 70)
    print(f"\nNamespace configuration is working correctly with: {expected_namespace}")
    print("\nTo use a different namespace, run:")
    print(f'  ROBOT_SHOP_NAMESPACE="robot-shop1" python {sys.argv[0]}')
    print()

    return True


def check_kubernetes_connectivity(namespace):
    """Check if we can connect to the specified Kubernetes namespace."""
    import subprocess

    print("\n" + "=" * 70)
    print(f"Checking Kubernetes Connectivity to {namespace}")
    print("=" * 70)

    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            pod_lines = [line for line in result.stdout.strip().split('\n') if line and not line.startswith('NAME')]
            print(f"\n✓ Successfully connected to namespace '{namespace}'")
            print(f"  Found {len(pod_lines)} pods")
            if pod_lines:
                print("\n  Sample pods:")
                for line in pod_lines[:5]:
                    print(f"    {line}")
            return True
        else:
            print(f"\n✗ Failed to connect to namespace '{namespace}'")
            print(f"  Error: {result.stderr}")
            return False

    except FileNotFoundError:
        print("\n✗ kubectl not found in PATH")
        print("  Cannot verify Kubernetes connectivity")
        return False
    except subprocess.TimeoutExpired:
        print(f"\n✗ Timeout connecting to namespace '{namespace}'")
        return False
    except Exception as e:
        print(f"\n✗ Error checking Kubernetes connectivity: {e}")
        return False


if __name__ == "__main__":
    try:
        # Run namespace configuration tests
        test_namespace_configuration()

        # Check Kubernetes connectivity
        from src.config import get_namespace
        namespace = get_namespace()
        check_kubernetes_connectivity(namespace)

        sys.exit(0)

    except AssertionError as e:
        print(f"\n✗ Test Failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
