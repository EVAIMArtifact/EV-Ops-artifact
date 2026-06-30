"""Compatibility layer for older EV-AIM code expecting fetch_metrics().

New code should use collector.collect_fault_observation().
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from .collector import collect_fault_observation
from .config import CollectionWindow


def _legacy_value(observation: Dict[str, Any], group: str, metric: str, field: str = "mean") -> Any:
    node = observation.get("metrics", {}).get(group, {}).get(metric, {})
    stats = node.get("aggregate_stats", {})
    if field in stats:
        return stats[field]
    if "value" in node:
        return node["value"]
    return None


def _legacy_entry(metric: Dict[str, str], value: Any) -> list:
    return [{"metric": metric, "value": [0, "" if value is None else str(value)]}]


def fetch_metrics(
    service: str,
    namespace: str,
    app: str,
    prometheus_url: str,
    window: CollectionWindow,
    fault_type: str = "unknown",
    metrics_to_fetch: Optional[list] = None,
) -> Dict[str, Any]:
    obs = collect_fault_observation(
        prometheus_url=prometheus_url,
        fault={
            "type": fault_type,
            "app": app,
            "namespace": namespace,
            "service": service,
            "deployment": service,
        },
        window=window,
        metric_groups=metrics_to_fetch,
    )

    return {
        "service": service,
        "namespace": namespace,
        "duration": f"Last {window.prom_window}",
        "average_cpu_usage": _legacy_entry({"pod": "aggregated"}, _legacy_value(obs, "container_resources", "cpu_usage_cores")),
        "average_cpu_throttle": _legacy_entry({"pod": "aggregated"}, _legacy_value(obs, "container_resources", "cpu_throttle_ratio")),
        "average_memory_working_set": _legacy_entry({"pod": "aggregated"}, _legacy_value(obs, "container_resources", "memory_working_set_bytes")),
        "average_memory_rss": _legacy_entry({"pod": "aggregated"}, _legacy_value(obs, "container_resources", "memory_rss_bytes")),
        "latency_p95": _legacy_entry({"service": service}, _legacy_value(obs, "application_api", "latency_p95")),
        "error_rate_5xx": _legacy_entry({"service": service}, _legacy_value(obs, "application_api", "error_rate_5xx")),
        "request_rate": _legacy_entry({"service": service}, _legacy_value(obs, "application_api", "request_rate")),
        "replicas_ready": _legacy_entry({"deployment": service}, _legacy_value(obs, "deployment_health", "replicas_ready", "last")),
        "replicas_desired": _legacy_entry({"deployment": service}, _legacy_value(obs, "deployment_health", "replicas_desired", "last")),
        "_rich_observation": obs,
    }
