"""
compute_feedback.py

Outcome-based feedback for EV-AIM, static LLM, and rule-based remediation.

Design:
- SHS is absolute post-remediation system health.
- delta_SHS measures absolute movement in health.
- RQ and MU measure relative improvement, so partial recovery is credited
  even when the system remains outside the healthy SLO band.
- Reward is derived from RQ/MU and delta_SHS minus operational penalties.
- PS and ES are diagnostic metrics only; they are not weighted into reward.
- infra_state is namespace-level, so it is used only for namespace/pod context
  and resource-cost estimation, not for deployment-specific planning.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Iterable, Optional, Tuple



APP_SLO_THRESHOLDS = {
    "robot-shop": {
        "latency_good_ms": 100.0,
        "latency_bad_ms": 800.0,
        "cpu_good": 0.70,
        "cpu_bad": 0.90,
        "memory_good": 0.70,
        "memory_bad": 0.95,
        "error_good_rate": 0.01,
        "error_bad_rate": 0.05,
        "disk_io_good_bps": 5 * 1024 * 1024,
"disk_io_bad_bps": 50 * 1024 * 1024,
"disk_usage_good": 0.70,
"disk_usage_bad": 0.90,
    },
    "sock-shop": {
        "latency_good_ms": 300.0,
        "latency_bad_ms": 1500.0,
        "cpu_good": 0.70,
        "cpu_bad": 0.95,
        "memory_good": 0.70,
        "memory_bad": 0.95,
        "error_good_rate": 0.01,
        "error_bad_rate": 0.05,
        "disk_io_good_bps": 5 * 1024 * 1024,
"disk_io_bad_bps": 50 * 1024 * 1024,
"disk_usage_good": 0.70,
"disk_usage_bad": 0.90,
    },
    "online-boutique": {
        "latency_good_ms": 200.0,
        "latency_bad_ms": 1200.0,
        "cpu_good": 0.70,
        "cpu_bad": 0.95,
        "memory_good": 0.70,
        "memory_bad": 0.95,
        "error_good_rate": 0.01,
        "error_bad_rate": 0.05,
        "disk_io_good_bps": 5 * 1024 * 1024,
"disk_io_bad_bps": 50 * 1024 * 1024,
"disk_usage_good": 0.70,
"disk_usage_bad": 0.90,
    },
}


def get_app_slo_thresholds(app: Optional[str], override: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    slo = dict(APP_SLO_THRESHOLDS.get(app or "", APP_SLO_THRESHOLDS["robot-shop"]))
    if override:
        slo.update(override)
    return slo

def clamp(x: Optional[float], lo: float = 0.0, hi: float = 1.0, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        val = float(x)
        if math.isnan(val) or math.isinf(val):
            return default
        return max(lo, min(hi, val))
    except Exception:
        return default


def safe_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, str):
            x = x.strip().replace("%", "")
            if not x:
                return None
        val = float(x)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except Exception:
        return None


def _walk(obj: Any) -> Iterable[Tuple[str, Any]]:
    def rec(prefix: str, value: Any):
        if isinstance(value, dict):
            for k, v in value.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                yield from rec(path, v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                path = f"{prefix}[{i}]"
                yield from rec(path, v)
        else:
            yield prefix, value
    yield from rec("", obj)


def find_metric(metrics: Dict[str, Any], names: Iterable[str], stats: Iterable[str] = ("last", "mean", "avg", "p95")) -> Optional[float]:
    if not metrics:
        return None

    wanted = [n.lower() for n in names]
    stat_names = [s.lower() for s in stats]
    flattened = list(_walk(metrics))

    for metric_name in wanted:
        for stat in stat_names:
            for path, value in flattened:
                p = path.lower()
                if metric_name in p and (p.endswith(f".{stat}") or f"aggregate_stats.{stat}" in p):
                    val = safe_float(value)
                    if val is not None:
                        return val

    for metric_name in wanted:
        for path, value in flattened:
            if metric_name in path.lower():
                val = safe_float(value)
                if val is not None:
                    return val
    return None


def ratio_from_percent_or_fraction(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    return x / 100.0 if x > 1.5 else x

ACTION_ALIASES = {
    "restart": [
        "restart",
        "rollout restart",
        "delete pod",
        "recreate pod"
    ],

    "scale_out": [
        "scale out",
        "increase replicas",
        "replica",
        "horizontal scale"
    ],

    "scale_up_cpu": [
        "cpu limit",
        "cpu request",
        "increase cpu",
        "cpu resource"
    ],

    "scale_up_memory": [
        "memory limit",
        "memory request",
        "increase memory",
        "memory resource"
    ],

    "rollback": [
        "rollback",
        "previous image",
        "previous version"
    ],

    "config_fix": [
        "configmap",
        "env var",
        "secret",
        "configuration"
    ],

    "traffic_control": [
        "network",
        "traffic",
        "latency",
        "packet loss"
    ],

    "none": [
        "wait",
        "self heal",
        "no action"
    ]
}

def extract_health_raw(
    metrics: Dict[str, Any],
    infra_state: Optional[Dict[str, Any]] = None,
    phase: str = "observation",   # "before", "after", "observation"
) -> Dict[str, Optional[float]]:
    infra_state = infra_state or {}

    phase = (phase or "observation").lower()

    if phase == "after":
        latency_stats = ("p95", "last", "mean", "max")
        pressure_stats = ("last", "mean", "p95", "max")
        error_stats = ("last", "mean", "p95", "max")
        availability_stats = ("last", "mean", "min")
    else:
        latency_stats = ("p95", "max", "mean", "last")
        pressure_stats = ("mean", "p95", "last", "max")
        error_stats = ("mean", "p95", "last", "max")
        availability_stats = ("mean", "last", "min")

    latency_p95_ms = find_metric(
        metrics,
        [
            "latency_p95",
            "edge_latency_p95",
            "http_request_duration_seconds_p95",
            "request_latency_p95",
            "p95_latency",
        ],
        stats=latency_stats,
    )

    error_rate = find_metric(
        metrics,
        [
            "error_rate_5xx",
            "error_5xx_rate",
            "error_5xx",
            "http_5xx_rate",
            "5xx_rate",
            "error_rate",
        ],
        stats=error_stats,
    )
    error_rate = ratio_from_percent_or_fraction(error_rate)

    availability = find_metric(
        metrics,
        ["availability", "success_rate", "request_success_rate"],
        stats=availability_stats,
    )
    availability = ratio_from_percent_or_fraction(availability)

    if availability is None and error_rate is not None:
        availability = 1.0 - error_rate

    pod_ready_ratio = find_metric(
        metrics,
        ["pod_ready_ratio", "ready_pod_ratio"],
        stats=("last", "mean", "min"),
    )
    pod_ready_ratio = ratio_from_percent_or_fraction(pod_ready_ratio)

    if pod_ready_ratio is None:
        ready = safe_float(
            infra_state.get("target_ready_pods")
            or infra_state.get("deployment_ready_pods")
            or infra_state.get("namespace_ready_pods")
            or infra_state.get("ready_pods")
        )
        running = safe_float(
            infra_state.get("target_running_pods")
            or infra_state.get("deployment_running_pods")
            or infra_state.get("namespace_running_pods")
            or infra_state.get("running_pods")
        )

        if ready is not None and running is not None and running > 0:
            pod_ready_ratio = ready / running
        elif running is not None and running > 0:
            pod_ready_ratio = 1.0

    cpu_usage_ratio = find_metric_stat(
        metrics,
        [
            "cpu_usage_to_limit_ratio",
            "cpu_limit_ratio",
            "cpu_saturation",
            "cpu_utilization",
        ],
        preferred_stats=pressure_stats,
    )

    cpu_throttle_ratio = find_metric_stat(
        metrics,
        [
            "cpu_throttle_ratio",
            "cpu_throttling_ratio",
            "cpu_throttling",
        ],
        preferred_stats=pressure_stats,
    )

    cpu_usage_ratio = ratio_from_percent_or_fraction(cpu_usage_ratio)
    cpu_throttle_ratio = ratio_from_percent_or_fraction(cpu_throttle_ratio)

    cpu_values = [
        v for v in [cpu_usage_ratio, cpu_throttle_ratio]
        if v is not None
    ]
    cpu = max(cpu_values) if cpu_values else None

    memory = find_metric_stat(
        metrics,
        [
            "memory_usage_to_limit_ratio",
            "memory_limit_ratio",
            "memory_saturation",
        ],
        preferred_stats=pressure_stats,
    )
    memory = ratio_from_percent_or_fraction(memory)

    fs_read_bps = find_metric_stat(
        metrics,
        ["fs_read_bytes_per_sec", "filesystem_read_bytes", "disk_read_bytes"],
        preferred_stats=pressure_stats,
    )

    fs_write_bps = find_metric_stat(
        metrics,
        ["fs_write_bytes_per_sec", "filesystem_write_bytes", "disk_write_bytes"],
        preferred_stats=pressure_stats,
    )

    fs_usage_ratio = find_metric_stat(
        metrics,
        ["fs_usage_to_limit_ratio", "filesystem_usage_ratio", "disk_usage_ratio"],
        preferred_stats=pressure_stats,
    )
    fs_usage_ratio = ratio_from_percent_or_fraction(fs_usage_ratio)

    disk_io = max(
        [v for v in [fs_read_bps, fs_write_bps] if v is not None],
        default=None,
    )

    return {
        "latency_p95_ms": latency_p95_ms,
        "error_rate": error_rate,
        "availability": availability,
        "pod_ready_ratio": pod_ready_ratio,

        "cpu": cpu,
        "cpu_usage_to_limit_ratio": cpu_usage_ratio,
        "cpu_throttle_ratio": cpu_throttle_ratio,

        "memory": memory,
        "memory_usage_to_limit_ratio": memory,
        "disk_io": disk_io,
        "fs_read_bytes_per_sec": fs_read_bps,
        "fs_write_bytes_per_sec": fs_write_bps,
        "fs_usage_to_limit_ratio": fs_usage_ratio,
    }

def decreasing_health(value: Optional[float], good: float, bad: float, default: float = 0.5) -> float:
    if value is None:
        return default
    if value <= good:
        return 1.0
    if value >= bad:
        return 0.0
    return clamp(1.0 - ((value - good) / (bad - good)), default=default)


def increasing_health(value: Optional[float], bad: float, good: float, default: float = 0.5) -> float:
    if value is None:
        return default
    if value >= good:
        return 1.0
    if value <= bad:
        return 0.0
    return clamp((value - bad) / (good - bad), default=default)

def aggregate_observed_services(
    metrics: Dict[str, Any],
    phase: str = "observation",
) -> Dict[str, Any]:
    """
    Build system-level metrics by aggregating all observed services.
    This prevents system_raw from accidentally becoming the first/target service.
    """
    if not metrics:
        return {}

    observations = metrics.get("service_observations", {})
    if not observations:
        return metrics

    raw_values = []

    for service_name, service_metrics in observations.items():
        raw = extract_health_raw(service_metrics, phase=phase)
        if raw:
            raw_values.append(raw)

    if not raw_values:
        return metrics

    def vals(key: str) -> list[float]:
        return [
            float(r[key])
            for r in raw_values
            if r.get(key) is not None
        ]

    def max_or_none(key: str):
        v = vals(key)
        return max(v) if v else None

    def min_or_none(key: str):
        v = vals(key)
        return min(v) if v else None

    def mean_or_none(key: str):
        v = vals(key)
        return sum(v) / len(v) if v else None

    return {
        "system_aggregate": {
            "application_metrics": {
                "application_api": {
                    "latency_p95": {
                        "aggregate_stats": {
                            "max": max_or_none("latency_p95_ms"),
                            "p95": max_or_none("latency_p95_ms"),
                            "last": max_or_none("latency_p95_ms"),
                            "mean": mean_or_none("latency_p95_ms"),
                        }
                    },
                    "error_rate_5xx": {
                        "aggregate_stats": {
                            "max": max_or_none("error_rate"),
                            "p95": max_or_none("error_rate"),
                            "last": max_or_none("error_rate"),
                            "mean": mean_or_none("error_rate"),
                        }
                    },
                    "availability": {
                        "aggregate_stats": {
                            "min": min_or_none("availability"),
                            "last": min_or_none("availability"),
                            "mean": mean_or_none("availability"),
                        }
                    },
                }
            },
            "system_metrics": {
                "container_resources": {
                    "cpu_usage_to_limit_ratio": {
                        "aggregate_stats": {
                            "max": max_or_none("cpu"),
                            "p95": max_or_none("cpu"),
                            "last": max_or_none("cpu"),
                            "mean": mean_or_none("cpu"),
                        }
                    },
                    "memory_usage_to_limit_ratio": {
                        "aggregate_stats": {
                            "max": max_or_none("memory"),
                            "p95": max_or_none("memory"),
                            "last": max_or_none("memory"),
                            "mean": mean_or_none("memory"),
                        }
                    },
                    "pod_ready_ratio": {
                        "aggregate_stats": {
                            "min": min_or_none("pod_ready_ratio"),
                            "last": min_or_none("pod_ready_ratio"),
                            "mean": mean_or_none("pod_ready_ratio"),
                        }
                    },
                }
            },
        }
    }

def compute_system_health_score(
    metrics,
    infra_state=None,
    slo_thresholds=None,
    fault_type: str = "",
    phase: str = "observation",
):
    slo = slo_thresholds or {}
    raw = extract_health_raw(metrics, infra_state, phase=phase)

    k8s_raw = extract_k8s_fault_raw(metrics)
    k8s_health = k8s_fault_health(k8s_raw)

    components = {
        "latency_health": decreasing_health(raw["latency_p95_ms"], slo.get("latency_good_ms", 100.0), slo.get("latency_bad_ms", 400.0)),
        "error_health": decreasing_health(raw["error_rate"], slo.get("error_good_rate", 0.01), slo.get("error_bad_rate", 0.05)),
        "availability_health": increasing_health(raw["availability"], slo.get("availability_bad", 0.95), slo.get("availability_good", 0.99)),
        "pod_ready_health": increasing_health(raw["pod_ready_ratio"], slo.get("pod_ready_bad", 0.50), slo.get("pod_ready_good", 1.00)),
        "cpu_health": decreasing_health(raw["cpu"], slo.get("cpu_good", 0.70), slo.get("cpu_bad", 0.95)),
        "memory_health": decreasing_health(raw["memory"], slo.get("memory_good", 0.70), slo.get("memory_bad", 0.95)),
        "k8s_fault_health": k8s_health,
        "disk_io_health": decreasing_health(
            raw.get("disk_io"),
            slo.get("disk_io_good_bps", 5 * 1024 * 1024),
            slo.get("disk_io_bad_bps", 50 * 1024 * 1024),
        ),
        "disk_usage_health": decreasing_health(
            raw.get("fs_usage_to_limit_ratio"),
            slo.get("disk_usage_good", 0.70),
            slo.get("disk_usage_bad", 0.90),
        ),
    }

    raw.update(k8s_raw)

    # DEFAULT weights: always defined
    ft = (fault_type or "").lower()

    if "mem_stress" in ft or "memory_pressure" in ft:
        weights = {
        "latency_health": 0.05,
        "error_health": 0.05,
        "availability_health": 0.10,
        "pod_ready_health": 0.10,
        "cpu_health": 0.10,
        "memory_health": 0.60,
    }
    elif "cpu_hog" in ft or "cpu_pressure" in ft:
        weights = {
            "latency_health": 0.10,
            "error_health": 0.10,
            "availability_health": 0.10,
            "pod_ready_health": 0.10,
            "cpu_health": 0.50,
            "memory_health": 0.10,
        }
    elif "net_delay" in ft or "network_delay" in ft or "network_latency" in ft:
        weights = {
            "latency_health": 0.55,
            "error_health": 0.15,
            "availability_health": 0.15,
            "pod_ready_health": 0.05,
            "cpu_health": 0.05,
            "memory_health": 0.05,
        }

    elif "net_loss" in ft or "packet_loss" in ft:
        weights = {
            "latency_health": 0.30,
            "error_health": 0.35,
            "availability_health": 0.20,
            "pod_ready_health": 0.05,
            "cpu_health": 0.05,
            "memory_health": 0.05,
        }
    elif "disk_stress" in ft or "disk_pressure" in ft or "disk_io" in ft:
        weights = {
            "latency_health": 0.20,
            "error_health": 0.10,
            "availability_health": 0.10,
            "pod_ready_health": 0.05,
            "cpu_health": 0.05,
            "memory_health": 0.05,
            "disk_io_health": 0.35,
            "disk_usage_health": 0.10,
        }
    else:
        weights = {
            "latency_health": 0.30,
            "error_health": 0.25,
            "availability_health": 0.15,
            "pod_ready_health": 0.10,
            "cpu_health": 0.10,
            "memory_health": 0.10,
        }

    # K8s fault-aware weights
    if any(v > 0 for v in k8s_raw.values()):
        weights = {
            "latency_health": 0.20,
            "error_health": 0.15,
            "availability_health": 0.15,
            "pod_ready_health": 0.10,
            "cpu_health": 0.05,
            "memory_health": 0.05,
            "k8s_fault_health": 0.30,
        }

    score = sum(weights[k] * components[k] for k in weights)

    return {
        "SHS": round(clamp(score), 6),
        "components": {k: round(v, 6) for k, v in components.items()},
        "raw": raw,
        "weights": weights,
    }


def relative_decrease_improvement(before: Optional[float], after: Optional[float], min_before: float = 1e-9) -> float:
    if before is None or after is None or before <= min_before:
        return 0.0
    return clamp((before - after) / before)


def relative_increase_improvement(before: Optional[float], after: Optional[float], min_before: float = 1e-9) -> float:
    if before is None or after is None or before <= min_before:
        return 0.0
    return clamp((after - before) / before)


def compute_relative_recovery(
    before_health: Dict[str, Any],
    after_health: Dict[str, Any],
    fault_type: str = "",
) -> Dict[str, Any]:
    before_raw_all = before_health.get("raw", {})
    after_raw_all = after_health.get("raw", {})

    before_raw = before_raw_all.get("target_raw", before_raw_all)
    after_raw = after_raw_all.get("target_raw", after_raw_all)

    before_read = before_raw.get("fs_read_bytes_per_sec")
    after_read = after_raw.get("fs_read_bytes_per_sec")

    before_write = before_raw.get("fs_write_bytes_per_sec")
    after_write = after_raw.get("fs_write_bytes_per_sec")

    disk_read_improvement = relative_decrease_improvement(before_read, after_read)
    disk_write_improvement = relative_decrease_improvement(before_write, after_write)

    disk_io_improvement = min(
        disk_read_improvement,
        disk_write_improvement,
    )

    components = {
        "latency_improvement": relative_decrease_improvement(before_raw.get("latency_p95_ms"), after_raw.get("latency_p95_ms")),
        "error_improvement": relative_decrease_improvement(before_raw.get("error_rate"), after_raw.get("error_rate")),
        "availability_improvement": relative_increase_improvement(before_raw.get("availability"), after_raw.get("availability")),
        "cpu_improvement": relative_decrease_improvement(before_raw.get("cpu"), after_raw.get("cpu")),
        "memory_improvement": relative_decrease_improvement(before_raw.get("memory"), after_raw.get("memory")),
        "pod_not_ready_improvement": relative_decrease_improvement(before_raw.get("pod_not_ready_count"), after_raw.get("pod_not_ready_count"), min_before=0.0),
        "container_waiting_improvement": relative_decrease_improvement(before_raw.get("container_waiting_count"), after_raw.get("container_waiting_count"), min_before=0.0),
        "disk_read_improvement": disk_read_improvement,
        "disk_write_improvement": disk_write_improvement,
        "disk_io_improvement": disk_io_improvement,
        "replicas_unavailable_improvement": relative_decrease_improvement(before_raw.get("replicas_unavailable_count"), after_raw.get("replicas_unavailable_count"), min_before=0.0),
    }

    weights = relative_weights_for_fault(fault_type)

    rq = clamp(sum(weights[k] * components.get(k, 0.0) for k in weights))

    return {
        "MU": round(rq, 6),
        "RQ": round(rq, 6),
        "relative_improvement_components": {k: round(v, 6) for k, v in components.items()},
        "relative_improvement_weights": weights,
    }

def dependency_recovery_override(
    *,
    fault_type: str,
    before_health: Dict[str, Any],
    after_health: Dict[str, Any],
    relative: Dict[str, Any],
    infra_state_before: Optional[Dict[str, Any]] = None,
    infra_state_after: Optional[Dict[str, Any]] = None,
    infra_comparison: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Dependency failure is often visible as pod/replica restoration rather than
    immediate latency/error improvement.

    This override only affects dependency_failure.
    It does not change SHS. It only adjusts RQ/MU/recovery_success/reward.
    """
    if "dependency_failure" not in (fault_type or "").lower():
        return relative

    before_raw = before_health.get("raw", {})
    after_raw = after_health.get("raw", {})

    infra_state_before = infra_state_before or {}
    infra_state_after = infra_state_after or {}
    infra_comparison = infra_comparison or {}

    improvements = {
        "pod_not_ready_improvement": relative_decrease_improvement(
            before_raw.get("pod_not_ready_count"),
            after_raw.get("pod_not_ready_count"),
            min_before=0.0,
        ),
        "container_waiting_improvement": relative_decrease_improvement(
            before_raw.get("container_waiting_count"),
            after_raw.get("container_waiting_count"),
            min_before=0.0,
        ),
        "replicas_unavailable_improvement": relative_decrease_improvement(
            before_raw.get("replicas_unavailable_count"),
            after_raw.get("replicas_unavailable_count"),
            min_before=0.0,
        ),
    }

    before_running = safe_float(
        infra_state_before.get("target_running_pods")
        or infra_state_before.get("deployment_running_pods")
        or infra_state_before.get("running_pods")
    )

    after_running = safe_float(
        infra_state_after.get("target_running_pods")
        or infra_state_after.get("deployment_running_pods")
        or infra_state_after.get("running_pods")
    )

    namespace_delta = safe_float(
        infra_comparison.get("namespace_running_pods_delta")
    )

    dependency_restore_improvement = 0.0

    if before_running == 0 and after_running is not None and after_running > 0:
        dependency_restore_improvement = 1.0
    elif namespace_delta is not None and namespace_delta > 0:
        dependency_restore_improvement = 1.0

    improvements["dependency_restore_improvement"] = dependency_restore_improvement

    dep_rq = max(improvements.values())

    if dep_rq > relative.get("RQ", 0.0):
        relative["RQ"] = round(dep_rq, 6)
        relative["MU"] = round(dep_rq, 6)
        relative["relative_improvement_components"].update(
            {k: round(v, 6) for k, v in improvements.items()}
        )
        relative["relative_improvement_weights"]["dependency_restore_override"] = 1.0

    return relative

def plan_text(plan: Optional[Dict[str, Any]]) -> str:
    return "" if not plan else str(plan).lower()

def compute_resource_cost_target(before_metrics, after_metrics, plan_action):
    before_mem = find_metric(before_metrics, ["memory_limit_per_pod"], stats=("mean", "last"))
    after_mem = find_metric(after_metrics, ["memory_limit_per_pod"], stats=("mean", "last"))

    before_cpu = find_metric(before_metrics, ["cpu_limit_per_pod"], stats=("mean", "last"))
    after_cpu = find_metric(after_metrics, ["cpu_limit_per_pod"], stats=("mean", "last"))

    mem_inc = 0.0
    if before_mem and after_mem and after_mem > before_mem:
        mem_inc = (after_mem - before_mem) / before_mem

    cpu_inc = 0.0
    if before_cpu and after_cpu and after_cpu > before_cpu:
        cpu_inc = (after_cpu - before_cpu) / before_cpu

    return {
        "resource_cost": round(clamp(0.5 * mem_inc + 0.5 * cpu_inc), 6),
        "replica_delta": 0.0,
        "namespace_cpu_increase_ratio": 0.0,
        "namespace_memory_increase_ratio": 0.0,
    }


def classify_plan_action(plan: Optional[Dict[str, Any]]) -> str:
    """
    Normalize remediation plans from:
    - rule_based planner: action/strategy fields
    - EV-AIM/LLM planner: actions list, target_changes, remediation_script-like text
    """

    if not plan:
        return "unknown"

    def norm(x: Any) -> str:
        return str(x or "").lower().strip().replace("-", "_").replace(" ", "_")

    def plan_text_safe(obj: Any) -> str:
        try:
            return json.dumps(obj, default=str).lower()
        except Exception:
            return str(obj).lower()

    text = plan_text_safe(plan)

    execution_required = plan.get("execution_required", True)
    if isinstance(execution_required, str):
        execution_required = execution_required.lower() in {"true", "yes", "1", "required"}

    # 1. FIRST: explicit rule-based / planner action fields
    explicit_candidates = [
        plan.get("action"),
        plan.get("strategy"),
        plan.get("remediation_action"),
        plan.get("planner_action"),
        plan.get("plan_action"),
    ]

    for raw in explicit_candidates:
        a = norm(raw)

        if a in {"noop", "no_op", "monitor", "observe", "observe_only", "none"}:
            return "noop"

        if a in {"set_memory_limit", "scale_up_memory", "increase_memory", "memory_limit"}:
            return "scale_up_memory"

        if a in {"set_cpu_limit", "scale_up_cpu", "increase_cpu", "cpu_limit"}:
            return "scale_up_cpu"

        if a in {"scale_out", "scale", "increase_replicas", "scale_replicas"}:
            return "scale_out"

        if a in {"rollout_restart", "restart", "kubectl_rollout_restart", "delete_pod", "recreate_pod"}:
            return "restart"

        if a in {"rollout_undo", "rollback", "undo", "restore_image"}:
            return "rollback"

        if a in {"rollout_resume", "resume_rollout", "resume", "rollout_resume_then_undo"}:
            return "resume_rollout"

        if a in {
            "restore_dependency",
            "scale_dependency",
            "scale_to_original",
            "scale_to_original_replicas",
            "restore_replicas",
        }:
            return "restore_dependency"

        if a in {"stop_load", "stop_traffic", "traffic_control", "rate_limit"}:
            return "traffic_control"

    # 2. EV-AIM target_changes schema
    target_changes = plan.get("target_changes") or {}
    change_type = norm(target_changes.get("type"))

    if change_type in {"memory_limit", "memory_request", "memory"}:
        return "scale_up_memory"

    if change_type in {"cpu_limit", "cpu_request", "cpu"}:
        return "scale_up_cpu"

    if change_type in {"replicas", "replica", "scale"}:
        if any(x in text for x in ["dependency", "restore", "original_replicas", "scale_to_original"]):
            return "restore_dependency"
        return "scale_out"

    if change_type in {"image", "container_image"}:
        return "rollback"

    if change_type in {"config", "configuration", "env"}:
        return "restore_config"

    # 3. EV-AIM actions list schema
    actions = plan.get("actions") or []
    if isinstance(actions, dict):
        actions = [actions]

    for act in actions:
        act_text = plan_text_safe(act)

        if any(x in act_text for x in ["set resources", "limits=memory", "memory limit", "increase memory"]):
            return "scale_up_memory"

        if any(x in act_text for x in ["limits=cpu", "cpu limit", "increase cpu"]):
            return "scale_up_cpu"

        if any(x in act_text for x in ["kubectl scale", "replicas", "scale out"]):
            if any(x in act_text for x in ["dependency", "original"]):
                return "restore_dependency"
            return "scale_out"

        if any(x in act_text for x in ["rollout restart", "delete pod", "restart"]):
            return "restart"

        if any(x in act_text for x in ["rollout undo", "rollback"]):
            return "rollback"

        if any(x in act_text for x in ["rollout resume"]):
            return "resume_rollout"

    # 4. No execution only after explicit action checks
    if not execution_required:
        return "noop"

    if change_type in {"none", "noop", "no_op"}:
        return "noop"

    # 5. Fallback text classifier
    if any(x in text for x in ["set resources", "--limits=memory", "memory limit", "increase memory", "oom"]):
        return "scale_up_memory"

    if any(x in text for x in ["--limits=cpu", "cpu limit", "increase cpu"]):
        return "scale_up_cpu"

    if any(x in text for x in ["scale out", "increase replica", "kubectl scale", "replicas"]):
        if any(x in text for x in ["dependency", "original replicas", "scale to original"]):
            return "restore_dependency"
        return "scale_out"

    if any(x in text for x in ["rollout restart", "restart deployment", "delete pod", "recreate pod"]):
        return "restart"

    if any(x in text for x in ["rollout undo", "rollback", "previous revision"]):
        return "rollback"

    if any(x in text for x in ["rollout resume", "resume rollout"]):
        return "resume_rollout"

    if any(x in text for x in ["restore dependency", "scale dependency", "restore service"]):
        return "restore_dependency"

    if any(x in text for x in ["stop load", "stop traffic", "rate limit", "traffic control"]):
        return "traffic_control"

    if any(x in text for x in ["no-op", "noop", "monitor only", "observe only", "no remediation"]):
        return "noop"

    return "unknown"

def fault_specific_success(
    *,
    fault_type: str,
    before_health: Dict[str, Any],
    after_health: Dict[str, Any],
    infra_state_before: Optional[Dict[str, Any]],
    infra_state_after: Optional[Dict[str, Any]],
    infra_comparison: Optional[Dict[str, Any]],
    execution_failed: int,
    rollout_failed: int,
) -> Tuple[bool, str]:
    if execution_failed or rollout_failed:
        return False, "execution_or_rollout_failed"

    ft = (fault_type or "").lower()

    before_raw = before_health.get("raw", {})
    after_raw = after_health.get("raw", {})

    br = before_raw.get("target_raw", before_raw)
    ar = after_raw.get("target_raw", after_raw)

    before_cpu = safe_float(br.get("cpu"))
    after_cpu = safe_float(ar.get("cpu"))
    before_mem = safe_float(br.get("memory"))
    after_mem = safe_float(ar.get("memory"))
    before_lat = safe_float(br.get("latency_p95_ms"))
    after_lat = safe_float(ar.get("latency_p95_ms"))
    before_err = safe_float(br.get("error_rate"))
    after_err = safe_float(ar.get("error_rate"))

    before_not_ready = safe_float(br.get("pod_not_ready_count")) or 0.0
    after_not_ready = safe_float(ar.get("pod_not_ready_count")) or 0.0
    before_waiting = safe_float(br.get("container_waiting_count")) or 0.0
    after_waiting = safe_float(ar.get("container_waiting_count")) or 0.0
    before_unavailable = safe_float(br.get("replicas_unavailable_count")) or 0.0
    after_unavailable = safe_float(ar.get("replicas_unavailable_count")) or 0.0

    if "mem_stress" in ft:
        if before_mem is not None and after_mem is not None:
            if before_mem >= 0.80 and after_mem <= 0.75:
                return True, "memory_pressure_recovered"
            if after_mem < before_mem and before_mem >= 0.70:
                return True, "memory_pressure_improved"

    if "cpu_hog" in ft:
        if before_cpu is not None and after_cpu is not None:
            if before_cpu >= 0.80 and after_cpu <= 0.75:
                return True, "cpu_pressure_recovered"
            if after_cpu < before_cpu and before_cpu >= 0.70:
                return True, "cpu_pressure_improved"

    if "pod_kill" in ft or "pod_crash" in ft:
        if after_not_ready == 0 and after_waiting == 0 and after_unavailable == 0:
            return True, "pod_recovered"

    if "net_delay" in ft or "network_delay" in ft or "network_latency" in ft:
        if before_lat is not None and after_lat is not None:
            if before_lat >= 400 and after_lat < before_lat:
                return True, "latency_improved"
            if after_lat <= 300:
                return True, "latency_recovered"

    if "net_loss" in ft or "packet_loss" in ft:
        latency_ok = before_lat is not None and after_lat is not None and after_lat < before_lat
        error_ok = before_err is not None and after_err is not None and after_err < before_err
        # if latency_ok or error_ok:
        #     return True, "network_loss_improved"

        latency_improvement = relative_decrease_improvement(before_lat, after_lat)
        error_improvement = relative_decrease_improvement(before_err, after_err)

        if error_improvement >= 0.50:
            return True, "network_loss_error_recovered"

        if latency_improvement >= 0.50:
            return True, "network_loss_latency_recovered"

        if after_lat is not None and after_lat <= 1500 and (after_err is None or after_err <= 0.01):
            return True, "network_loss_recovered_to_slo"

    if "dependency_failure" in ft:
        infra_comparison = infra_comparison or {}

        if infra_comparison.get("replica_restore_occurred"):
            return True, "dependency_replica_restored"

        if infra_comparison.get("scale_out_occurred"):
            return True, "dependency_scaled_from_zero"

        namespace_delta = safe_float(infra_comparison.get("namespace_running_pods_delta"))
        if namespace_delta is not None and namespace_delta > 0:
            return True, "dependency_namespace_pod_restored"

        before_ready = safe_float(br.get("replicas_ready")) or 0.0
        after_ready = safe_float(ar.get("replicas_ready")) or 0.0
        before_avail = safe_float(br.get("replicas_available")) or 0.0
        after_avail = safe_float(ar.get("replicas_available")) or 0.0

        if before_ready == 0 and after_ready > 0:
            return True, "dependency_ready_replica_restored"

        if before_avail == 0 and after_avail > 0:
            return True, "dependency_available_replica_restored"
        
    if "disk_stress" in ft or "disk_pressure" in ft or "disk_io" in ft:
        before_disk = safe_float(br.get("disk_io"))
        after_disk = safe_float(ar.get("disk_io"))

        if before_disk is not None and after_disk is not None:
            if before_disk >= 10 * 1024 * 1024 and after_disk < before_disk:
                return True, "disk_io_pressure_improved"

            if after_disk <= 5 * 1024 * 1024:
                return True, "disk_io_pressure_recovered"

    return False, "no_fault_specific_success"

def expected_actions_for_fault(fault_type: str) -> set[str]:
    ft = (fault_type or "").lower()

    mapping = {
        "mem_stress": {"scale_up_memory", "restart", "scale_out", "noop"},
        "memory_pressure": {"scale_up_memory", "restart", "scale_out", "noop"},
        "cpu_hog": {"scale_out", "scale_up_cpu", "restart", "noop"},
        "cpu_pressure": {"scale_out", "scale_up_cpu", "restart", "noop"},
        "cpu_throttle": {"scale_up_cpu", "scale_out", "restart", "noop"},
        "disk_stress": {
                        "scale_out",
                        "restart",
                        "scale_up_memory",
                        "scale_up_cpu"
                    },
        "config_error": {"rollback", "restart", "noop"},
        "pod_kill": {"restart", "noop"},
        "pod_crash": {"restart", "noop"},
        "bad_image": {"rollback"},
        "stuck_deployment": {"resume_rollout", "rollback"},
        "dependency_failure": {"restore_dependency", "scale_out", "restart", "noop"},
        "load_spike": {"scale_out", "traffic_control", "noop"},
        "db_overload": {"scale_out", "scale_up_cpu", "scale_up_memory", "restart", "noop"},
        "net_delay": {"traffic_control", "restart", "scale_out", "scale_up_cpu", "scale_up_memory", "noop"},
        "net_loss": {"traffic_control", "restart", "scale_out", "scale_up_cpu", "scale_up_memory", "noop"},
    }

    for key, value in mapping.items():
        if key in ft:
            return value

    return set()


def compute_plan_success(plan: Optional[Dict[str, Any]], fault_type: str, execution_required: bool = True) -> Dict[str, Any]:
    action = classify_plan_action(plan)
    expected = expected_actions_for_fault(fault_type)
    if action == "noop":
        score = 1.0 if not execution_required else 0.0
    elif not expected:
        score = 0.5 if action != "unknown" else 0.0
    else:
        score = 1.0 if action in expected else 0.0
    return {"PS": score, "plan_action": action, "expected_actions": sorted(expected)}


def parse_ansible_recap_counts(ansible_log: str) -> Dict[str, int]:
    counts = {"ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    if not ansible_log:
        return counts
    for k in counts:
        m = re.search(rf"{k}\s*=\s*(\d+)", ansible_log)
        if m:
            counts[k] = int(m.group(1))
    return counts

def relative_weights_for_fault(fault_type: str) -> Dict[str, float]:
    ft = (fault_type or "").lower()

    if "mem_stress" in ft or "memory_pressure" in ft:
        return {
            "latency_improvement": 0.05,
            "error_improvement": 0.05,
            "availability_improvement": 0.05,
            "cpu_improvement": 0.05,
            "memory_improvement": 0.70,
            "pod_not_ready_improvement": 0.03,
            "container_waiting_improvement": 0.04,
            "replicas_unavailable_improvement": 0.03,
        }

    if "cpu_hog" in ft or "cpu_pressure" in ft:
        return {
            "latency_improvement": 0.05,
            "error_improvement": 0.05,
            "availability_improvement": 0.05,
            "cpu_improvement": 0.70,
            "memory_improvement": 0.05,
            "pod_not_ready_improvement": 0.03,
            "container_waiting_improvement": 0.04,
            "replicas_unavailable_improvement": 0.03,
        }
    if "net_delay" in ft or "network_delay" in ft or "network_latency" in ft:
        return {
            "latency_improvement": 0.70,
            "error_improvement": 0.10,
            "availability_improvement": 0.10,
            "cpu_improvement": 0.02,
            "memory_improvement": 0.02,
            "pod_not_ready_improvement": 0.02,
            "container_waiting_improvement": 0.02,
            "replicas_unavailable_improvement": 0.02,
        }

    if "net_loss" in ft or "packet_loss" in ft:
        return {
            "latency_improvement": 0.35,
            "error_improvement": 0.40,
            "availability_improvement": 0.15,
            "cpu_improvement": 0.02,
            "memory_improvement": 0.02,
            "pod_not_ready_improvement": 0.02,
            "container_waiting_improvement": 0.02,
            "replicas_unavailable_improvement": 0.02,
        }
    
    if "disk_stress" in ft or "disk_pressure" in ft or "disk_io" in ft:
        return {
            "disk_io_improvement": 0.80,
            "latency_improvement": 0.10,
            "error_improvement": 0.05,
            "availability_improvement": 0.05,
        }

    return {
        "latency_improvement": 0.20,
        "error_improvement": 0.15,
        "availability_improvement": 0.10,
        "cpu_improvement": 0.10,
        "memory_improvement": 0.20,
        "pod_not_ready_improvement": 0.10,
        "container_waiting_improvement": 0.10,
        "replicas_unavailable_improvement": 0.05,
    }

def compute_execution_success(
    plan: Optional[Dict[str, Any]],
    execution_status: str,
    execution_error: Optional[str],
    rollout_result: Optional[Dict[str, Any]],
    ansible_log: str,
    infra_comparison: Optional[Dict[str, Any]] = None,
    execution_required: bool = True,
) -> Dict[str, Any]:

    action = classify_plan_action(plan)
    recap = parse_ansible_recap_counts(ansible_log)
    infra_comparison = infra_comparison or {}
    rollout_result = rollout_result or {}

    if not execution_required:
        return {
            "ES": 1.0,
            "code_changed_system": False,
            "ansible_recap_counts": recap,
            "execution_failure_reason": None,
        }

    if execution_status != "success" or execution_error:
        return {
            "ES": 0.0,
            "code_changed_system": False,
            "ansible_recap_counts": recap,
            "execution_failure_reason": execution_error or execution_status,
        }

    if recap.get("failed", 0) > 0 or recap.get("unreachable", 0) > 0:
        return {
            "ES": 0.0,
            "code_changed_system": recap.get("changed", 0) > 0,
            "ansible_recap_counts": recap,
            "execution_failure_reason": "ansible_failed_or_unreachable",
        }

    rollout_ok = bool(rollout_result.get("rollout_completed", True))
    ansible_changed = recap.get("changed", 0) > 0

    scale_out_observed = bool(
                                infra_comparison.get("scale_out_occurred")
                                or (
                                    action == "scale_out"
                                    and safe_float(infra_comparison.get("namespace_running_pods_delta")) is not None
                                    and safe_float(infra_comparison.get("namespace_running_pods_delta")) > 0
                                )
                                or (
                                    action == "scale_out"
                                    and safe_float(infra_comparison.get("target_replicas_desired_delta")) is not None
                                    and safe_float(infra_comparison.get("target_replicas_desired_delta")) > 0
                                )
                            )

    scale_up_observed = bool(
        infra_comparison.get("scale_up_occurred")
        or infra_comparison.get("resource_change_occurred")
        or infra_comparison.get("memory_limit_changed")
        or infra_comparison.get("cpu_limit_changed")
        or (
            infra_comparison.get("target_memory_limit_per_pod_delta_bytes") is not None
            and infra_comparison.get("target_memory_limit_per_pod_delta_bytes") > 0
        )
        or (
            infra_comparison.get("target_cpu_limit_per_pod_delta") is not None
            and infra_comparison.get("target_cpu_limit_per_pod_delta") > 0
        )
    )

    rollout_action_observed = bool(
        action in {"restart", "rollback", "resume_rollout"}
        and rollout_ok
    )

    if action == "scale_out":
        observed_action_effect = scale_out_observed

    elif action in {"scale_up_cpu", "scale_up_memory", "scale_out_and_memory"}:
        observed_action_effect = scale_up_observed

    elif action in {"restart", "rollback", "resume_rollout"}:
        observed_action_effect = rollout_action_observed

    elif action in {"traffic_control", "restore_dependency"}:
        observed_action_effect = rollout_ok or ansible_changed

    else:
        observed_action_effect = ansible_changed or scale_out_observed or scale_up_observed

    code_changed_system = bool(
        ansible_changed
        or scale_out_observed
        or scale_up_observed
        or rollout_action_observed
    )

    if rollout_ok and observed_action_effect:
        es = 1.0
        reason = None
    elif rollout_ok and ansible_changed:
        es = 0.75
        reason = None
    elif rollout_ok:
        es = 0.50
        reason = "rollout_ok_but_no_observed_action_effect"
    else:
        es = 0.25
        reason = "rollout_incomplete"

    return {
        "ES": es,
        "code_changed_system": code_changed_system,
        "ansible_recap_counts": recap,
        "execution_failure_reason": reason,
    }


def compute_resource_cost(infra_before: Optional[Dict[str, Any]], infra_after: Optional[Dict[str, Any]], infra_comparison: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    infra_before = infra_before or {}
    infra_after = infra_after or {}
    infra_comparison = infra_comparison or {}

    before_pods = safe_float(infra_before.get("namespace_running_pods"))
    after_pods = safe_float(infra_after.get("namespace_running_pods"))
    replica_delta = safe_float(infra_comparison.get("namespace_running_pods_delta"))
    if replica_delta is None and before_pods is not None and after_pods is not None:
        replica_delta = after_pods - before_pods
    replica_delta = max(0.0, replica_delta or 0.0)

    cpu_before = safe_float(infra_before.get("namespace_cpu_usage_cores"))
    cpu_after = safe_float(infra_after.get("namespace_cpu_usage_cores"))
    mem_before = safe_float(infra_before.get("namespace_memory_working_set_bytes"))
    mem_after = safe_float(infra_after.get("namespace_memory_working_set_bytes"))

    cpu_inc_ratio = 0.0
    if cpu_before and cpu_after and cpu_after > cpu_before:
        cpu_inc_ratio = (cpu_after - cpu_before) / cpu_before

    mem_inc_ratio = 0.0
    if mem_before and mem_after and mem_after > mem_before:
        mem_inc_ratio = (mem_after - mem_before) / mem_before

    replica_cost = min(replica_delta / 5.0, 1.0)
    cpu_cost = min(cpu_inc_ratio, 1.0)
    memory_cost = min(mem_inc_ratio, 1.0)
    total_cost = clamp(0.50 * replica_cost + 0.25 * cpu_cost + 0.25 * memory_cost)

    return {
        "resource_cost": round(total_cost, 6),
        "replica_delta": replica_delta,
        "namespace_cpu_increase_ratio": round(cpu_inc_ratio, 6),
        "namespace_memory_increase_ratio": round(mem_inc_ratio, 6),
    }


def select_target_metrics(metrics: dict, target_service: str) -> dict:
    if not metrics:
        return {}

    obs = metrics.get("service_observations", {})
    if target_service and target_service in obs:
        return obs[target_service].get("metrics", obs[target_service])

    return metrics


def feedback_scope_weights_old(fault_type: str) -> Tuple[float, float]:
    if fault_type in {"pod_crash", "bad_image", "stuck_deployment"}:
        return 0.70, 0.30

    if fault_type == "dependency_failure":
        return 0.50, 0.50

    if fault_type == "load_spike":
        return 0.30, 0.70

    # Resource/network pressure ripples beyond the target -> weight system health.
    if fault_type in {"db_overload", "cpu_hog", "mem_stress", "disk_stress",
                      "net_delay", "net_loss", "resource_pressure"}:
        return 0.40, 0.60

    return 0.50, 0.50

def feedback_scope_weights(fault_type: str) -> Tuple[float, float]:
    """
    Target-only feedback mode.
    Ignore system-wide health and score only the injected target service.
    """
    return 1.0, 0.0

def feedback_scope_weights_(fault_type: str):
    ft = (fault_type or "").lower()

    if ft in {
        "pod_crash",
        "pod_kill",
        "bad_image",
        "stuck_deployment",
        "config_error",
        "cpu_throttle",
        "mem_stress",
        "cpu_hog",
        "cpu_pressure",
        "memory_pressure",
    }:
        return 0.8, 0.2

    if ft in {
        "load_spike",
        "db_overload",
        "dependency_failure",
        "disk_stress",
        "net_delay",
        "net_loss",
        "network_delay",
        "network_latency",
        "packet_loss",
    }:
        return 0.4, 0.6

    return 0.6, 0.4


def combine_health_scores(
    target_health: Dict[str, Any],
    system_health: Dict[str, Any],
    target_weight: float,
    system_weight: float,
) -> Dict[str, Any]:
    target_shs = float(target_health.get("SHS") or 0.0)
    system_shs = float(system_health.get("SHS") or 0.0)

    combined_shs = round(
        target_weight * target_shs + system_weight * system_shs,
        6,
    )

    return {
        "SHS": combined_shs,
        "components": {
            "target_SHS": target_shs,
            "system_SHS": system_shs,
        },
        "raw": {
            "target_raw": target_health.get("raw", {}),
            "system_raw": system_health.get("raw", {}),
        },
        "weights": {
            "target_weight": target_weight,
            "system_weight": system_weight,
        },
    }


def dict_values_sum(d: Any) -> float:
    if not isinstance(d, dict):
        return 0.0
    total = 0.0
    for v in d.values():
        val = safe_float(v)
        if val is not None:
            total += val
    return total


def metric_values(metrics: Dict[str, Any], metric_name: str) -> Dict[str, Any]:
    if not metrics:
        return {}

    for path, value in _walk(metrics):
        if path.lower().endswith(f"{metric_name.lower()}.values") and isinstance(value, dict):
            return value
    return {}


def extract_k8s_fault_raw(metrics: Dict[str, Any]) -> Dict[str, float]:
    not_ready = dict_values_sum(metric_values(metrics, "pod_not_ready"))
    waiting = dict_values_sum(metric_values(metrics, "container_waiting_reason"))
    unavailable = dict_values_sum(metric_values(metrics, "replicas_unavailable"))
    generation_mismatch = dict_values_sum(metric_values(metrics, "deployment_generation_mismatch"))

    return {
        "pod_not_ready_count": not_ready,
        "container_waiting_count": waiting,
        "replicas_unavailable_count": unavailable,
        "generation_mismatch_count": generation_mismatch,
    }


def k8s_fault_health(raw: Dict[str, float]) -> float:
    penalty = (
        0.30 * min(raw.get("pod_not_ready_count", 0.0), 3.0) / 3.0
        + 0.35 * min(raw.get("container_waiting_count", 0.0), 2.0) / 2.0
        + 0.25 * min(raw.get("replicas_unavailable_count", 0.0), 2.0) / 2.0
        + 0.10 * min(raw.get("generation_mismatch_count", 0.0), 1.0)
    )
    return round(clamp(1.0 - penalty), 6)

def feedback_mode_for_fault(fault_type: str) -> str:
    ft = (fault_type or "").lower()

    target_only_faults = {
        "mem_stress", "memory_pressure",
        "cpu_hog", "cpu_pressure", "cpu_throttle",
        "pod_crash", "pod_kill",
        "bad_image", "stuck_deployment", "config_error",
    }

    path_faults = {
        "load_spike", "db_overload", "dependency_failure",
        "net_delay", "network_delay", "network_latency",
        "net_loss", "packet_loss",
    }

    for f in target_only_faults:
        if f in ft:
            return "target_only"

    for f in path_faults:
        if f in ft:
            return "target_plus_system"

    return "target_only"




# -----------------------------------------------------------------------------
# Fault-aware feedback additions
# -----------------------------------------------------------------------------
# These functions keep the old feedback API intact, but make the learning signal
# fault-aware and make plans comparable across rule-based, static LLM, and EV-AIM.

CANONICAL_ACTION_TYPES = {
    "restart",
    "scale_out",
    "scale_up_cpu",
    "scale_up_memory",
    "rollback",
    "config_fix",
    "traffic_control",
    "restore_dependency",
    "resume_rollout",
    "none",
    "unknown",
}


def find_metric_stat(
    metrics: Dict[str, Any],
    names: Iterable[str],
    preferred_stats: Iterable[str] = ("mean", "p95", "last"),
) -> Optional[float]:
    return find_metric(metrics, names, stats=preferred_stats)


def _safe_json_text(obj: Any) -> str:
    try:
        return json.dumps(obj or {}, default=str).lower()
    except Exception:
        return str(obj or {}).lower()


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _extract_action_value(plan: Optional[Dict[str, Any]], action_type: str) -> Optional[str]:
    if not plan:
        return None

    target_changes = plan.get("target_changes") or {}
    if isinstance(target_changes, dict):
        value = _first_non_empty(
        target_changes.get("target_value"),
        target_changes.get("value"),
        target_changes.get("new_value"),
        target_changes.get("after"),
        target_changes.get("to"),
        target_changes.get("limit"),
        target_changes.get("replicas"),
    )
        if value:
            return value

    for key in (
        "value",
        "action_value",
        "new_value",
        "target_value",
        "memory_limit",
        "cpu_limit",
        "replicas",
        "image",
    ):
        value = _first_non_empty(plan.get(key))
        if value:
            return value

    text = _safe_json_text(plan)

    if action_type == "scale_up_memory":
        m = re.search(r"(?:memory|--limits=memory)\s*[=: ]\s*([0-9.]+\s*(?:mi|mib|gi|gib|mb|gb|bytes?)?)", text)
        if m:
            return m.group(1).replace(" ", "")

    if action_type == "scale_up_cpu":
        m = re.search(r"(?:cpu|--limits=cpu)\s*[=: ]\s*([0-9.]+\s*(?:m|cores?)?)", text)
        if m:
            return m.group(1).replace(" ", "")

    if action_type == "scale_out":
        m = re.search(r"replicas?\s*[=: ]\s*([0-9]+)", text)
        if m:
            return m.group(1)

    return None


def _extract_action_target(plan: Optional[Dict[str, Any]], target_service: Optional[str] = None) -> Optional[str]:
    if target_service:
        return target_service
    if not plan:
        return None

    target_changes = plan.get("target_changes") or {}
    if isinstance(target_changes, dict):
        value = _first_non_empty(
            target_changes.get("target"),
            target_changes.get("service"),
            target_changes.get("deployment"),
            target_changes.get("resource"),
        )
        if value:
            return value

    return _first_non_empty(
        plan.get("target"),
        plan.get("service"),
        plan.get("deployment"),
        plan.get("target_service"),
        plan.get("target_deployment"),
    )


def normalize_action(plan: Optional[Dict[str, Any]], target_service: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Convert heterogeneous rule-based, static LLM, and EV-AIM plans into one
    canonical representation used by feedback and retrieval.

    Returns:
        {
            "action_type": one of CANONICAL_ACTION_TYPES,
            "target": service/deployment/resource if known,
            "value": replica/resource/image/config value if known,
        }
    """
    raw_action = classify_plan_action(plan)

    alias_to_canonical = {
        "noop": "none",
        "no_op": "none",
        "monitor": "none",
        "observe": "none",
        "observe_only": "none",
        "rollout_restart": "restart",
        "delete_pod": "restart",
        "recreate_pod": "restart",
        "rollout_undo": "rollback",
        "restore_image": "rollback",
        "restore_config": "config_fix",
        "resume": "resume_rollout",
        "rollout_resume": "resume_rollout",
        "scale_dependency": "restore_dependency",
        "scale_to_original": "restore_dependency",
        "scale_to_original_replicas": "restore_dependency",
        "restore_replicas": "restore_dependency",
        "stop_load": "traffic_control",
        "stop_traffic": "traffic_control",
        "rate_limit": "traffic_control",
    }

    action_type = alias_to_canonical.get(raw_action, raw_action)
    if action_type not in CANONICAL_ACTION_TYPES:
        text = _safe_json_text(plan)
        action_type = "unknown"
        for canonical, aliases in ACTION_ALIASES.items():
            for alias in aliases:
                if alias in text:
                    action_type = "none" if canonical == "none" else canonical
                    break
            if action_type != "unknown":
                break

    return {
        "action_type": action_type,
        "target": _extract_action_target(plan, target_service),
        "value": _extract_action_value(plan, action_type),
    }


def compute_plan_success(plan: Optional[Dict[str, Any]], fault_type: str, execution_required: bool = True) -> Dict[str, Any]:
    """
    Override the earlier implementation so PS is based on canonical actions.
    This makes rule-based, static LLM, and EV-AIM plans comparable.
    """
    normalized = normalize_action(plan)
    action = normalized["action_type"] or "unknown"
    expected = expected_actions_for_fault(fault_type)

    expected = {"none" if x in {"noop", "no_op"} else x for x in expected}

    if action == "none":
        score = 1.0 if not execution_required else 0.0
    elif not expected:
        score = 0.5 if action != "unknown" else 0.0
    else:
        score = 1.0 if action in expected else 0.0

    return {
        "PS": round(score, 6),
        "plan_action": action,
        "expected_actions": sorted(expected),
        "normalized_action": normalized,
    }


def _raw_for_fault_health(health: Dict[str, Any]) -> Dict[str, Any]:
    raw = health.get("raw", {}) or {}
    return raw.get("target_raw", raw)


def _component_delta(before_health: Dict[str, Any], after_health: Dict[str, Any], component: str) -> float:
    before = safe_float((before_health.get("components") or {}).get(component))
    after = safe_float((after_health.get("components") or {}).get(component))
    if before is None or after is None:
        return 0.0
    return clamp(after - before)


def compute_fault_recovery(
    *,
    fault_type: str,
    before_health: Dict[str, Any],
    after_health: Dict[str, Any],
    infra_comparison: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Fault Recovery Quality (FRQ): primary symptom recovery for the injected fault.

    FRQ is intentionally separate from generic SHS. SHS answers: is the system
    healthy overall? FRQ answers: did the mitigation fix the fault's dominant
    symptom?
    """
    ft = (fault_type or "").lower()
    before_raw = _raw_for_fault_health(before_health)
    after_raw = _raw_for_fault_health(after_health)
    infra_comparison = infra_comparison or {}

    before_cpu = safe_float(before_raw.get("cpu"))
    after_cpu = safe_float(after_raw.get("cpu"))

    before_mem = safe_float(before_raw.get("memory"))
    after_mem = safe_float(after_raw.get("memory"))

    before_disk_io = safe_float(before_raw.get("disk_io"))
    after_disk_io = safe_float(after_raw.get("disk_io"))

    before_disk_usage = safe_float(before_raw.get("fs_usage_to_limit_ratio"))
    after_disk_usage = safe_float(after_raw.get("fs_usage_to_limit_ratio"))

    if "cpu_hog" in ft or "cpu_pressure" in ft or "cpu_throttle" in ft:
        before_cpu = (
            safe_float(before_raw.get("cpu_throttle_ratio"))
            if safe_float(before_raw.get("cpu_throttle_ratio")) is not None
            else safe_float(before_raw.get("cpu"))
        )

        after_cpu = (
            safe_float(after_raw.get("cpu_throttle_ratio"))
            if safe_float(after_raw.get("cpu_throttle_ratio")) is not None
            else safe_float(after_raw.get("cpu"))
        )

    if "mem_stress" in ft or "memory_pressure" in ft:
        before_mem = (
            safe_float(before_raw.get("memory_usage_to_limit_ratio"))
            if safe_float(before_raw.get("memory_usage_to_limit_ratio")) is not None
            else safe_float(before_raw.get("memory"))
        )

        after_mem = (
            safe_float(after_raw.get("memory_usage_to_limit_ratio"))
            if safe_float(after_raw.get("memory_usage_to_limit_ratio")) is not None
            else safe_float(after_raw.get("memory"))
        )
    components = {
        "latency_improvement": relative_decrease_improvement(before_raw.get("latency_p95_ms"), after_raw.get("latency_p95_ms")),
        "error_improvement": relative_decrease_improvement(before_raw.get("error_rate"), after_raw.get("error_rate")),
        "availability_improvement": relative_increase_improvement(before_raw.get("availability"), after_raw.get("availability")),
        "cpu_improvement": relative_decrease_improvement(before_cpu, after_cpu),
        "memory_improvement": relative_decrease_improvement(before_mem, after_mem),
        "pod_ready_improvement": _component_delta(before_health, after_health, "pod_ready_health"),
        "restart_improvement": max(
            relative_decrease_improvement(before_raw.get("pod_not_ready_count"), after_raw.get("pod_not_ready_count"), min_before=0.0),
            relative_decrease_improvement(before_raw.get("container_waiting_count"), after_raw.get("container_waiting_count"), min_before=0.0),
        ),
        "replicas_improvement": relative_decrease_improvement(
            before_raw.get("replicas_unavailable_count"),
            after_raw.get("replicas_unavailable_count"),
            min_before=0.0,
        ),
        "disk_io_improvement": relative_decrease_improvement(before_disk_io, after_disk_io),
        "disk_usage_improvement": relative_decrease_improvement(before_disk_usage, after_disk_usage),
    }

    dependency_restore = 0.0

    before_ready = safe_float(before_raw.get("replicas_ready"))
    after_ready = safe_float(after_raw.get("replicas_ready"))

    before_available = safe_float(before_raw.get("replicas_available"))
    after_available = safe_float(after_raw.get("replicas_available"))

    if "dependency_failure" in ft:
        # 1. Prefer direct deployment replica metrics if present
        if before_ready == 0 and after_ready is not None and after_ready > 0:
            dependency_restore = 1.0
        elif before_available == 0 and after_available is not None and after_available > 0:
            dependency_restore = 1.0

        # 2. Fallback to infra comparison if replica metrics are missing
        elif infra_comparison.get("replica_restore_occurred"):
            dependency_restore = 1.0
        elif infra_comparison.get("scale_out_occurred"):
            dependency_restore = 1.0
        elif (
            safe_float(infra_comparison.get("namespace_running_pods_delta")) is not None
            and safe_float(infra_comparison.get("namespace_running_pods_delta")) > 0
        ):
            dependency_restore = 1.0

        # 3. Fallback to health component improvement
        elif components.get("pod_ready_improvement", 0.0) > 0:
            dependency_restore = components["pod_ready_improvement"]
        elif components.get("replicas_improvement", 0.0) > 0:
            dependency_restore = components["replicas_improvement"]

    components["dependency_restore_improvement"] = dependency_restore

    if "mem_stress" in ft or "memory_pressure" in ft:
        weights = {"memory_improvement": 1.00}
        primary = ["memory"]

    elif "cpu_hog" in ft or "cpu_pressure" in ft or "cpu_throttle" in ft:
        weights = {"cpu_improvement": 1.00}
        primary = ["cpu"]
    elif "pod_kill" in ft or "pod_crash" in ft:
        weights = {
            "pod_ready_improvement": 0.50,
            "replicas_improvement": 0.50,
        }
        primary = ["pod_ready", "replicas"]
    elif "net_delay" in ft or "network_delay" in ft or "network_latency" in ft:
        weights = {"latency_improvement": 1.00}
        primary = ["latency"]
    elif "net_loss" in ft or "packet_loss" in ft:
        weights = {"error_improvement": 0.30, "latency_improvement": 0.70}
        primary = ["error", "latency"]
    elif "dependency_failure" in ft:
        weights = {
            "dependency_restore_improvement": 1.00
        }
        primary = ["dependency_restore"]
    elif "disk_stress" in ft or "disk_pressure" in ft or "disk_io" in ft:
        weights = {
            "disk_io_improvement": 0.70,
            "latency_improvement": 0.20,
            "disk_usage_improvement": 0.10,
        }
        primary = ["disk_io", "latency"]
    else:
        weights = {
            "latency_improvement": 0.25,
            "error_improvement": 0.25,
            "availability_improvement": 0.20,
            "cpu_improvement": 0.15,
            "memory_improvement": 0.15,
        }
        primary = ["latency", "error", "availability", "cpu", "memory"]

    frq = clamp(sum(weights[k] * components.get(k, 0.0) for k in weights))

    improved_metrics = []
    degraded_metrics = []
    metric_pairs = {
        "latency": (before_raw.get("latency_p95_ms"), after_raw.get("latency_p95_ms"), "decrease"),
        "error": (before_raw.get("error_rate"), after_raw.get("error_rate"), "decrease"),
        "availability": (before_raw.get("availability"), after_raw.get("availability"), "increase"),
        "cpu": (before_raw.get("cpu"), after_raw.get("cpu"), "decrease"),
        "memory": (before_raw.get("memory"), after_raw.get("memory"), "decrease"),
        "pod_ready": (
            (before_health.get("components") or {}).get("pod_ready_health"),
            (after_health.get("components") or {}).get("pod_ready_health"),
            "increase",
        ),
        "disk_io": (before_raw.get("disk_io"), after_raw.get("disk_io"), "decrease"),
        "fs_read": (before_raw.get("fs_read_bytes_per_sec"), after_raw.get("fs_read_bytes_per_sec"), "decrease"),
        "fs_write": (before_raw.get("fs_write_bytes_per_sec"), after_raw.get("fs_write_bytes_per_sec"), "decrease"),
    }

    for name, (before, after, direction) in metric_pairs.items():
        before_v = safe_float(before)
        after_v = safe_float(after)
        if before_v is None or after_v is None:
            continue
        diff = after_v - before_v
        if direction == "decrease":
            diff = -diff
        if diff > 1e-6:
            improved_metrics.append(name)
        elif diff < -1e-6:
            degraded_metrics.append(name)

    return {
        "FRQ": round(frq, 6),
        "fault_recovery_components": {k: round(v, 6) for k, v in components.items()},
        "fault_recovery_weights": weights,
        "primary_metrics": primary,
        "improved_metrics": improved_metrics,
        "degraded_metrics": degraded_metrics,
        "primary_metric_fixed": frq >= 0.25,
    }


BASE_ACTION_COST = {
    "restart": 0.10,
    "scale_out": 0.20,
    "scale_up_cpu": 0.15,
    "scale_up_memory": 0.15,
    "rollback": 0.10,
    "config_fix": 0.10,
    "traffic_control": 0.10,
    "restore_dependency": 0.05,
    "resume_rollout": 0.05,
    "none": 0.00,
    "unknown": 0.20,
}

FAULT_ACTION_COST = {
    "mem_stress": {"scale_up_memory": 0.05, "restart": 0.15, "scale_out": 0.25, "scale_up_cpu": 0.20},
    "memory_pressure": {"scale_up_memory": 0.05, "restart": 0.15, "scale_out": 0.25, "scale_up_cpu": 0.20},
    "cpu_hog": {"scale_up_cpu": 0.05, "scale_out": 0.10, "restart": 0.15, "scale_up_memory": 0.20},
    "cpu_pressure": {"scale_up_cpu": 0.05, "scale_out": 0.10, "restart": 0.15, "scale_up_memory": 0.20},
    "pod_kill": {"restart": 0.05, "none": 0.05, "scale_out": 0.20},
    "pod_crash": {"restart": 0.05, "none": 0.05, "scale_out": 0.20},
    "net_delay": {"traffic_control": 0.05, "restart": 0.10, "rollback": 0.10, "scale_out": 0.40, "scale_up_cpu": 0.30, "scale_up_memory": 0.30},
    "network_delay": {"traffic_control": 0.05, "restart": 0.10, "rollback": 0.10, "scale_out": 0.40, "scale_up_cpu": 0.30, "scale_up_memory": 0.30},
    "net_loss": {"traffic_control": 0.05, "restart": 0.10, "rollback": 0.10, "scale_out": 0.40, "scale_up_cpu": 0.30, "scale_up_memory": 0.30},
    "packet_loss": {"traffic_control": 0.05, "restart": 0.10, "rollback": 0.10, "scale_out": 0.40, "scale_up_cpu": 0.30, "scale_up_memory": 0.30},
    "dependency_failure": {"restore_dependency": 0.05, "restart": 0.05, "rollback": 0.05, "scale_out": 0.10, "scale_up_cpu": 0.25, "scale_up_memory": 0.25},
    "disk_stress": {
        "scale_out": 0.10,
        "restart": 0.10,
        "none": 0.05,
        "scale_up_cpu": 0.35,
        "scale_up_memory": 0.35,
        "traffic_control": 0.25,
    },
}


def fault_aware_resource_penalty(
    *,
    fault_type: str,
    action_type: str,
    raw_resource_cost: float,
) -> float:
    """
    Penalize resource growth according to whether the action is appropriate for
    the fault family. Example: memory increase is cheap for mem_stress but costly
    for net_delay/net_loss.
    """
    ft = (fault_type or "").lower()
    action_type = action_type or "unknown"

    base = BASE_ACTION_COST.get(action_type, BASE_ACTION_COST["unknown"])
    for key, table in FAULT_ACTION_COST.items():
        if key in ft:
            base = table.get(action_type, base)
            break

    raw_resource_cost = clamp(raw_resource_cost)

    # Keep measured resource growth as a secondary factor, but do not let it
    # dominate action appropriateness.
    return round(clamp(0.70 * base + 0.30 * raw_resource_cost), 6)


def build_symptom_snapshot(health: Dict[str, Any]) -> Dict[str, Optional[float]]:
    raw = _raw_for_fault_health(health)
    return {
        "latency_p95_ms": safe_float(raw.get("latency_p95_ms")),
        "error_rate": safe_float(raw.get("error_rate")),
        "availability": safe_float(raw.get("availability")),
        "cpu": safe_float(raw.get("cpu")),
        "memory": safe_float(raw.get("memory")),
        "pod_ready_ratio": safe_float(raw.get("pod_ready_ratio")),
        "pod_not_ready_count": safe_float(raw.get("pod_not_ready_count")),
        "container_waiting_count": safe_float(raw.get("container_waiting_count")),
        "replicas_unavailable_count": safe_float(raw.get("replicas_unavailable_count")),
        "disk_io": safe_float(raw.get("disk_io")),
        "fs_read_bytes_per_sec": safe_float(raw.get("fs_read_bytes_per_sec")),
        "fs_write_bytes_per_sec": safe_float(raw.get("fs_write_bytes_per_sec")),
        "fs_usage_to_limit_ratio": safe_float(raw.get("fs_usage_to_limit_ratio")),
    }

def compute_feedback(
    *,
    metrics_before: Dict[str, Any],
    metrics_after: Optional[Dict[str, Any]],
    infra_state_before: Optional[Dict[str, Any]],
    infra_state_after: Optional[Dict[str, Any]],
    infra_comparison: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    app: Optional[str] = None,
    fault_type: str,
    target_service: Optional[str] = None,
    execution_required: bool,
    execution_status: str,
    execution_error: Optional[str],
    rollout_result: Optional[Dict[str, Any]],
    ansible_log: str = "",
    playbook_retries: int = 0,
    slo_thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Fault-aware feedback for EV-AIM.

    Keeps old output fields, but adds:
      - FRQ: fault-specific recovery quality
      - normalized_action: canonical action taxonomy for retrieval
      - symptoms_before / symptoms_after
      - improved_metrics / degraded_metrics
      - primary_metric_fixed
      - fault-aware resource_penalty

    Reward:
        0.55 * FRQ
      + 0.25 * RQ
      + 0.20 * ES
      - resource_penalty
      - execution/rollout/regression penalties
    """

    # -----------------------------
    # 1. Select target/system scope
    # -----------------------------
    mode = feedback_mode_for_fault(fault_type)

    before_target = select_target_metrics(metrics_before, target_service)
    after_target = select_target_metrics(metrics_after, target_service) if metrics_after else None

    slo_thresholds = get_app_slo_thresholds(app, slo_thresholds)

    before_target_health = compute_system_health_score(
                                                            before_target or {},
                                                            infra_state_before,
                                                            slo_thresholds,
                                                            fault_type,
                                                            phase="before",
                                                        )

    if metrics_after is None:
        final_before_health = before_target_health
        final_after_health = {
            "SHS": None,
            "components": {},
            "raw": {},
            "weights": before_target_health.get("weights", {}),
        }
        after_target_health = final_after_health
        before_system_health = {}
        after_system_health = {}
        delta_shs = None
        relative = {
            "MU": 0.0,
            "RQ": 0.0,
            "relative_improvement_components": {},
            "relative_improvement_weights": {},
        }
    else:
        after_target_health = compute_system_health_score(
                                                                                                after_target or {},
                                                                                                infra_state_after,
                                                                                                slo_thresholds,
                                                                                                fault_type,
                                                                                                phase="after",
                                                                                            )

        if mode == "target_only":
            final_before_health = before_target_health
            final_after_health = after_target_health
            before_system_health = {}
            after_system_health = {}
        else:
            before_system = aggregate_observed_services(
                                                        metrics_before or {},
                                                        phase="before",
                                                    )
            after_system = aggregate_observed_services(
                                                    metrics_after or {},
                                                    phase="after",
                                                )

            before_system_health = compute_system_health_score(
                                                before_system,
                                                None,
                                                slo_thresholds,
                                                fault_type,
                                                phase="before",
                                            )
            after_system_health = compute_system_health_score(
                                                            after_system,
                                                            None,
                                                            slo_thresholds,
                                                            fault_type,
                                                            phase="after",
                                                        )

            final_before_health = combine_health_scores(
                before_target_health,
                before_system_health,
                target_weight=0.4,
                system_weight=0.6,
            )
            final_after_health = combine_health_scores(
                after_target_health,
                after_system_health,
                target_weight=0.4,
                system_weight=0.6,
            )

        delta_shs = round(float(final_after_health["SHS"]) - float(final_before_health["SHS"]), 6)
        relative = compute_relative_recovery(final_before_health, final_after_health, fault_type=fault_type)

    # -----------------------------
    # 2. Fallback RQ from SHS delta
    # -----------------------------
    if relative.get("RQ", 0.0) == 0.0 and delta_shs is not None and delta_shs > 0.0:
        relative["RQ"] = round(
            min(delta_shs / max(1.0 - float(final_before_health["SHS"]), 1e-6), 1.0),
            6,
        )
        relative["MU"] = relative["RQ"]
        relative["relative_improvement_components"] = {"target_health_improvement": relative["RQ"]}
        relative["relative_improvement_weights"] = {"target_health_improvement": 1.0}

    # -----------------------------
    # 3. Plan, execution, and action normalization
    # -----------------------------
    normalized_action = normalize_action(plan, target_service=target_service)
    ps = compute_plan_success(plan, fault_type, execution_required)
    ps["normalized_action"] = normalized_action
    ps["plan_action"] = normalized_action["action_type"]

    es = compute_execution_success(
        plan,
        execution_status,
        execution_error,
        rollout_result,
        ansible_log,
        infra_comparison,
        execution_required,
    )

    # -----------------------------
    # 4. Fault-aware recovery and cost
    # -----------------------------
    if metrics_after is None:
        frq_result = {
            "FRQ": 0.0,
            "fault_recovery_components": {},
            "fault_recovery_weights": {},
            "primary_metrics": [],
            "improved_metrics": [],
            "degraded_metrics": [],
            "primary_metric_fixed": False,
        }
    else:
        frq_result = compute_fault_recovery(
            fault_type=fault_type,
            before_health=final_before_health,
            after_health=final_after_health,
            infra_comparison=infra_comparison,
        )

    raw_cost = compute_resource_cost_target(before_target, after_target or {}, ps["plan_action"])
    resource_penalty = fault_aware_resource_penalty(
        fault_type=fault_type,
        action_type=normalized_action["action_type"] or "unknown",
        raw_resource_cost=raw_cost["resource_cost"],
    )

    execution_failed = 1 if execution_required and execution_status != "success" else 0
    rollout_failed = 1 if rollout_result and rollout_result.get("rollout_completed") is False else 0
    regression = 1 if delta_shs is not None and delta_shs < -0.02 else 0

    # -----------------------------
    # 5. Reward
    # -----------------------------

    degradation_penalty = 0.05 * len(frq_result["degraded_metrics"])
    frq_result["degradation_penalty"] = min(degradation_penalty, 0.15)

    if delta_shs is None:
        reward = -0.50 if execution_failed else 0.0
    else:
        reward = (
            0.8 * frq_result["FRQ"]
            + 0.20 * es["ES"]
            - resource_penalty
            - frq_result["degradation_penalty"]
        )

    reward = round(clamp(float(reward), -1.0, 1.0), 6)

    # -----------------------------
    # 6. Fault-specific success
    # -----------------------------
    fault_success, fault_success_reason = fault_specific_success(
    fault_type=fault_type,
    before_health=final_before_health,
    after_health=final_after_health,
    infra_state_before=infra_state_before,
    infra_state_after=infra_state_after,
    infra_comparison=infra_comparison,
    execution_failed=execution_failed,
    rollout_failed=rollout_failed,
)
    if frq_result["FRQ"] >= 0.50 and fault_success_reason == "no_fault_specific_success":
        fault_success_reason = "primary_metric_fixed"
    if fault_success_reason == "network_loss_recovered_to_slo":
        frq_result["FRQ"] = max(frq_result["FRQ"], 1.0)

    recovery_success = (
                execution_failed == 0
                and rollout_failed == 0
                and not regression
                and (
                    frq_result["FRQ"] >= 0.50
                    or fault_success
                )
            )

    reward += 0.10 if recovery_success else 0.0

    symptoms_before = build_symptom_snapshot(final_before_health)
    symptoms_after = build_symptom_snapshot(final_after_health) if metrics_after is not None else {}

    # -----------------------------
    # 7. Return old fields + retrieval-friendly additions
    # -----------------------------
    return {
        "fault_type": fault_type,
        "service": target_service,

        "SHS_before": final_before_health["SHS"],
        "SHS_after": final_after_health["SHS"],
        "delta_SHS": delta_shs,

        "FRQ": frq_result["FRQ"],
        "fault_recovery_score": frq_result["FRQ"],
        "fault_recovery_components": frq_result["fault_recovery_components"],
        "fault_recovery_weights": frq_result["fault_recovery_weights"],
        "primary_metrics": frq_result["primary_metrics"],
        "primary_metric_fixed": frq_result["primary_metric_fixed"],

        "RQ": relative["RQ"],
        "MU": relative.get("MU", relative["RQ"]),
        "relative_improvement_components": relative["relative_improvement_components"],
        "relative_improvement_weights": relative["relative_improvement_weights"],

        "symptoms_before": symptoms_before,
        "symptoms_after": symptoms_after,
        "improved_metrics": frq_result["improved_metrics"],
        "degradation_penalty": frq_result["degradation_penalty"],

        "recovery_success": recovery_success,
        "success": recovery_success,
        "fault_success_reason": fault_success_reason,
        "regression": bool(regression),

        "PS": ps["PS"],
        "plan_action": ps["plan_action"],
        "normalized_action": normalized_action,
        "expected_actions": ps["expected_actions"],

        "ES": es["ES"],
        "code_changed_system": es["code_changed_system"],
        "execution_failure_reason": es["execution_failure_reason"],
        "ansible_recap_counts": es["ansible_recap_counts"],

        # Keep old resource-cost field for compatibility, but make it fault-aware.
        "resource_cost": resource_penalty,
        "raw_resource_cost": raw_cost["resource_cost"],
        "replica_delta": raw_cost["replica_delta"],
        "namespace_cpu_increase_ratio": raw_cost["namespace_cpu_increase_ratio"],
        "namespace_memory_increase_ratio": raw_cost["namespace_memory_increase_ratio"],

        "reward": reward,

        "health_before_details": final_before_health,
        "health_after_details": final_after_health,

        "target_health_before_details": before_target_health,
        "target_health_after_details": after_target_health,
        "system_health_before_details": before_system_health,
        "system_health_after_details": after_system_health,
        "feedback_mode": mode,
    }
