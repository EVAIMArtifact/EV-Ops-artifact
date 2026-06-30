import json
from typing import Dict, Any, Optional


def service_metric(metrics: dict, target_service: Optional[str], metric_name: str):
    if not metrics or not target_service:
        return None

    svc = metrics.get("service_observations", {}).get(target_service, {})
    if not svc:
        return None

    # Try all main groups where service-level metrics may exist
    for root in ("metrics", "system_metrics"):
        root_obj = svc.get(root, {}) or {}

        for group_name in (
            "deployment_health",
            "container_resources",
            "pod_health",
            "application_api",
        ):
            group = root_obj.get(group_name, {}) or {}
            metric = group.get(metric_name, {}) or {}
            value = metric.get("aggregate_stats", {}).get("mean")
            if value is not None:
                return value

    return None


def build_infrastructure_snapshot(
    metrics: Dict[str, Any],
    target_service: Optional[str] = None,
) -> Dict[str, Any]:
    infra = metrics.get("infrastructure_observation") or metrics

    namespace_ctx = infra.get("infrastructure_metrics", {}).get("namespace_context", {})
    node_ctx = infra.get("infrastructure_metrics", {}).get("node_context", {})
    hpa_ctx = infra.get("infrastructure_metrics", {}).get("hpa_context", {})

    def last_metric(group: dict, metric_name: str):
        return group.get(metric_name, {}).get("aggregate_stats", {}).get("last")

    snapshot = {
        # Namespace
        "namespace_running_pods": last_metric(namespace_ctx, "namespace_running_pods"),
        "namespace_pending_pods": last_metric(namespace_ctx, "namespace_pending_pods"),
        "namespace_failed_pods": last_metric(namespace_ctx, "namespace_failed_pods"),
        "namespace_restart_count": last_metric(namespace_ctx, "namespace_restarts"),
        "namespace_cpu_usage_cores": last_metric(namespace_ctx, "namespace_cpu_usage_cores"),
        "namespace_memory_working_set_bytes": last_metric(namespace_ctx, "namespace_memory_working_set_bytes"),

        # Node
        "node_cpu_busy_ratio": last_metric(node_ctx, "node_cpu_busy_ratio"),
        "node_memory_available_ratio": last_metric(node_ctx, "node_memory_available_ratio"),
        "node_disk_available_ratio": last_metric(node_ctx, "node_disk_available_ratio"),
        "node_memory_pressure": last_metric(node_ctx, "node_memory_pressure"),
        "node_disk_pressure": last_metric(node_ctx, "node_disk_pressure"),
        "node_pid_pressure": last_metric(node_ctx, "node_pid_pressure"),
        "kubelet_running_pods": last_metric(node_ctx, "kubelet_running_pods"),

        # HPA
        "hpa_current_replicas": last_metric(hpa_ctx, "hpa_current_replicas"),
        "hpa_desired_replicas": last_metric(hpa_ctx, "hpa_desired_replicas"),

        # Target deployment resources
        "target_cpu_limit_per_pod": service_metric(metrics, target_service, "cpu_limit_per_pod"),
        "target_cpu_request_per_pod": service_metric(metrics, target_service, "cpu_request_per_pod"),
        "target_memory_limit_per_pod_bytes": service_metric(metrics, target_service, "memory_limit_per_pod"),
        "target_memory_request_per_pod_bytes": service_metric(metrics, target_service, "memory_request_per_pod"),

        # Target replicas
        "target_replicas_desired": service_metric(metrics, target_service, "replicas_desired"),
        "target_replicas_ready": service_metric(metrics, target_service, "replicas_ready"),
        "target_replicas_available": service_metric(metrics, target_service, "replicas_available"),
        "target_replicas_unavailable": service_metric(metrics, target_service, "replicas_unavailable"),

        # Target disk I/O
        "target_fs_read_bytes_per_sec": service_metric(metrics, target_service, "fs_read_bytes_per_sec"),
        "target_fs_write_bytes_per_sec": service_metric(metrics, target_service, "fs_write_bytes_per_sec"),
        "target_fs_read_ops_per_sec": service_metric(metrics, target_service, "fs_read_ops_per_sec"),
        "target_fs_write_ops_per_sec": service_metric(metrics, target_service, "fs_write_ops_per_sec"),
        "target_fs_usage_bytes": service_metric(metrics, target_service, "fs_usage_bytes"),
        "target_fs_limit_bytes": service_metric(metrics, target_service, "fs_limit_bytes"),
        "target_fs_usage_to_limit_ratio": service_metric(metrics, target_service, "fs_usage_to_limit_ratio"),
    }

    return snapshot


def print_infrastructure_snapshot(snapshot: dict):
    print("\n========== Infrastructure Snapshot ==========")

    print(
        f"[Namespace] "
        f"running={snapshot.get('namespace_running_pods')} | "
        f"pending={snapshot.get('namespace_pending_pods')} | "
        f"failed={snapshot.get('namespace_failed_pods')} | "
        f"restarts={snapshot.get('namespace_restart_count')}"
    )

    print(
        f"[Namespace Resources] "
        f"cpu={snapshot.get('namespace_cpu_usage_cores')} cores | "
        f"memory={snapshot.get('namespace_memory_working_set_bytes')} bytes"
    )

    print(
        f"[Target Resources] "
        f"cpu_limit={snapshot.get('target_cpu_limit_per_pod')} cores | "
        f"cpu_request={snapshot.get('target_cpu_request_per_pod')} cores | "
        f"memory_limit={snapshot.get('target_memory_limit_per_pod_bytes')} bytes | "
        f"memory_request={snapshot.get('target_memory_request_per_pod_bytes')} bytes"
    )

    print(
        f"[Target Disk I/O] "
        f"read_bytes/s={snapshot.get('target_fs_read_bytes_per_sec')} | "
        f"write_bytes/s={snapshot.get('target_fs_write_bytes_per_sec')} | "
        f"read_ops/s={snapshot.get('target_fs_read_ops_per_sec')} | "
        f"write_ops/s={snapshot.get('target_fs_write_ops_per_sec')}"
    )

    print(
        f"[Target Filesystem] "
        f"usage={snapshot.get('target_fs_usage_bytes')} bytes | "
        f"limit={snapshot.get('target_fs_limit_bytes')} bytes | "
        f"usage_ratio={snapshot.get('target_fs_usage_to_limit_ratio')}"
    )

    print(
        f"[Node] "
        f"cpu_busy={snapshot.get('node_cpu_busy_ratio')} | "
        f"mem_available={snapshot.get('node_memory_available_ratio')} | "
        f"disk_available={snapshot.get('node_disk_available_ratio')} | "
        f"mem_pressure={snapshot.get('node_memory_pressure')} | "
        f"disk_pressure={snapshot.get('node_disk_pressure')} | "
        f"pid_pressure={snapshot.get('node_pid_pressure')}"
    )

    print(
        f"[Kubelet] "
        f"running_pods={snapshot.get('kubelet_running_pods')}"
    )

    print(
        f"[HPA] "
        f"current={snapshot.get('hpa_current_replicas')} | "
        f"desired={snapshot.get('hpa_desired_replicas')}"
    )

    print("=============================================\n")


def compare_infrastructure_states(before: dict, after: dict) -> Dict[str, Any]:
    def delta(key: str):
        before_value = before.get(key)
        after_value = after.get(key)
        if before_value is None or after_value is None:
            return None
        try:
            return after_value - before_value
        except TypeError:
            return None

    result = {
        "namespace_running_pods_before": before.get("namespace_running_pods"),
        "namespace_running_pods_after": after.get("namespace_running_pods"),
        "namespace_running_pods_delta": delta("namespace_running_pods"),

        "namespace_pending_pods_delta": delta("namespace_pending_pods"),
        "namespace_failed_pods_delta": delta("namespace_failed_pods"),
        "namespace_restart_count_delta": delta("namespace_restart_count"),

        "namespace_cpu_usage_cores_delta": delta("namespace_cpu_usage_cores"),
        "namespace_memory_working_set_bytes_delta": delta("namespace_memory_working_set_bytes"),

        "target_cpu_limit_per_pod_before": before.get("target_cpu_limit_per_pod"),
        "target_cpu_limit_per_pod_after": after.get("target_cpu_limit_per_pod"),
        "target_cpu_limit_per_pod_delta": delta("target_cpu_limit_per_pod"),

        "target_memory_limit_per_pod_before_bytes": before.get("target_memory_limit_per_pod_bytes"),
        "target_memory_limit_per_pod_after_bytes": after.get("target_memory_limit_per_pod_bytes"),
        "target_memory_limit_per_pod_delta_bytes": delta("target_memory_limit_per_pod_bytes"),

        "target_replicas_desired_before": before.get("target_replicas_desired"),
        "target_replicas_desired_after": after.get("target_replicas_desired"),
        "target_replicas_desired_delta": delta("target_replicas_desired"),

        "node_cpu_busy_ratio_delta": delta("node_cpu_busy_ratio"),
        "node_memory_available_ratio_delta": delta("node_memory_available_ratio"),
        "node_disk_available_ratio_delta": delta("node_disk_available_ratio"),

        "hpa_current_replicas_before": before.get("hpa_current_replicas"),
        "hpa_current_replicas_after": after.get("hpa_current_replicas"),
        "hpa_current_replicas_delta": delta("hpa_current_replicas"),

        "hpa_desired_replicas_before": before.get("hpa_desired_replicas"),
        "hpa_desired_replicas_after": after.get("hpa_desired_replicas"),
        "hpa_desired_replicas_delta": delta("hpa_desired_replicas"),

        # Disk I/O before/after/delta
        "target_fs_read_bytes_per_sec_before": before.get("target_fs_read_bytes_per_sec"),
        "target_fs_read_bytes_per_sec_after": after.get("target_fs_read_bytes_per_sec"),
        "target_fs_read_bytes_per_sec_delta": delta("target_fs_read_bytes_per_sec"),

        "target_fs_write_bytes_per_sec_before": before.get("target_fs_write_bytes_per_sec"),
        "target_fs_write_bytes_per_sec_after": after.get("target_fs_write_bytes_per_sec"),
        "target_fs_write_bytes_per_sec_delta": delta("target_fs_write_bytes_per_sec"),

        "target_fs_read_ops_per_sec_before": before.get("target_fs_read_ops_per_sec"),
        "target_fs_read_ops_per_sec_after": after.get("target_fs_read_ops_per_sec"),
        "target_fs_read_ops_per_sec_delta": delta("target_fs_read_ops_per_sec"),

        "target_fs_write_ops_per_sec_before": before.get("target_fs_write_ops_per_sec"),
        "target_fs_write_ops_per_sec_after": after.get("target_fs_write_ops_per_sec"),
        "target_fs_write_ops_per_sec_delta": delta("target_fs_write_ops_per_sec"),

        "target_fs_usage_bytes_before": before.get("target_fs_usage_bytes"),
        "target_fs_usage_bytes_after": after.get("target_fs_usage_bytes"),
        "target_fs_usage_bytes_delta": delta("target_fs_usage_bytes"),

        "target_fs_limit_bytes_before": before.get("target_fs_limit_bytes"),
        "target_fs_limit_bytes_after": after.get("target_fs_limit_bytes"),
        "target_fs_limit_bytes_delta": delta("target_fs_limit_bytes"),

        "target_fs_usage_to_limit_ratio_before": before.get("target_fs_usage_to_limit_ratio"),
        "target_fs_usage_to_limit_ratio_after": after.get("target_fs_usage_to_limit_ratio"),
        "target_fs_usage_to_limit_ratio_delta": delta("target_fs_usage_to_limit_ratio"),
    }

    target_replica_delta = result["target_replicas_desired_delta"]
    namespace_pod_delta = result["namespace_running_pods_delta"]

    result["scale_out_occurred"] = (
        target_replica_delta is not None and target_replica_delta > 0
    )

    result["replica_restore_occurred"] = (
        target_replica_delta is not None and target_replica_delta > 0
    )

    result["scale_up_occurred"] = (
        (
            result["target_cpu_limit_per_pod_delta"] is not None
            and result["target_cpu_limit_per_pod_delta"] > 0
        )
        or
        (
            result["target_memory_limit_per_pod_delta_bytes"] is not None
            and result["target_memory_limit_per_pod_delta_bytes"] > 0
        )
    )

    result["memory_limit_changed"] = (
        result["target_memory_limit_per_pod_delta_bytes"] is not None
        and result["target_memory_limit_per_pod_delta_bytes"] != 0
    )

    result["cpu_limit_changed"] = (
        result["target_cpu_limit_per_pod_delta"] is not None
        and result["target_cpu_limit_per_pod_delta"] != 0
    )

    result["disk_io_increased"] = any([
        result["target_fs_read_bytes_per_sec_delta"] is not None
        and result["target_fs_read_bytes_per_sec_delta"] > 0,

        result["target_fs_write_bytes_per_sec_delta"] is not None
        and result["target_fs_write_bytes_per_sec_delta"] > 0,

        result["target_fs_read_ops_per_sec_delta"] is not None
        and result["target_fs_read_ops_per_sec_delta"] > 0,

        result["target_fs_write_ops_per_sec_delta"] is not None
        and result["target_fs_write_ops_per_sec_delta"] > 0,
    ])

    result["disk_usage_increased"] = (
        result["target_fs_usage_bytes_delta"] is not None
        and result["target_fs_usage_bytes_delta"] > 0
    )

    result["disk_limit_changed"] = (
        result["target_fs_limit_bytes_delta"] is not None
        and result["target_fs_limit_bytes_delta"] != 0
    )

    result["disk_pressure_detected_after"] = (
        after.get("node_disk_pressure") == 1
    )

    result["node_pressure_detected_after"] = any([
        after.get("node_memory_pressure") == 1,
        after.get("node_disk_pressure") == 1,
        after.get("node_pid_pressure") == 1,
    ])

    result["namespace_pod_count_increased"] = (
        namespace_pod_delta is not None and namespace_pod_delta > 0
    )

    result["possible_rollout_surge_or_helper_pod"] = (
        result["namespace_pod_count_increased"]
        and not result["scale_out_occurred"]
    )

    return result