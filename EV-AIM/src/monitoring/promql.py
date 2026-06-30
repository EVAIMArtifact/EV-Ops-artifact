"""Namespace-aware PromQL templates for EV-AIM.

This version fixes:
- no hardcoded namespace
- deployment metrics use deployment label, not pod label
- explicit infrastructure groups
- public API-compatible plain PromQL strings
- extra signals for pod crash, bad image, dependency failure, DB overload, CPU/memory pressure

Spanmetric names/labels vary by OTel setup. Configure these env vars if needed:
  SPANMETRIC_PREFIX=robotshop_traces_spanmetrics
  SPAN_SERVICE_LABEL=service_name
  SPAN_STATUS_LABEL=http_status_code
  SPAN_KIND_LABEL=span_kind        (values: SPAN_KIND_SERVER / SPAN_KIND_CLIENT / ...)

IMPORTANT — span_kind filtering:
The spanmetrics counter/histogram cover EVERY span a service emits, including
outbound client calls (http GET, dns.lookup, tcp.connect, tls.connect) and
internal spans (redis-info). For service-level RED (request rate / error rate /
latency of *inbound* requests) you MUST restrict to span_kind=SPAN_KIND_SERVER,
otherwise the numbers are dominated by client/internal spans. For inter-service
("between pods") latency, use span_kind=SPAN_KIND_CLIENT grouped by the peer.
"""

from __future__ import annotations

import os

SPANMETRIC_PREFIX = os.getenv("SPANMETRIC_PREFIX", "robotshop_traces_spanmetrics")
SPAN_SERVICE_LABEL = os.getenv("SPAN_SERVICE_LABEL", "service_name")
SPAN_STATUS_LABEL = os.getenv("SPAN_STATUS_LABEL", "http_status_code")
SPAN_KIND_LABEL = os.getenv("SPAN_KIND_LABEL", "span_kind")
SPAN_SERVER_KIND = os.getenv("SPAN_SERVER_KIND", "SPAN_KIND_SERVER")
SPAN_CLIENT_KIND = os.getenv("SPAN_CLIENT_KIND", "SPAN_KIND_CLIENT")
# Destination peer for client spans. HTTP client spans expose net_peer_name
# (an IP under the Node SDK's semconv); datastore spans expose server_address.
SPAN_PEER_LABEL = os.getenv("SPAN_PEER_LABEL", "net_peer_name")

SPAN_CALLS = f"{SPANMETRIC_PREFIX}_calls_total"
SPAN_LAT_BUCKET = f"{SPANMETRIC_PREFIX}_latency_bucket"
SPAN_LAT_SUM = f"{SPANMETRIC_PREFIX}_latency_sum"
SPAN_LAT_COUNT = f"{SPANMETRIC_PREFIX}_latency_count"

# ---------------------------------------------------------------------------
# Application/API symptoms
# ---------------------------------------------------------------------------
# All service-level queries restrict to inbound (SERVER) spans. The selector
# is factored out so every query stays consistent.
_SRV = '%(span_service_label)s="%(span_service)s", %(span_kind_label)s="%(span_server_kind)s"'

LATENCY_P95 = """
histogram_quantile(
  0.95,
  sum by (le) (
    rate(%(span_bucket)s{%(srv)s}[%(rate_interval)s])
  )
)
"""

LATENCY_P90 = """
histogram_quantile(
  0.90,
  sum by (le) (
    rate(%(span_bucket)s{%(srv)s}[%(rate_interval)s])
  )
)
"""

LATENCY_AVG = """
sum(rate(%(span_sum)s{%(srv)s}[%(rate_interval)s]))
/
sum(rate(%(span_count)s{%(srv)s}[%(rate_interval)s]))
"""

REQUEST_RATE = """
sum(rate(%(span_calls)s{%(srv)s}[%(rate_interval)s]))
"""

# `or vector(0)` makes the ratio 0 (not empty) when a service has emitted no
# matching error spans yet; clamp_min guards divide-by-zero when idle.
ERROR_RATE_5XX = """
(sum(rate(%(span_calls)s{%(srv)s, %(span_status_label)s=~"5.."}[%(rate_interval)s])) or vector(0))
/
clamp_min(sum(rate(%(span_calls)s{%(srv)s}[%(rate_interval)s])), 1e-9)
"""

ERROR_RATE_4XX = """
(sum(rate(%(span_calls)s{%(srv)s, %(span_status_label)s=~"4.."}[%(rate_interval)s])) or vector(0))
/
clamp_min(sum(rate(%(span_calls)s{%(srv)s}[%(rate_interval)s])), 1e-9)
"""

# ---------------------------------------------------------------------------
# Inter-service ("between pods") latency, from the caller's CLIENT spans,
# grouped by destination peer. Answers "how long do <service>'s outbound calls
# to each peer take". Peer label resolution depends on the SDK semconv
# (HTTP client spans -> net_peer_name as IP; datastore spans -> server_address).
# ---------------------------------------------------------------------------
_CLIENT = '%(span_service_label)s="%(span_service)s", %(span_kind_label)s="%(span_client_kind)s"'

EDGE_LATENCY_P95 = """
histogram_quantile(
  0.95,
  sum by (%(span_peer_label)s, le) (
    rate(%(span_bucket)s{%(client)s}[%(rate_interval)s])
  )
)
"""

EDGE_CALL_RATE = """
sum by (%(span_peer_label)s) (
  rate(%(span_calls)s{%(client)s}[%(rate_interval)s])
)
"""

# ---------------------------------------------------------------------------
# Container/resource symptoms, per pod
# ---------------------------------------------------------------------------
CPU_USAGE_PER_POD = """
sum by (pod) (
  rate(container_cpu_usage_seconds_total{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  }[%(rate_interval)s])
)
"""

CPU_THROTTLE_RATIO_PER_POD = """
sum by (pod) (
  rate(container_cpu_cfs_throttled_periods_total{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  }[%(rate_interval)s])
)
/
sum by (pod) (
  rate(container_cpu_cfs_periods_total{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  }[%(rate_interval)s])
)
"""

MEM_WORKING_SET_PER_POD = """
sum by (pod) (container_memory_working_set_bytes{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD",
  image!=""
})
"""

MEM_RSS_PER_POD = """
sum by (pod) (container_memory_rss{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD",
  image!=""
})
"""

MEM_CACHE_PER_POD = """
sum by (pod) (container_memory_cache{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD",
  image!=""
})
"""

MEM_USAGE_TO_LIMIT_RATIO = """
(
  sum by (pod) (container_memory_working_set_bytes{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD",
    image!=""
  })
)
/
(
  sum by (pod) (kube_pod_container_resource_limits{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    resource="memory"
  })
)
"""

CPU_USAGE_TO_LIMIT_RATIO = """
(
  sum by (pod) (rate(container_cpu_usage_seconds_total{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  }[%(rate_interval)s]))
)
/
(
  sum by (pod) (kube_pod_container_resource_limits{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    resource="cpu"
  })
)
"""

NETWORK_RX_BYTES_PER_POD = """
sum by (pod) (rate(container_network_receive_bytes_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
}[%(rate_interval)s]))
"""

NETWORK_TX_BYTES_PER_POD = """
sum by (pod) (rate(container_network_transmit_bytes_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
}[%(rate_interval)s]))
"""

FS_READ_BYTES_PER_POD = """
sum by (pod) (rate(container_fs_reads_bytes_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
}[%(rate_interval)s]))
"""

FS_WRITE_BYTES_PER_POD = """
sum by (pod) (rate(container_fs_writes_bytes_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
}[%(rate_interval)s]))
"""

# ---------------------------------------------------------------------------
# Pod/container health symptoms
# ---------------------------------------------------------------------------
POD_RESTARTS = """
sum by (pod) (increase(kube_pod_container_status_restarts_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
}[%(window)s]))
"""

POD_NOT_READY = """
sum by (pod) (1 - kube_pod_status_ready{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  condition="true"
})
"""

POD_PHASE_COUNT = """
sum by (phase) (kube_pod_status_phase{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
})
"""

CONTAINER_WAITING_REASON = """
sum by (pod, container, reason) (kube_pod_container_status_waiting_reason{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
})
"""

CONTAINER_TERMINATED_REASON = """
sum by (pod, container, reason) (increase(kube_pod_container_status_terminated_reason{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
}[%(window)s]))
"""

OOM_KILLS = """
sum by (pod, container) (increase(container_oom_events_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*"
}[%(window)s]))
"""

# ---------------------------------------------------------------------------
# Deployment/state symptoms. These must use deployment="...", not pod regex.
# ---------------------------------------------------------------------------
DEPLOYMENT_SPEC_REPLICAS = """
max by (deployment) (
  kube_deployment_spec_replicas{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

DEPLOYMENT_AVAILABLE_REPLICAS = """
max by (deployment) (
  kube_deployment_status_replicas_available{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

DEPLOYMENT_READY_REPLICAS = """
max by (deployment) (
  kube_deployment_status_replicas_ready{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

DEPLOYMENT_UPDATED_REPLICAS = """
max by (deployment) (
  kube_deployment_status_replicas_updated{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

DEPLOYMENT_UNAVAILABLE_REPLICAS = """
max by (deployment) (
  kube_deployment_status_replicas_unavailable{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

DEPLOYMENT_GENERATION_MISMATCH = """
max by (deployment) (
  kube_deployment_metadata_generation{namespace="%(namespace)s", deployment="%(deployment)s"}
)
-
max by (deployment) (
  kube_deployment_status_observed_generation{namespace="%(namespace)s", deployment="%(deployment)s"}
)
"""

CPU_LIMIT_PER_POD = """
sum by (pod) (kube_pod_container_resource_limits{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  resource="cpu"
})
"""

CPU_REQUEST_PER_POD = """
sum by (pod) (kube_pod_container_resource_requests{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  resource="cpu"
})
"""

MEM_LIMIT_PER_POD = """
sum by (pod) (kube_pod_container_resource_limits{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  resource="memory"
})
"""

MEM_REQUEST_PER_POD = """
sum by (pod) (kube_pod_container_resource_requests{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  resource="memory"
})
"""

# ---------------------------------------------------------------------------
# Namespace/node/HPA infrastructure context
# ---------------------------------------------------------------------------
NAMESPACE_POD_COUNT = """
count(kube_pod_info{namespace="%(namespace)s"})
"""

NAMESPACE_RUNNING_PODS = """
sum(kube_pod_status_phase{namespace="%(namespace)s", phase="Running"})
"""

NAMESPACE_PENDING_PODS = """
sum(kube_pod_status_phase{namespace="%(namespace)s", phase="Pending"})
"""

NAMESPACE_FAILED_PODS = """
sum(kube_pod_status_phase{namespace="%(namespace)s", phase="Failed"})
"""

NAMESPACE_RESTARTS = """
sum(increase(kube_pod_container_status_restarts_total{namespace="%(namespace)s"}[%(window)s]))
"""

NAMESPACE_CPU_USAGE = """
sum(rate(container_cpu_usage_seconds_total{
  namespace="%(namespace)s",
  container!="",
  container!="POD"
}[%(rate_interval)s]))
"""

NAMESPACE_MEMORY_WORKING_SET = """
sum(container_memory_working_set_bytes{
  namespace="%(namespace)s",
  container!="",
  container!="POD",
  image!=""
})
"""
FS_READ_OPS_PER_POD = """
sum by (pod) (rate(container_fs_reads_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
}[%(rate_interval)s]))
"""

FS_WRITE_OPS_PER_POD = """
sum by (pod) (rate(container_fs_writes_total{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
}[%(rate_interval)s]))
"""

FS_USAGE_BYTES_PER_POD = """
sum by (pod) (container_fs_usage_bytes{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
})
"""

FS_LIMIT_BYTES_PER_POD = """
sum by (pod) (container_fs_limit_bytes{
  namespace="%(namespace)s",
  pod=~"%(pod_prefix)s.*",
  container!="",
  container!="POD"
})
"""

FS_USAGE_TO_LIMIT_RATIO = """
(
  sum by (pod) (container_fs_usage_bytes{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  })
)
/
clamp_min(
  sum by (pod) (container_fs_limit_bytes{
    namespace="%(namespace)s",
    pod=~"%(pod_prefix)s.*",
    container!="",
    container!="POD"
  }),
  1
)
"""

NODE_CPU_BUSY = """
avg(1 - rate(node_cpu_seconds_total{mode="idle"}[%(rate_interval)s]))
"""

NODE_MEMORY_AVAILABLE_RATIO = """
avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
"""

NODE_DISK_AVAILABLE_RATIO = """
avg(node_filesystem_avail_bytes{fstype!~"tmpfs|overlay", mountpoint!~"/run.*|/var/lib/kubelet/pods.*"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay", mountpoint!~"/run.*|/var/lib/kubelet/pods.*"})
"""

NODE_MEMORY_PRESSURE = """
sum(kube_node_status_condition{condition="MemoryPressure", status="true"})
"""

NODE_DISK_PRESSURE = """
sum(kube_node_status_condition{condition="DiskPressure", status="true"})
"""

NODE_PID_PRESSURE = """
sum(kube_node_status_condition{condition="PIDPressure", status="true"})
"""

KUBELET_RUNNING_PODS = """
sum(kubelet_running_pods)
"""

HPA_CURRENT_REPLICAS = """
kube_horizontalpodautoscaler_status_current_replicas{namespace="%(namespace)s", horizontalpodautoscaler="%(deployment)s"}
"""

HPA_DESIRED_REPLICAS = """
kube_horizontalpodautoscaler_status_desired_replicas{namespace="%(namespace)s", horizontalpodautoscaler="%(deployment)s"}
"""

HPA_MAX_REPLICAS = """
kube_horizontalpodautoscaler_spec_max_replicas{namespace="%(namespace)s", horizontalpodautoscaler="%(deployment)s"}
"""

HPA_MIN_REPLICAS = """
kube_horizontalpodautoscaler_spec_min_replicas{namespace="%(namespace)s", horizontalpodautoscaler="%(deployment)s"}
"""

METRIC_QUERIES = {
    "application_api": {
        "request_rate": REQUEST_RATE,
        "latency_avg": LATENCY_AVG,
        "latency_p90": LATENCY_P90,
        "latency_p95": LATENCY_P95,
        "error_rate_4xx": ERROR_RATE_4XX,
        "error_rate_5xx": ERROR_RATE_5XX,
        # inter-service / pod-to-pod latency (caller's CLIENT spans, by peer)
        "edge_latency_p95": EDGE_LATENCY_P95,
        "edge_call_rate": EDGE_CALL_RATE,
    },
    "container_resources": {
        "cpu_usage_cores": CPU_USAGE_PER_POD,
        "cpu_usage_to_limit_ratio": CPU_USAGE_TO_LIMIT_RATIO,
        "cpu_throttle_ratio": CPU_THROTTLE_RATIO_PER_POD,
        "memory_working_set_bytes": MEM_WORKING_SET_PER_POD,
        "memory_rss_bytes": MEM_RSS_PER_POD,
        "memory_cache_bytes": MEM_CACHE_PER_POD,
        "memory_usage_to_limit_ratio": MEM_USAGE_TO_LIMIT_RATIO,
        "network_rx_bytes_per_sec": NETWORK_RX_BYTES_PER_POD,
        "network_tx_bytes_per_sec": NETWORK_TX_BYTES_PER_POD,

        # disk I/O
        "fs_read_bytes_per_sec": FS_READ_BYTES_PER_POD,
        "fs_write_bytes_per_sec": FS_WRITE_BYTES_PER_POD,
        "fs_read_ops_per_sec": FS_READ_OPS_PER_POD,
        "fs_write_ops_per_sec": FS_WRITE_OPS_PER_POD,
        "fs_usage_bytes": FS_USAGE_BYTES_PER_POD,
        "fs_limit_bytes": FS_LIMIT_BYTES_PER_POD,
        "fs_usage_to_limit_ratio": FS_USAGE_TO_LIMIT_RATIO,
    },
    "pod_health": {
        "pod_restarts": POD_RESTARTS,
        "pod_not_ready": POD_NOT_READY,
        "pod_phase_count": POD_PHASE_COUNT,
        "container_waiting_reason": CONTAINER_WAITING_REASON,
        "container_terminated_reason": CONTAINER_TERMINATED_REASON,
        "oom_kills": OOM_KILLS,
    },
    "deployment_health": {
        "replicas_desired": DEPLOYMENT_SPEC_REPLICAS,
        "replicas_available": DEPLOYMENT_AVAILABLE_REPLICAS,
        "replicas_ready": DEPLOYMENT_READY_REPLICAS,
        "replicas_updated": DEPLOYMENT_UPDATED_REPLICAS,
        "replicas_unavailable": DEPLOYMENT_UNAVAILABLE_REPLICAS,
        "deployment_generation_mismatch": DEPLOYMENT_GENERATION_MISMATCH,
        "cpu_limit_per_pod": CPU_LIMIT_PER_POD,
        "cpu_request_per_pod": CPU_REQUEST_PER_POD,
        "memory_limit_per_pod": MEM_LIMIT_PER_POD,
        "memory_request_per_pod": MEM_REQUEST_PER_POD,
    },
    "namespace_context": {
        "namespace_pod_count": NAMESPACE_POD_COUNT,
        "namespace_running_pods": NAMESPACE_RUNNING_PODS,
        "namespace_pending_pods": NAMESPACE_PENDING_PODS,
        "namespace_failed_pods": NAMESPACE_FAILED_PODS,
        "namespace_restarts": NAMESPACE_RESTARTS,
        "namespace_cpu_usage_cores": NAMESPACE_CPU_USAGE,
        "namespace_memory_working_set_bytes": NAMESPACE_MEMORY_WORKING_SET,
    },
    "node_context": {
        "node_cpu_busy_ratio": NODE_CPU_BUSY,
        "node_memory_available_ratio": NODE_MEMORY_AVAILABLE_RATIO,
        "node_disk_available_ratio": NODE_DISK_AVAILABLE_RATIO,
        "node_memory_pressure": NODE_MEMORY_PRESSURE,
        "node_disk_pressure": NODE_DISK_PRESSURE,
        "node_pid_pressure": NODE_PID_PRESSURE,
        "kubelet_running_pods": KUBELET_RUNNING_PODS,
    },
    "hpa_context": {
        "hpa_current_replicas": HPA_CURRENT_REPLICAS,
        "hpa_desired_replicas": HPA_DESIRED_REPLICAS,
        "hpa_min_replicas": HPA_MIN_REPLICAS,
        "hpa_max_replicas": HPA_MAX_REPLICAS,
    },
}


def render_query(template: str, params: dict) -> str:
    # Resolve the spanmetric metric names from a per-call prefix when given
    # (params["spanmetric_prefix"]), so one process can query robotshop_* /
    # sockshop_* / onlineboutique_* per app. Falls back to the module-level
    # names baked from SPANMETRIC_PREFIX. Explicit span_* params still win.
    prefix = params.get("spanmetric_prefix")
    if prefix:
        span_calls = f"{prefix}_calls_total"
        span_bucket = f"{prefix}_latency_bucket"
        span_sum = f"{prefix}_latency_sum"
        span_count = f"{prefix}_latency_count"
    else:
        span_calls, span_bucket = SPAN_CALLS, SPAN_LAT_BUCKET
        span_sum, span_count = SPAN_LAT_SUM, SPAN_LAT_COUNT

    full_params = {
        "span_calls": span_calls,
        "span_bucket": span_bucket,
        "span_sum": span_sum,
        "span_count": span_count,
        "span_service_label": SPAN_SERVICE_LABEL,
        "span_status_label": SPAN_STATUS_LABEL,
        "span_kind_label": SPAN_KIND_LABEL,
        "span_server_kind": SPAN_SERVER_KIND,
        "span_client_kind": SPAN_CLIENT_KIND,
        "span_peer_label": SPAN_PEER_LABEL,
        **params,
    }
    # Pre-expand the shared span_kind selectors so the nested placeholders
    # inside them resolve before the final substitution.
    full_params["srv"] = _SRV % full_params
    full_params["client"] = _CLIENT % full_params
    return template % full_params
