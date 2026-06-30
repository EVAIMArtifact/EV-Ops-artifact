"""
Central configuration for AIM-EVM project.

This module provides centralized configuration management for environment-specific
settings like Kubernetes namespace, allowing easy switching between deployments
(e.g., robot-shop1, robot-shop2, etc.).

Namespace Detection (Priority Order):
    1. ROBOT_SHOP_NAMESPACE environment variable (highest priority)
    2. Kubernetes current context namespace (from kubectl config)
    3. Default: "robot-shop" (fallback)

Environment Variables:
    ROBOT_SHOP_NAMESPACE: Kubernetes namespace for Robot Shop deployment
                          If not set, will attempt to detect from kubectl context

Example:
    # Method 1: Set via environment variable (explicit)
    export ROBOT_SHOP_NAMESPACE="robot-shop1"

    # Method 2: Use kubectl context (automatic detection)
    kubectl config set-context --current --namespace=robot-shop1

    # Use in code
    from src.config import get_namespace
    namespace = get_namespace()
"""

import os
import subprocess
from typing import Optional


def _get_kubectl_context_namespace() -> Optional[str]:
    """
    Get the namespace from the current kubectl context.

    Returns:
        Namespace from kubectl context, or None if cannot be determined

    Example:
        >>> _get_kubectl_context_namespace()
        'robot-shop1'
    """
    try:
        # Get current context
        result = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )

        if result.returncode != 0:
            return None

        context_name = result.stdout.strip()
        if not context_name:
            return None

        # Get namespace for current context
        result = subprocess.run(
            ["kubectl", "config", "view", "--minify", "--output", "jsonpath={..namespace}"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False
        )

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        return None

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # kubectl not available or command failed
        return None


def get_namespace(default: Optional[str] = "robot-shop") -> str:
    """
    Get the configured Kubernetes namespace for Robot Shop.

    Priority order:
    1. ROBOT_SHOP_NAMESPACE environment variable (highest priority)
    2. Namespace from kubectl current context (auto-detection)
    3. Provided default value (fallback)

    Args:
        default: Default namespace to use if no other source available (default: "robot-shop")

    Returns:
        Configured Kubernetes namespace

    Example:
        >>> # With environment variable set
        >>> os.environ['ROBOT_SHOP_NAMESPACE'] = 'robot-shop1'
        >>> get_namespace()
        'robot-shop1'

        >>> # Without env var, using kubectl context
        >>> del os.environ['ROBOT_SHOP_NAMESPACE']
        >>> # If kubectl context has namespace=robot-shop1
        >>> get_namespace()
        'robot-shop1'

        >>> # Fallback to default
        >>> get_namespace()
        'robot-shop'
    """
    # Priority 1: Environment variable (explicit configuration)
    env_namespace = os.getenv("ROBOT_SHOP_NAMESPACE")
    if env_namespace:
        return env_namespace

    # Priority 2: kubectl context (automatic detection)
    kubectl_namespace = _get_kubectl_context_namespace()
    if kubectl_namespace:
        return kubectl_namespace

    # Priority 3: Default fallback
    return default


# Convenience constant for backward compatibility
NAMESPACE = get_namespace()
