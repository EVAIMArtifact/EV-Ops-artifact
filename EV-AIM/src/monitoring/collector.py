from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
import time

from src.monitoring.prometheus_client import PublicPrometheusClient
from .config import (
    CollectionWindow,
    FaultEvent,
    MetricTarget,
    ALL_METRIC_GROUPS,
    APPLICATION_METRIC_GROUPS,
    SYSTEM_METRIC_GROUPS,
    INFRASTRUCTURE_METRIC_GROUPS,
    spanmetric_prefix_for,
)
from .promql import METRIC_QUERIES, render_query
from .summarizer import (
    extract_instant_vector,
    extract_range_matrix,
    summarize_instant_vector,
    summarize_vector_series,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _query(
    prom: PublicPrometheusClient,
    query: str,
    range_mode: bool,
    start: Optional[float],
    end: Optional[float],
    step: str,
) -> list:
    if range_mode:
        return prom.query_range(query=query, start=start, end=end, step=step)
    return prom.query(query=query)


def _metric_is_range_candidate(group: str, metric_name: str) -> bool:
    """Use query_range only for continuous values.

    State/reason metrics are instant snapshots because range summaries for states
    like ImagePullBackOff or OOMKilled are harder for the LLM to interpret.
    """
    if metric_name.endswith("_reason"):
        return False
    if metric_name in {
        "pod_phase_count",
        "container_waiting_reason",
        "container_terminated_reason",
        "oom_kills",
        "replicas_desired",
        "replicas_available",
        "replicas_ready",
        "replicas_updated",
        "replicas_unavailable",
        "deployment_generation_mismatch",
        "cpu_limit_per_pod",
        "cpu_request_per_pod",
        "memory_limit_per_pod",
        "memory_request_per_pod",
        "node_memory_pressure",
        "node_disk_pressure",
        "node_pid_pressure",
        "hpa_current_replicas",
        "hpa_desired_replicas",
        "hpa_min_replicas",
        "hpa_max_replicas",
    }:
        return False
    return group in {
        "application_api",
        "container_resources",
        "namespace_context",
        "node_context",
    }


def collect_target_metrics(
    prom: PublicPrometheusClient,
    target: MetricTarget,
    window: CollectionWindow,
    metric_groups: Optional[List[str]] = None,
    end_time: Optional[float] = None,
    include_raw_queries: bool = False,
    max_workers: int = 12,
) -> Dict[str, Any]:
    """Collect LLM-ready telemetry for one service/deployment in one namespace."""

    metric_groups = metric_groups or ALL_METRIC_GROUPS
    end_time = end_time or time.time()
    start_time = end_time - window.lookback_seconds

    params = {
        "namespace": target.namespace,
        "pod_prefix": target.pod_prefix,
        "deployment": target.deployment_name,
        "span_service": target.otel_service,
        # Per-app spanmetric prefix so RED queries hit the right metric names
        # (robotshop_* / sockshop_* / onlineboutique_*) instead of a global default.
        "spanmetric_prefix": spanmetric_prefix_for(target.app),
        "window": window.prom_window,
        "rate_interval": window.rate_interval,
    }

    tasks = []
    for group in metric_groups:
        if group not in METRIC_QUERIES:
            continue
        for metric_name, template in METRIC_QUERIES[group].items():
            query = render_query(template, params)
            range_mode = _metric_is_range_candidate(group, metric_name)
            tasks.append((group, metric_name, query, range_mode))

    collected: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def run_one(task):
        group, metric_name, query, range_mode = task
        result = _query(
            prom=prom,
            query=query,
            range_mode=range_mode,
            start=start_time,
            end=end_time,
            step=window.prom_step,
        )

        if range_mode:
            matrix = extract_range_matrix(result)
            summary = summarize_vector_series(matrix)
        else:
            vector = extract_instant_vector(result)
            summary = summarize_instant_vector(vector)

        if include_raw_queries:
            summary["query"] = query
        return group, metric_name, summary

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(run_one, task): task for task in tasks}
        for future in as_completed(future_map):
            group, metric_name, _, _ = future_map[future]
            try:
                _, _, summary = future.result()
                collected.setdefault(group, {})[metric_name] = summary
            except Exception as exc:
                errors[f"{group}.{metric_name}"] = str(exc)

    return {
        "target": {
            "app": target.app,
            "namespace": target.namespace,
            "service": target.service,
            "deployment": target.deployment_name,
            "pod_prefix": target.pod_prefix,
            "span_service": target.otel_service,
        },
        "collection": {
            "collected_at": _now_iso(),
            "start_time": start_time,
            "end_time": end_time,
            "lookback_seconds": window.lookback_seconds,
            "step_seconds": window.step_seconds,
            "rate_interval": window.rate_interval,
            "note": "Prometheus query_range includes both start and end timestamps, so a 300s/60s window may return 6 samples.",
        },
        "application_metrics": {
            group: collected.get(group, {})
            for group in APPLICATION_METRIC_GROUPS
            if group in collected
        },
        "system_metrics": {
            group: collected.get(group, {})
            for group in SYSTEM_METRIC_GROUPS
            if group in collected
        },
        "infrastructure_metrics": {
            group: collected.get(group, {})
            for group in INFRASTRUCTURE_METRIC_GROUPS
            if group in collected
        },
        "metrics": collected,
        "errors": errors,
    }


def target_from_fault(
    fault: FaultEvent,
    span_service: Optional[str] = None,
) -> MetricTarget:
    return MetricTarget(
        app=fault.app,
        namespace=fault.namespace,
        service=fault.service,
        deployment=fault.deployment or fault.service,
        span_service=span_service or fault.service,
    )


def collect_fault_observation(
    prometheus_url: str,
    fault: FaultEvent | Dict[str, Any],
    window: CollectionWindow,
    span_service: Optional[str] = None,
    metric_groups: Optional[List[str]] = None,
    include_raw_queries: bool = False,
) -> Dict[str, Any]:
    """Collect metrics for the namespace/service where one fault was injected.

    prometheus_url is kept only for backward compatibility. The active client uses
    BASE_URL/BASIC_AUTH_USER/BASIC_AUTH_PASSWORD because your Prometheus API is public
    and authenticated through your colleague's wrapper.
    """

    if isinstance(fault, dict):
        fault_event = FaultEvent.from_fault_dict(fault)
    else:
        fault_event = fault

    prom = PublicPrometheusClient()
    target = target_from_fault(fault_event, span_service=span_service)

    observation = collect_target_metrics(
        prom=prom,
        target=target,
        window=window,
        metric_groups=metric_groups,
        end_time=fault_event.end_time or time.time(),
        include_raw_queries=include_raw_queries,
    )
    observation["fault"] = fault_event.__dict__
    observation["diagnostic_hints"] = build_diagnostic_hints(observation)
    return observation


def collect_experiment_observation(
    prometheus_url: str,
    faults: Iterable[FaultEvent | Dict[str, Any]],
    window: CollectionWindow,
    metric_groups: Optional[List[str]] = None,
    include_neighbor_services: Optional[List[MetricTarget]] = None,
) -> Dict[str, Any]:
    """Collect telemetry for multiple parallel/sequential injected faults."""

    prom = PublicPrometheusClient()
    observations: List[Dict[str, Any]] = []

    fault_events = [FaultEvent.from_fault_dict(f) if isinstance(f, dict) else f for f in faults]
    for fault in fault_events:
        target = target_from_fault(fault)
        obs = collect_target_metrics(
            prom=prom,
            target=target,
            window=window,
            metric_groups=metric_groups,
            end_time=fault.end_time or time.time(),
        )
        obs["fault"] = fault.__dict__
        obs["diagnostic_hints"] = build_diagnostic_hints(obs)
        observations.append(obs)

    neighbor_observations: List[Dict[str, Any]] = []
    for target in include_neighbor_services or []:
        neighbor_observations.append(
            collect_target_metrics(
                prom=prom,
                target=target,
                window=window,
                metric_groups=metric_groups,
            )
        )

    return {
        "collected_at": _now_iso(),
        "fault_count": len(fault_events),
        "observations": observations,
        "neighbor_observations": neighbor_observations,
    }


def _get_metric(
    obs: Dict[str, Any],
    group: str,
    metric: str,
    path: List[str],
    default=None,
):
    node = obs.get("metrics", {}).get(group, {}).get(metric, {})
    for part in path:
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return node if node is not None else default


def _has_reason(obs: Dict[str, Any], reason_substring: str) -> bool:
    for metric_name in ("container_waiting_reason", "container_terminated_reason"):
        values = obs.get("metrics", {}).get("pod_health", {}).get(metric_name, {}).get("values", {})
        for key, value in values.items():
            if reason_substring.lower() in str(key).lower() and value and value > 0:
                return True
    return False


def build_diagnostic_hints(obs: Dict[str, Any]) -> List[str]:
    """Non-decisive symptom hints for the prompt. These do not map fault to mitigation."""

    hints: List[str] = []

    err5 = _get_metric(obs, "application_api", "error_rate_5xx", ["aggregate_stats", "last"])
    req = _get_metric(obs, "application_api", "request_rate", ["aggregate_stats", "last"])
    lat = _get_metric(obs, "application_api", "latency_p95", ["aggregate_stats", "last"])
    cpu = _get_metric(obs, "container_resources", "cpu_usage_cores", ["aggregate_stats", "max"])
    cpu_limit_ratio = _get_metric(obs, "container_resources", "cpu_usage_to_limit_ratio", ["aggregate_stats", "max"])
    throttle = _get_metric(obs, "container_resources", "cpu_throttle_ratio", ["aggregate_stats", "max"])
    mem_limit_ratio = _get_metric(obs, "container_resources", "memory_usage_to_limit_ratio", ["aggregate_stats", "max"])
    restarts = _get_metric(obs, "pod_health", "pod_restarts", ["aggregate_stats", "sum"], 0)
    not_ready = _get_metric(obs, "pod_health", "pod_not_ready", ["aggregate_stats", "sum"], 0)
    oom = _get_metric(obs, "pod_health", "oom_kills", ["aggregate_stats", "sum"], 0)
    unavailable = _get_metric(obs, "deployment_health", "replicas_unavailable", ["aggregate_stats", "sum"], 0)
    desired = _get_metric(obs, "deployment_health", "replicas_desired", ["aggregate_stats", "last"])
    ready = _get_metric(obs, "deployment_health", "replicas_ready", ["aggregate_stats", "last"])
    generation_mismatch = _get_metric(obs, "deployment_health", "deployment_generation_mismatch", ["aggregate_stats", "sum"], 0)
    node_mem_pressure = _get_metric(obs, "node_context", "node_memory_pressure", ["aggregate_stats", "sum"], 0)
    node_disk_pressure = _get_metric(obs, "node_context", "node_disk_pressure", ["aggregate_stats", "sum"], 0)

    if err5 is not None and err5 > 0.05:
        hints.append("High 5xx error rate detected; check dependency failure, bad rollout, or overloaded backend.")
    if req is not None and req > 0:
        hints.append("Request rate is available; compare with latency/error trends to distinguish load spike from backend failure.")
    if lat is not None and lat > 1.0:
        hints.append("High p95 latency detected; check load spike, DB overload, CPU throttling, or saturated downstream service.")
    if cpu_limit_ratio is not None and cpu_limit_ratio > 0.8:
        hints.append("CPU usage is close to CPU limit; scaling out or increasing CPU limit may help if latency/errors are elevated.")
    if throttle is not None and throttle > 0.2:
        hints.append("CPU throttling is high; mitigation may require scaling out, increasing CPU limits, or reducing load.")
    if mem_limit_ratio is not None and mem_limit_ratio > 0.8:
        hints.append("Memory working set is close to limit; check memory pressure/OOM risk before choosing mitigation.")
    if restarts and restarts > 0:
        hints.append("Pod/container restarts detected; check crash, OOMKilled, failing probes, or bad image.")
    if oom and oom > 0:
        hints.append("OOM kill events detected; memory limit increase, leak mitigation, or scale-out may be relevant.")
    if not_ready and not_ready > 0:
        hints.append("One or more pods are not ready; inspect waiting/terminated reasons before scaling blindly.")
    if unavailable and unavailable > 0:
        hints.append("Deployment has unavailable replicas; likely rollout, crash, dependency, or image-pull issue.")
    if desired is not None and ready is not None and ready < desired:
        hints.append("Ready replicas are below desired replicas; check rollout status, image pull, scheduling, or crashing pods.")
    if generation_mismatch and abs(generation_mismatch) > 0:
        hints.append("Deployment generation mismatch detected; rollout may be incomplete or stuck.")
    if _has_reason(obs, "ImagePullBackOff") or _has_reason(obs, "ErrImagePull"):
        hints.append("Image pull failure reason detected; bad image or registry access is likely.")
    if _has_reason(obs, "CrashLoopBackOff"):
        hints.append("CrashLoopBackOff detected; inspect container logs/config and avoid simple scaling as the first mitigation.")
    if node_mem_pressure and node_mem_pressure > 0:
        hints.append("Node memory pressure detected; mitigation may require reducing memory load, moving pods, or scaling nodes.")
    if node_disk_pressure and node_disk_pressure > 0:
        hints.append("Node disk pressure detected; check eviction, image pulls, logs, or filesystem usage.")
    if cpu is not None:
        hints.append("CPU usage is available; compare it with CPU limits and throttling to distinguish load spike from CPU pressure.")

    return hints



def _split_metric_groups_for_multi_service(
    metric_groups: Optional[List[str]],
) -> tuple[List[str], List[str]]:
    """Split requested groups into service-scoped and infrastructure-scoped groups.

    In multi-service observation, namespace/node/HPA metrics should be collected once,
    not repeated under every service.
    """
    requested = metric_groups or ALL_METRIC_GROUPS
    service_groups = [
        group
        for group in requested
        if group in APPLICATION_METRIC_GROUPS or group in SYSTEM_METRIC_GROUPS
    ]
    infrastructure_groups = [
        group
        for group in requested
        if group in INFRASTRUCTURE_METRIC_GROUPS
    ]
    return service_groups, infrastructure_groups


def _application_metrics_available(observation: Optional[Dict[str, Any]]) -> bool:
    if not observation:
        return False
    app_metrics = observation.get("application_metrics", {}).get("application_api", {})
    for metric_summary in app_metrics.values():
        if metric_summary.get("aggregate_stats", {}).get("count", 0) > 0:
            return True
    return False


def collect_multi_service_observation(
    prometheus_url: str,
    fault: FaultEvent | Dict[str, Any],
    services: List[str],
    window: CollectionWindow,
    metric_groups: Optional[List[str]] = None,
    include_raw_queries: bool = False,
) -> Dict[str, Any]:
    """Collect target + neighbor service metrics and one shared infra snapshot.

    Output shape:
      - service_observations: service-scoped application/system metrics only
      - infrastructure_observation: namespace/node/HPA metrics collected once
      - primary_observation: target service observation for legacy feedback/MU
    """

    if isinstance(fault, dict):
        fault_event = FaultEvent.from_fault_dict(fault)
    else:
        fault_event = fault

    prom = PublicPrometheusClient()
    end_time = fault_event.end_time or time.time()
    service_groups, infrastructure_groups = _split_metric_groups_for_multi_service(metric_groups)

    # Keep input order but remove duplicates.
    services = list(dict.fromkeys(services or [fault_event.service]))
    if fault_event.service not in services:
        services.insert(0, fault_event.service)

    observations: Dict[str, Any] = {}
    for svc in services:
        target = MetricTarget(
            app=fault_event.app,
            namespace=fault_event.namespace,
            service=svc,
            deployment=svc,
            span_service=svc,
        )

        observations[svc] = collect_target_metrics(
            prom=prom,
            target=target,
            window=window,
            metric_groups=service_groups,
            end_time=end_time,
            include_raw_queries=include_raw_queries,
        )

    infrastructure_observation: Optional[Dict[str, Any]] = None
    if infrastructure_groups:
        infra_target = MetricTarget(
            app=fault_event.app,
            namespace=fault_event.namespace,
            service=fault_event.service,
            deployment=fault_event.deployment or fault_event.service,
            span_service=fault_event.service,
        )

        infra_full = collect_target_metrics(
            prom=prom,
            target=infra_target,
            window=window,
            metric_groups=infrastructure_groups,
            end_time=end_time,
            include_raw_queries=include_raw_queries,
        )

        infrastructure_observation = {
            "target": infra_full.get("target", {}),
            "collection": infra_full.get("collection", {}),
            "infrastructure_metrics": infra_full.get("infrastructure_metrics", {}),
            "metrics": {
                group: infra_full.get("metrics", {}).get(group, {})
                for group in infrastructure_groups
            },
            "errors": infra_full.get("errors", {}),
        }

    primary = observations.get(fault_event.service)

    return {
        "fault": fault_event.__dict__,
        "target_service": fault_event.service,
        "observed_services": services,
        "service_observations": observations,
        "infrastructure_observation": infrastructure_observation,
        "primary_observation": primary,
        "observability_status": {
            "metrics_enabled": True,
            "traces_enabled": _application_metrics_available(primary),
            "application_api_metrics_available": _application_metrics_available(primary),
            "note": "If false, application_api metrics are unavailable because traces/spanmetrics are not enabled or labels do not match.",
        },
    }
