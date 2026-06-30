from typing import Optional, Dict, Any, List


def _safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _stat(metric_obj: Dict[str, Any], key: str = "last"):
    if not isinstance(metric_obj, dict):
        return None
    return metric_obj.get("aggregate_stats", {}).get(key)


def _trend(metric_obj: Dict[str, Any]):
    if not isinstance(metric_obj, dict):
        return "unknown"
    return metric_obj.get("aggregate_trend", "unknown")


def _values(metric_obj: Dict[str, Any]):
    if not isinstance(metric_obj, dict):
        return {}
    return metric_obj.get("values", {}) or {}


def _round(value, ndigits=4):
    if value is None:
        return "unavailable"
    try:
        return round(float(value), ndigits)
    except Exception:
        return value


def _pct(value):
    if value is None:
        return "unavailable"
    try:
        return round(float(value) * 100, 2)
    except Exception:
        return value


def _mb(value):
    if value is None:
        return "unavailable"
    try:
        return round(float(value) / (1024 * 1024), 2)
    except Exception:
        return value


def _count_phase(pod_phase_obj: Dict[str, Any], phase: str):
    return _values(pod_phase_obj).get(phase, 0.0) or 0.0


def _container_reason_list(metric_obj: Dict[str, Any]):
    vals = _values(metric_obj)
    return list(vals.keys()) if vals else []


def _get_service_obs(metrics: Dict[str, Any], service: str):
    return metrics.get("service_observations", {}).get(service, {})


def _extract_service_compact(metrics: Dict[str, Any], service: str):
    obs = _get_service_obs(metrics, service)
    groups = obs.get("metrics", {})

    app = groups.get("application_api", {})
    cr = groups.get("container_resources", {})
    ph = groups.get("pod_health", {})
    dh = groups.get("deployment_health", {})

    pod_phase = ph.get("pod_phase_count", {})

    return {
        "service": service,

        "desired": _stat(dh.get("replicas_desired")),
        "ready": _stat(dh.get("replicas_ready")),
        "available": _stat(dh.get("replicas_available")),
        "unavailable": _stat(dh.get("replicas_unavailable")),
        "generation_mismatch": _stat(dh.get("deployment_generation_mismatch")),

        "running_pods": _count_phase(pod_phase, "Running"),
        "failed_pods": _count_phase(pod_phase, "Failed"),
        "pending_pods": _count_phase(pod_phase, "Pending"),
        "succeeded_pods": _count_phase(pod_phase, "Succeeded"),
        "unknown_pods": _count_phase(pod_phase, "Unknown"),

        "not_ready_sum": _stat(ph.get("pod_not_ready"), "sum"),
        "restart_sum": _stat(ph.get("pod_restarts"), "sum"),
        "oom_sum": _stat(ph.get("oom_kills"), "sum"),
        "terminated": _container_reason_list(ph.get("container_terminated_reason")),
        "waiting": _container_reason_list(ph.get("container_waiting_reason")),

        "cpu_p95": _stat(cr.get("cpu_usage_cores"), "p95"),
        "cpu_last": _stat(cr.get("cpu_usage_cores"), "last"),
        "cpu_limit_p95": _stat(cr.get("cpu_usage_to_limit_ratio"), "p95"),
        "cpu_limit_last": _stat(cr.get("cpu_usage_to_limit_ratio"), "last"),
        "cpu_throttle_p95": _stat(cr.get("cpu_throttle_ratio"), "p95"),
        "cpu_throttle_last": _stat(cr.get("cpu_throttle_ratio"), "last"),

        "mem_ws_p95": _stat(cr.get("memory_working_set_bytes"), "p95"),
        "mem_ws_last": _stat(cr.get("memory_working_set_bytes"), "last"),
        "mem_limit_p95": _stat(cr.get("memory_usage_to_limit_ratio"), "p95"),
        "mem_limit_last": _stat(cr.get("memory_usage_to_limit_ratio"), "last"),

        "rx_p95": _stat(cr.get("network_rx_bytes_per_sec"), "p95"),
        "tx_p95": _stat(cr.get("network_tx_bytes_per_sec"), "p95"),

        "fs_read_bytes_p95": _stat(cr.get("fs_read_bytes_per_sec"), "p95"),
        "fs_read_bytes_last": _stat(cr.get("fs_read_bytes_per_sec"), "last"),
        "fs_write_bytes_p95": _stat(cr.get("fs_write_bytes_per_sec"), "p95"),
        "fs_write_bytes_last": _stat(cr.get("fs_write_bytes_per_sec"), "last"),

        "fs_read_ops_p95": _stat(cr.get("fs_read_ops_per_sec"), "p95"),
        "fs_read_ops_last": _stat(cr.get("fs_read_ops_per_sec"), "last"),
        "fs_write_ops_p95": _stat(cr.get("fs_write_ops_per_sec"), "p95"),
        "fs_write_ops_last": _stat(cr.get("fs_write_ops_per_sec"), "last"),

        "fs_usage_bytes": _stat(cr.get("fs_usage_bytes"), "last"),
        "fs_limit_bytes": _stat(cr.get("fs_limit_bytes"), "last"),
        "fs_usage_to_limit_ratio": _stat(cr.get("fs_usage_to_limit_ratio"), "last"),

        "request_rate": _stat(app.get("request_rate"), "last"),
        "latency_p95": _stat(app.get("latency_p95"), "p95"),
        "error_5xx": _stat(app.get("error_rate_5xx"), "last"),

        "cpu_request": _stat(cr.get("cpu_request_per_pod")),
        "cpu_limit": _stat(cr.get("cpu_limit_per_pod")),

        "memory_request": _stat(cr.get("memory_request_per_pod_bytes")),
        "memory_limit": _stat(cr.get("memory_limit_per_pod_bytes")),
    }


def _impact_score(s: Dict[str, Any]) -> float:
    score = 0.0

    if s["failed_pods"]:
        score += 4
    if s["succeeded_pods"]:
        score += 2
    if s["pending_pods"]:
        score += 4
    if s["not_ready_sum"]:
        score += 3
    if s["unavailable"]:
        score += 5

    if s["cpu_limit_p95"] is not None:
        score += min(float(s["cpu_limit_p95"]) * 5, 5)

    if s["cpu_throttle_p95"] is not None:
        score += min(float(s["cpu_throttle_p95"]) * 10, 5)

    if s["mem_limit_p95"] is not None:
        score += min(float(s["mem_limit_p95"]) * 5, 5)

    return score


def _fmt(value, suffix=""):
    if value == "unavailable" or value is None:
        return "unavailable"
    return f"{value}{suffix}"


def _build_service_block(title: str, s: Dict[str, Any]) -> str:
    terminated = ", ".join(s["terminated"]) if s["terminated"] else "none"
    waiting = ", ".join(s["waiting"]) if s["waiting"] else "none"

    return f"""
{title}:
- Service: {s["service"]}
- Deployment replicas: desired={_round(s["desired"], 0)}, ready={_round(s["ready"], 0)}, available={_round(s["available"], 0)}, unavailable={_round(s["unavailable"], 0)}
- Resource configuration:
  CPU request={_fmt(_round(s["cpu_request"], 3), " cores")},
  CPU limit={_fmt(_round(s["cpu_limit"], 3), " cores")},
  Memory request={_fmt(_mb(s["memory_request"]), " MB")},
  Memory limit={_fmt(_mb(s["memory_limit"]), " MB")}
- Deployment generation mismatch: {_round(s["generation_mismatch"], 0)}
- Pod phases: running={_round(s["running_pods"], 0)}, failed={_round(s["failed_pods"], 0)}, pending={_round(s["pending_pods"], 0)}, succeeded={_round(s["succeeded_pods"], 0)}, unknown={_round(s["unknown_pods"], 0)}
- Not-ready pod count: {_round(s["not_ready_sum"], 0)}
- Restart count: {_round(s["restart_sum"], 0)}
- OOM kill count: {_round(s["oom_sum"], 0)}
- Terminated containers: {terminated}
- Waiting containers: {waiting}
- CPU usage: p95={_fmt(_round(s["cpu_p95"], 4), " cores")}, last={_fmt(_round(s["cpu_last"], 4), " cores")}
- CPU usage to limit: p95={_fmt(_pct(s["cpu_limit_p95"]), "%")}, last={_fmt(_pct(s["cpu_limit_last"]), "%")}
- CPU throttling: p95={_fmt(_pct(s["cpu_throttle_p95"]), "%")}, last={_fmt(_pct(s["cpu_throttle_last"]), "%")}
- Memory working set: p95={_fmt(_mb(s["mem_ws_p95"]), " MB")}, last={_fmt(_mb(s["mem_ws_last"]), " MB")}
- Memory usage to limit: p95={_fmt(_pct(s["mem_limit_p95"]), "%")}, last={_fmt(_pct(s["mem_limit_last"]), "%")}
- Network: rx_p95={_fmt(_round(s["rx_p95"], 2), " B/s")}, tx_p95={_fmt(_round(s["tx_p95"], 2), " B/s")}
- Disk I/O: read_p95={_fmt(_round(s["fs_read_bytes_p95"], 2), " B/s")}, read_last={_fmt(_round(s["fs_read_bytes_last"], 2), " B/s")}, write_p95={_fmt(_round(s["fs_write_bytes_p95"], 2), " B/s")}, write_last={_fmt(_round(s["fs_write_bytes_last"], 2), " B/s")}
- Disk ops: read_ops_p95={_round(s["fs_read_ops_p95"], 4)}, read_ops_last={_round(s["fs_read_ops_last"], 4)}, write_ops_p95={_round(s["fs_write_ops_p95"], 4)}, write_ops_last={_round(s["fs_write_ops_last"], 4)}
- Filesystem usage: usage={_fmt(_mb(s["fs_usage_bytes"]), " MB")}, limit={_fmt(_mb(s["fs_limit_bytes"]), " MB")}, usage_to_limit={_fmt(_pct(s["fs_usage_to_limit_ratio"]), "%")}
- Application telemetry: request_rate={_round(s["request_rate"], 4)}, latency_p95={_round(s["latency_p95"], 4)}, error_5xx={_round(s["error_5xx"], 4)}
""".strip()
def _infra_value(infra_state: Optional[Dict[str, Any]], *keys):
    infra_state = infra_state or {}
    for key in keys:
        if key in infra_state and infra_state[key] is not None:
            return infra_state[key]
    return None

def build_planner_metrics(
    metrics: Dict[str, Any],
    infra_state: Optional[Dict[str, Any]] = None,
    max_related_services: int = 3,
) -> str:
    """
    Build compact natural-language planner context for LLM1.

    This avoids JSON-style prompts and avoids recommending actions.
    The LLM receives evidence only and must infer severity/action.
    """

    fault = metrics.get("fault", {})
    app = fault.get("app")
    namespace = fault.get("namespace")
    fault_type = fault.get("fault_type")
    target_service = metrics.get("target_service") or fault.get("service")
    deployment = fault.get("deployment") or target_service

    target = _extract_service_compact(metrics, target_service)

    infra_state = infra_state or {}

    target["cpu_request"] = _infra_value(
        infra_state,
        "target_cpu_request_per_pod",
        "cpu_request_per_pod",
    )

    target["cpu_limit"] = _infra_value(
        infra_state,
        "target_cpu_limit_per_pod",
        "cpu_limit_per_pod",
    )

    target["memory_request"] = _infra_value(
        infra_state,
        "target_memory_request_per_pod_bytes",
        "memory_request_per_pod_bytes",
    )

    target["memory_limit"] = _infra_value(
        infra_state,
        "target_memory_limit_per_pod_bytes",
        "memory_limit_per_pod_bytes",
    )

    related = []
    for svc in metrics.get("observed_services", []):
        if svc == target_service:
            continue
        s = _extract_service_compact(metrics, svc)
        related.append((_impact_score(s), s))

    related = [
        s for score, s in sorted(related, key=lambda x: x[0], reverse=True)
        if score > 0
    ][:max_related_services]

    cluster = metrics.get("infrastructure_observation", {})
    infra = cluster.get("infrastructure_metrics", {}) if isinstance(cluster, dict) else {}

    ns_running = _stat(_safe_get(infra, ["namespace_context", "namespace_running_pods"], {}))
    ns_pending = _stat(_safe_get(infra, ["namespace_context", "namespace_pending_pods"], {}))
    ns_failed = _stat(_safe_get(infra, ["namespace_context", "namespace_failed_pods"], {}))
    node_cpu = _stat(_safe_get(infra, ["node_context", "node_cpu_busy_ratio"], {}))
    node_mem = _stat(_safe_get(infra, ["node_context", "node_memory_available_ratio"], {}))
    hpa_current = _stat(_safe_get(infra, ["hpa_context", "hpa_current_replicas"], {}))
    hpa_desired = _stat(_safe_get(infra, ["hpa_context", "hpa_desired_replicas"], {}))
    node_disk_avail = _stat(_safe_get(infra, ["node_context", "node_disk_available_ratio"], {}))
    node_disk_pressure = _stat(_safe_get(infra, ["node_context", "node_disk_pressure"], {}))
    

    related_text = "\n\n".join(
        _build_service_block(f"Related service {idx+1}", s)
        for idx, s in enumerate(related)
    )

    if not related_text:
        related_text = "No related services show stronger symptoms than the target service."

    return f"""
Application: {app}
Namespace: {namespace}
Fault type: {fault_type}
Target service: {target_service}
Target deployment: {deployment}

{_build_service_block("Target service state", target)}

Cluster context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
- Node CPU busy ratio: {_fmt(_pct(node_cpu), "%")}
- Node memory available ratio: {_fmt(_pct(node_mem), "%")}
- HPA current replicas: {_round(hpa_current, 0)}
- HPA desired replicas: {_round(hpa_desired, 0)}
- Node disk available ratio: {_fmt(_pct(node_disk_avail), "%")}
- Node disk pressure: {_round(node_disk_pressure, 0)}
""".strip()


def _bytes_to_mb(value):
    if value is None:
        return None
    try:
        return float(value) / (1024 * 1024)
    except Exception:
        return None

def build_planner_metrics_groq(
    metrics: Dict[str, Any],
    infra_state: Optional[Dict[str, Any]] = None,
    max_related_services: int = 1,
) -> str:
    """
    Build compact fault-specific planner context for LLM1.
    Keeps only metrics needed for the current fault type.
    """

    fault = metrics.get("fault", {})
    app = fault.get("app")
    namespace = fault.get("namespace")
    fault_type = fault.get("fault_type") or fault.get("type")
    target_service = metrics.get("target_service") or fault.get("service")
    deployment = fault.get("deployment") or target_service

    infra_state = infra_state or {}

    target = _extract_service_compact(metrics, target_service)

    target["cpu_request"] = _infra_value(
        infra_state,
        "target_cpu_request_per_pod",
        "cpu_request_per_pod",
    )
    target["cpu_limit"] = _infra_value(
        infra_state,
        "target_cpu_limit_per_pod",
        "cpu_limit_per_pod",
    )
    target["memory_request"] = _infra_value(
        infra_state,
        "target_memory_request_per_pod_bytes",
        "memory_request_per_pod_bytes",
    )
    target["memory_limit"] = _infra_value(
        infra_state,
        "target_memory_limit_per_pod_bytes",
        "memory_limit_per_pod_bytes",
    )

    cluster = metrics.get("infrastructure_observation", {})
    infra = cluster.get("infrastructure_metrics", {}) if isinstance(cluster, dict) else {}

    ns_running = _stat(_safe_get(infra, ["namespace_context", "namespace_running_pods"], {}))
    ns_pending = _stat(_safe_get(infra, ["namespace_context", "namespace_pending_pods"], {}))
    ns_failed = _stat(_safe_get(infra, ["namespace_context", "namespace_failed_pods"], {}))
    node_cpu = _stat(_safe_get(infra, ["node_context", "node_cpu_busy_ratio"], {}))
    node_mem = _stat(_safe_get(infra, ["node_context", "node_memory_available_ratio"], {}))
    node_disk_avail = _stat(_safe_get(infra, ["node_context", "node_disk_available_ratio"], {}))
    node_disk_pressure = _stat(_safe_get(infra, ["node_context", "node_disk_pressure"], {}))

    fault_type_l = str(fault_type).lower()

    header = f"""
Application: {app}
Namespace: {namespace}
Fault type: {fault_type}
Target service: {target_service}
Target deployment: {deployment}

Target resource configuration:
- CPU request: {_fmt(target.get("cpu_request"), " cores")}
- CPU limit: {_fmt(target.get("cpu_limit"), " cores")}
- Memory request: {_fmt(_bytes_to_mb(target.get("memory_request")), " MB")}
- Memory limit: {_fmt(_bytes_to_mb(target.get("memory_limit")), " MB")}

Target pod/deployment health:
- Desired replicas: {_round(target.get("desired_replicas"), 0)}
- Ready replicas: {_round(target.get("ready_replicas"), 0)}
- Available replicas: {_round(target.get("available_replicas"), 0)}
- Unavailable replicas: {_round(target.get("unavailable_replicas"), 0)}
- Not-ready pod count: {_round(target.get("not_ready_pods"), 0)}
- Restart count: {_round(target.get("restart_count"), 0)}
- OOM kill count: {_round(target.get("oom_kills"), 0)}
""".strip()

    if fault_type_l in {"cpu_hog", "cpu_pressure", "cpu_throttle"}:
        fault_block = f"""
Fault-specific evidence:
- CPU usage p95: {_fmt(target.get("cpu_usage_p95"), " cores")}
- CPU usage last: {_fmt(target.get("cpu_usage_last"), " cores")}
- CPU usage to limit p95: {_fmt(_pct(target.get("cpu_usage_to_limit_p95")), "%")}
- CPU usage to limit last: {_fmt(_pct(target.get("cpu_usage_to_limit_last")), "%")}
- CPU throttling p95: {_fmt(_pct(target.get("cpu_throttle_p95")), "%")}
- CPU throttling last: {_fmt(_pct(target.get("cpu_throttle_last")), "%")}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
""".strip()

        cluster_block = f"""
Cluster capacity context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
- Node CPU busy ratio: {_fmt(_pct(node_cpu), "%")}
- Node memory available ratio: {_fmt(_pct(node_mem), "%")}
""".strip()

    elif fault_type_l in {"mem_stress", "memory_pressure", "resource_pressure"}:
        fault_block = f"""
Fault-specific evidence:
- Memory working set p95: {_fmt(_bytes_to_mb(target.get("memory_working_set_p95")), " MB")}
- Memory working set last: {_fmt(_bytes_to_mb(target.get("memory_working_set_last")), " MB")}
- Memory usage to limit p95: {_fmt(_pct(target.get("memory_usage_to_limit_p95")), "%")}
- Memory usage to limit last: {_fmt(_pct(target.get("memory_usage_to_limit_last")), "%")}
- OOM kill count: {_round(target.get("oom_kills"), 0)}
- Restart count: {_round(target.get("restart_count"), 0)}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
""".strip()

        cluster_block = f"""
Cluster capacity context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
- Node memory available ratio: {_fmt(_pct(node_mem), "%")}
- Node CPU busy ratio: {_fmt(_pct(node_cpu), "%")}
""".strip()

    elif fault_type_l in {"disk_stress", "disk_pressure"}:
        fault_block = f"""
Fault-specific evidence:
- Filesystem usage: {_fmt(_bytes_to_mb(target.get("fs_usage_bytes")), " MB")}
- Filesystem limit: {_fmt(_bytes_to_mb(target.get("fs_limit_bytes")), " MB")}
- Filesystem usage to limit: {_fmt(_pct(target.get("fs_usage_to_limit")), "%")}
- Disk read p95: {_fmt(target.get("fs_read_bps_p95"), " B/s")}
- Disk write p95: {_fmt(target.get("fs_write_bps_p95"), " B/s")}
- Disk read ops p95: {_fmt(target.get("fs_read_ops_p95"), " ops/s")}
- Disk write ops p95: {_fmt(target.get("fs_write_ops_p95"), " ops/s")}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
""".strip()

        cluster_block = f"""
Cluster capacity context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
- Node disk available ratio: {_fmt(_pct(node_disk_avail), "%")}
- Node disk pressure: {_round(node_disk_pressure, 0)}
""".strip()

    elif fault_type_l in {"pod_crash", "pod_kill"}:
        fault_block = f"""
Fault-specific evidence:
- Desired replicas: {_round(target.get("desired_replicas"), 0)}
- Ready replicas: {_round(target.get("ready_replicas"), 0)}
- Available replicas: {_round(target.get("available_replicas"), 0)}
- Unavailable replicas: {_round(target.get("unavailable_replicas"), 0)}
- Running pods: {_round(target.get("running_pods"), 0)}
- Failed pods: {_round(target.get("failed_pods"), 0)}
- Pending pods: {_round(target.get("pending_pods"), 0)}
- Restart count: {_round(target.get("restart_count"), 0)}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
""".strip()

        cluster_block = f"""
Cluster context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
""".strip()

    elif fault_type_l in {"dependency_failure", "bad_image", "stuck_deployment"}:
        fault_block = f"""
Fault-specific evidence:
- Desired replicas: {_round(target.get("desired_replicas"), 0)}
- Ready replicas: {_round(target.get("ready_replicas"), 0)}
- Available replicas: {_round(target.get("available_replicas"), 0)}
- Unavailable replicas: {_round(target.get("unavailable_replicas"), 0)}
- Deployment generation mismatch: {_round(target.get("generation_mismatch"), 0)}
- Waiting containers: {target.get("waiting_reasons") or "none"}
- Terminated containers: {target.get("terminated_reasons") or "none"}
- Restart count: {_round(target.get("restart_count"), 0)}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
""".strip()

        cluster_block = f"""
Cluster context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
""".strip()

    elif fault_type_l in {"load_spike", "net_delay", "net_loss"}:
        fault_block = f"""
Fault-specific evidence:
- Request rate: {_fmt(target.get("request_rate"), " req/s")}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
- CPU usage to limit p95: {_fmt(_pct(target.get("cpu_usage_to_limit_p95")), "%")}
- Memory usage to limit p95: {_fmt(_pct(target.get("memory_usage_to_limit_p95")), "%")}
""".strip()

        cluster_block = f"""
Cluster capacity context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
- Node CPU busy ratio: {_fmt(_pct(node_cpu), "%")}
- Node memory available ratio: {_fmt(_pct(node_mem), "%")}
""".strip()

    else:
        fault_block = f"""
Fault-specific evidence:
- Request rate: {_fmt(target.get("request_rate"), " req/s")}
- Latency p95: {_fmt(target.get("latency_p95"), " ms")}
- Error 5xx rate: {_fmt(_pct(target.get("error_5xx")), "%")}
- CPU usage to limit p95: {_fmt(_pct(target.get("cpu_usage_to_limit_p95")), "%")}
- Memory usage to limit p95: {_fmt(_pct(target.get("memory_usage_to_limit_p95")), "%")}
- Restart count: {_round(target.get("restart_count"), 0)}
- OOM kill count: {_round(target.get("oom_kills"), 0)}
""".strip()

        cluster_block = f"""
Cluster context:
- Namespace running pods: {_round(ns_running, 0)}
- Namespace pending pods: {_round(ns_pending, 0)}
- Namespace failed pods: {_round(ns_failed, 0)}
""".strip()

    return f"""
{header}

{fault_block}

{cluster_block}
""".strip()