"""
Rollout monitoring module for Kubernetes deployments.

This module provides functions to poll and wait for Kubernetes rollout completion,
enabling rollout-aware metric collection instead of fixed-time waits.

Typical usage:
    from src.executor.rollout_monitor import wait_for_rollout_completion

    result = wait_for_rollout_completion(
        service="cart",
        namespace="robot-shop",
        timeout=300
    )

    if result["rollout_completed"]:
        # Proceed with post-remediation metric collection
        print(f"Rollout completed in {result['rollout_duration_seconds']}s")
    else:
        # Handle failure
        print(f"Rollout failed: {result['error_message']}")
"""

import logging
import subprocess
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def wait_for_rollout_completion(
    service: str,
    namespace: str,
    timeout: int = 300
) -> Dict[str, any]:
    """
    Wait for a Kubernetes deployment rollout to complete.

    Polls kubectl rollout status until completion or timeout. This is critical
    for ensuring post-remediation metrics are collected only after the system
    has stabilized.

    Args:
        service: Name of the Kubernetes deployment
        namespace: Kubernetes namespace containing the deployment
        timeout: Maximum seconds to wait for rollout completion (default: 300)

    Returns:
        Dictionary containing:
            - rollout_completed (bool): True if rollout finished successfully
            - rollout_duration_seconds (float): Actual time taken for rollout
            - timeout_occurred (bool): True if timeout was reached
            - final_pod_count (int): Number of pods after rollout
            - all_pods_ready (bool): Whether all pods are ready
            - error_message (str): Error details if rollout failed

    Example:
        >>> result = wait_for_rollout_completion("cart", "robot-shop", timeout=180)
        >>> if result["rollout_completed"]:
        ...     print("Safe to collect metrics")
    """
    start_time = time.time()
    poll_interval = 5  # seconds

    logger.info(
        f"Starting rollout wait for deployment/{service} in namespace {namespace}",
        extra={
            "service": service,
            "namespace": namespace,
            "timeout_seconds": timeout
        }
    )

    # First check if deployment exists
    deployment_exists = _check_deployment_exists(service, namespace)
    if not deployment_exists:
        error_msg = f"Deployment {service} not found in namespace {namespace}"
        logger.error(error_msg, extra={"service": service, "namespace": namespace})
        return {
            "rollout_completed": False,
            "rollout_duration_seconds": 0.0,
            "timeout_occurred": False,
            "final_pod_count": 0,
            "all_pods_ready": False,
            "error_message": error_msg
        }

    # Poll rollout status
    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            logger.warning(
                f"Rollout timeout reached for {service}",
                extra={
                    "service": service,
                    "namespace": namespace,
                    "elapsed_seconds": elapsed,
                    "timeout_seconds": timeout
                }
            )

            # Get final status for debugging
            status = check_rollout_status(service, namespace)

            return {
                "rollout_completed": False,
                "rollout_duration_seconds": elapsed,
                "timeout_occurred": True,
                "final_pod_count": status.get("ready_replicas", 0),
                "all_pods_ready": False,
                "error_message": f"Timeout after {timeout}s. Status: {status}"
            }

        # Check rollout status
        try:
            cmd = [
                "kubectl", "rollout", "status",
                f"deployment/{service}",
                "-n", namespace,
                "--timeout=10s"
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15  # subprocess timeout (slightly longer than kubectl timeout)
            )

            if result.returncode == 0:
                # Rollout completed successfully
                duration = time.time() - start_time
                status = check_rollout_status(service, namespace)

                logger.info(
                    f"Rollout completed successfully for {service}",
                    extra={
                        "service": service,
                        "namespace": namespace,
                        "duration_seconds": duration,
                        "ready_replicas": status.get("ready_replicas", 0)
                    }
                )

                return {
                    "rollout_completed": True,
                    "rollout_duration_seconds": duration,
                    "timeout_occurred": False,
                    "final_pod_count": status.get("ready_replicas", 0),
                    "all_pods_ready": status.get("is_complete", False),
                    "error_message": ""
                }

            else:
                # Check if it's a permanent failure
                error_output = result.stderr.lower()

                # Permanent failure conditions
                if any(term in error_output for term in [
                    "not found",
                    "does not exist",
                    "no resources found"
                ]):
                    error_msg = f"Deployment no longer exists: {result.stderr}"
                    logger.error(
                        error_msg,
                        extra={"service": service, "namespace": namespace}
                    )
                    return {
                        "rollout_completed": False,
                        "rollout_duration_seconds": time.time() - start_time,
                        "timeout_occurred": False,
                        "final_pod_count": 0,
                        "all_pods_ready": False,
                        "error_message": error_msg
                    }

                # Still in progress, continue polling
                logger.debug(
                    f"Rollout in progress for {service} (elapsed: {elapsed:.1f}s)",
                    extra={
                        "service": service,
                        "namespace": namespace,
                        "elapsed_seconds": elapsed
                    }
                )

        except subprocess.TimeoutExpired:
            logger.warning(
                f"kubectl command timeout for {service}, retrying",
                extra={"service": service, "namespace": namespace}
            )
            # Continue polling

        except Exception as e:
            logger.error(
                f"Unexpected error checking rollout status: {e}",
                extra={
                    "service": service,
                    "namespace": namespace,
                    "error": str(e)
                }
            )
            # Continue polling unless we've exceeded overall timeout

        # Wait before next poll
        time.sleep(poll_interval)


def check_rollout_status(service: str, namespace: str) -> Dict[str, any]:
    """
    Perform a single non-blocking check of deployment rollout status.

    Queries the deployment resource to get current replica counts and readiness.
    Useful for monitoring progress without blocking.

    Args:
        service: Name of the Kubernetes deployment
        namespace: Kubernetes namespace containing the deployment

    Returns:
        Dictionary containing:
            - is_complete (bool): Whether rollout is complete
            - updated_replicas (int): Number of updated replicas
            - ready_replicas (int): Number of ready replicas
            - available_replicas (int): Number of available replicas
            - desired_replicas (int): Target replica count
            - error (str): Error message if check failed

    Example:
        >>> status = check_rollout_status("cart", "robot-shop")
        >>> print(f"Ready: {status['ready_replicas']}/{status['desired_replicas']}")
    """
    try:
        cmd = [
            "kubectl", "get", "deployment", service,
            "-n", namespace,
            "-o", "jsonpath={.spec.replicas},{.status.replicas},{.status.updatedReplicas},{.status.readyReplicas},{.status.availableReplicas}"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.error(
                f"Failed to check deployment status: {result.stderr}",
                extra={"service": service, "namespace": namespace}
            )
            return {
                "is_complete": False,
                "updated_replicas": 0,
                "ready_replicas": 0,
                "available_replicas": 0,
                "desired_replicas": 0,
                "error": result.stderr
            }

        # Parse output: desired,current,updated,ready,available
        parts = result.stdout.strip().split(",")

        # Handle missing values (None becomes 0)
        desired = int(parts[0]) if parts[0] else 0
        current = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        updated = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        ready = int(parts[3]) if len(parts) > 3 and parts[3] else 0
        available = int(parts[4]) if len(parts) > 4 and parts[4] else 0

        # Rollout is complete when all replicas are updated, ready, and available
        is_complete = (
            desired > 0 and
            updated == desired and
            ready == desired and
            available == desired
        )

        return {
            "is_complete": is_complete,
            "updated_replicas": updated,
            "ready_replicas": ready,
            "available_replicas": available,
            "desired_replicas": desired,
            "error": ""
        }

    except subprocess.TimeoutExpired:
        logger.error(
            f"Timeout checking deployment status for {service}",
            extra={"service": service, "namespace": namespace}
        )
        return {
            "is_complete": False,
            "updated_replicas": 0,
            "ready_replicas": 0,
            "available_replicas": 0,
            "desired_replicas": 0,
            "error": "kubectl command timeout"
        }

    except Exception as e:
        logger.error(
            f"Unexpected error checking deployment status: {e}",
            extra={
                "service": service,
                "namespace": namespace,
                "error": str(e)
            }
        )
        return {
            "is_complete": False,
            "updated_replicas": 0,
            "ready_replicas": 0,
            "available_replicas": 0,
            "desired_replicas": 0,
            "error": str(e)
        }


def wait_for_pods_ready(
    service: str,
    namespace: str,
    expected_count: int,
    timeout: int = 180
) -> Dict[str, any]:
    """
    Wait until the expected number of pods are ready.

    Particularly useful for scale-out operations where we need to ensure
    new pods have fully started before collecting metrics or proceeding
    with additional actions.

    Args:
        service: Name of the Kubernetes deployment
        namespace: Kubernetes namespace containing the deployment
        expected_count: Number of ready pods to wait for
        timeout: Maximum seconds to wait (default: 180)

    Returns:
        Dictionary containing:
            - success (bool): True if expected pods became ready
            - ready_count (int): Final number of ready pods
            - wait_duration_seconds (float): Time taken
            - timeout_occurred (bool): Whether timeout was reached
            - error_message (str): Error details if failed

    Example:
        >>> # After scaling out from 1 to 3 replicas
        >>> result = wait_for_pods_ready("cart", "robot-shop", expected_count=3)
        >>> if result["success"]:
        ...     print("All new pods are ready")
    """
    start_time = time.time()
    poll_interval = 5  # seconds

    logger.info(
        f"Waiting for {expected_count} pods to be ready for {service}",
        extra={
            "service": service,
            "namespace": namespace,
            "expected_count": expected_count,
            "timeout_seconds": timeout
        }
    )

    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            status = check_rollout_status(service, namespace)
            ready_count = status.get("ready_replicas", 0)

            logger.warning(
                f"Timeout waiting for pods to be ready for {service}",
                extra={
                    "service": service,
                    "namespace": namespace,
                    "expected_count": expected_count,
                    "ready_count": ready_count,
                    "elapsed_seconds": elapsed
                }
            )

            return {
                "success": False,
                "ready_count": ready_count,
                "wait_duration_seconds": elapsed,
                "timeout_occurred": True,
                "error_message": f"Timeout: only {ready_count}/{expected_count} pods ready"
            }

        # Check current ready count
        status = check_rollout_status(service, namespace)
        ready_count = status.get("ready_replicas", 0)

        if ready_count >= expected_count:
            duration = time.time() - start_time
            logger.info(
                f"Expected pod count reached for {service}",
                extra={
                    "service": service,
                    "namespace": namespace,
                    "expected_count": expected_count,
                    "ready_count": ready_count,
                    "duration_seconds": duration
                }
            )

            return {
                "success": True,
                "ready_count": ready_count,
                "wait_duration_seconds": duration,
                "timeout_occurred": False,
                "error_message": ""
            }

        logger.debug(
            f"Waiting for pods: {ready_count}/{expected_count} ready (elapsed: {elapsed:.1f}s)",
            extra={
                "service": service,
                "namespace": namespace,
                "ready_count": ready_count,
                "expected_count": expected_count,
                "elapsed_seconds": elapsed
            }
        )

        time.sleep(poll_interval)


def _check_deployment_exists(service: str, namespace: str) -> bool:
    """
    Internal helper to verify deployment exists.

    Args:
        service: Name of the Kubernetes deployment
        namespace: Kubernetes namespace

    Returns:
        True if deployment exists, False otherwise
    """
    try:
        cmd = [
            "kubectl", "get", "deployment", service,
            "-n", namespace,
            "--no-headers"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        return result.returncode == 0

    except Exception as e:
        logger.error(
            f"Error checking if deployment exists: {e}",
            extra={"service": service, "namespace": namespace, "error": str(e)}
        )
        return False


def get_pod_failure_reasons(service: str, namespace: str) -> Dict[str, any]:
    """
    Check for common pod failure conditions.

    Useful for diagnosing why a rollout is stuck or failing. Checks for:
    - ImagePullBackOff
    - CrashLoopBackOff
    - Pending (unschedulable)
    - OOMKilled

    Args:
        service: Name of the Kubernetes deployment
        namespace: Kubernetes namespace

    Returns:
        Dictionary containing:
            - has_failures (bool): Whether any pods have failures
            - failure_types (list): List of failure condition types found
            - pod_statuses (list): List of dicts with pod name and status

    Example:
        >>> failures = get_pod_failure_reasons("cart", "robot-shop")
        >>> if failures["has_failures"]:
        ...     print(f"Failures detected: {failures['failure_types']}")
    """
    try:
        # Get pods for this deployment
        cmd = [
            "kubectl", "get", "pods",
            "-n", namespace,
            "-l", f"app={service}",
            "-o", "json"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            return {
                "has_failures": False,
                "failure_types": [],
                "pod_statuses": [],
                "error": result.stderr
            }

        import json
        pods_data = json.loads(result.stdout)

        failure_types = set()
        pod_statuses = []

        for pod in pods_data.get("items", []):
            pod_name = pod["metadata"]["name"]
            status = pod.get("status", {})

            # Check container statuses
            container_statuses = status.get("containerStatuses", [])
            for container in container_statuses:
                state = container.get("state", {})

                # Check waiting state
                if "waiting" in state:
                    reason = state["waiting"].get("reason", "")
                    if reason:
                        failure_types.add(reason)
                        pod_statuses.append({
                            "pod_name": pod_name,
                            "status": "Waiting",
                            "reason": reason,
                            "message": state["waiting"].get("message", "")
                        })

                # Check terminated state
                elif "terminated" in state:
                    reason = state["terminated"].get("reason", "")
                    if reason in ["Error", "OOMKilled"]:
                        failure_types.add(reason)
                        pod_statuses.append({
                            "pod_name": pod_name,
                            "status": "Terminated",
                            "reason": reason,
                            "exit_code": state["terminated"].get("exitCode", "")
                        })

            # Check pod phase
            phase = status.get("phase", "")
            if phase == "Pending":
                conditions = status.get("conditions", [])
                for condition in conditions:
                    if condition.get("type") == "PodScheduled" and condition.get("status") == "False":
                        failure_types.add("Unschedulable")
                        pod_statuses.append({
                            "pod_name": pod_name,
                            "status": "Pending",
                            "reason": condition.get("reason", ""),
                            "message": condition.get("message", "")
                        })

        return {
            "has_failures": len(failure_types) > 0,
            "failure_types": list(failure_types),
            "pod_statuses": pod_statuses,
            "error": ""
        }

    except Exception as e:
        logger.error(
            f"Error checking pod failures: {e}",
            extra={"service": service, "namespace": namespace, "error": str(e)}
        )
        return {
            "has_failures": False,
            "failure_types": [],
            "pod_statuses": [],
            "error": str(e)
        }
