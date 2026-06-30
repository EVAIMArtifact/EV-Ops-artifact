from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import os
import time


# Default namespace per app. sock-shop / online-boutique point at the
# INSTRUMENTED "2" copies: the legacy sock-shop / online-boutique namespaces
# emit no OTel spanmetrics, so RED/latency would be empty and SHS meaningless
# there. An explicit `namespace` in the fault/experiment still overrides this.
APP_TO_NAMESPACE = {
    "robot-shop": "robot-shop",
    "sock-shop": "sock-shop",
    "online-boutique": "online-boutique",
}

# Spanmetric metric-name prefix per app — set by each app's OTel collector. The
# RED queries are <prefix>_calls_total / <prefix>_latency_* (see promql.py), so
# the WRONG prefix makes every RED query return empty (and SHS default to the
# neutral 0.5). Keyed by app, since one Prometheus holds all apps' metrics.
APP_TO_SPANMETRIC_PREFIX = {
    "robot-shop": "robotshop_traces_spanmetrics",
    "sock-shop": "sockshop_traces_spanmetrics",
    "online-boutique": "onlineboutique_traces_spanmetrics",
}

# Fallback when the app is unknown. SPANMETRIC_PREFIX env still overrides.
DEFAULT_SPANMETRIC_PREFIX = os.getenv("SPANMETRIC_PREFIX", "robotshop_traces_spanmetrics")


def spanmetric_prefix_for(app: Optional[str]) -> str:
    """Resolve the spanmetric prefix for an app (e.g. "sock-shop" ->
    "sockshop_traces_spanmetrics"). Falls back to the env/default prefix."""
    return APP_TO_SPANMETRIC_PREFIX.get(app or "", DEFAULT_SPANMETRIC_PREFIX)


@dataclass(frozen=True)
class MetricTarget:
    """Kubernetes/Prometheus target observed by EV-AIM."""

    app: str
    namespace: str
    service: str
    span_service: Optional[str] = None
    deployment: Optional[str] = None
    pod_prefix_override: Optional[str] = None
    extra_labels: Dict[str, str] = field(default_factory=dict)

    @property
    def pod_prefix(self) -> str:
        return self.pod_prefix_override or self.deployment or self.service

    @property
    def otel_service(self) -> str:
        return self.span_service or self.service

    @property
    def deployment_name(self) -> str:
        return self.deployment or self.service


@dataclass(frozen=True)
class FaultEvent:
    """Fault metadata used to align telemetry with injected faults."""

    fault_type: str
    app: str
    namespace: str
    service: str
    deployment: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    mode: str = "single"
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_fault_dict(
        cls,
        fault: Dict[str, Any],
        start_time: Optional[float] = None,
    ) -> "FaultEvent":
        app = fault.get("app", "robot-shop")
        namespace = fault.get("namespace") or APP_TO_NAMESPACE.get(app, app)
        service = fault.get("service") or fault.get("deployment")
        if not service:
            raise ValueError("Fault must include service or deployment for metric collection")

        return cls(
            fault_type=fault.get("type") or fault.get("fault_type", "unknown"),
            app=app,
            namespace=namespace,
            service=service,
            deployment=fault.get("deployment") or service,
            start_time=start_time or fault.get("start_time") or time.time(),
            end_time=fault.get("end_time"),
            mode=fault.get("mode", "single"),
            details={
                k: v
                for k, v in fault.items()
                if k not in {
                    "type",
                    "fault_type",
                    "app",
                    "namespace",
                    "service",
                    "deployment",
                    "start_time",
                    "end_time",
                    "mode",
                }
            },
        )


@dataclass(frozen=True)
class CollectionWindow:
    """Prometheus extraction window supplied by run_experiment.py/test_gpt.json."""

    lookback_seconds: int
    step_seconds: int
    rate_interval: str = "1m"

    def __post_init__(self):
        if self.lookback_seconds <= 0:
            raise ValueError("lookback_seconds must be > 0")
        if self.step_seconds <= 0:
            raise ValueError("step_seconds must be > 0")
        if self.step_seconds > self.lookback_seconds:
            raise ValueError("step_seconds cannot be larger than lookback_seconds")

    @property
    def prom_window(self) -> str:
        if self.lookback_seconds % 60 == 0:
            return f"{self.lookback_seconds // 60}m"
        return f"{self.lookback_seconds}s"

    @property
    def prom_step(self) -> str:
        return f"{self.step_seconds}s"

    @property
    def bucket_count(self) -> int:
        return max(1, int(self.lookback_seconds / self.step_seconds))


MANUAL_TEST_COLLECTION_WINDOW = CollectionWindow(
    lookback_seconds=300,
    step_seconds=60,
    rate_interval="1m",
)

APPLICATION_METRIC_GROUPS = ["application_api"]

SYSTEM_METRIC_GROUPS = [
    "container_resources",
    "pod_health",
    "deployment_health",
]

INFRASTRUCTURE_METRIC_GROUPS = [
    "namespace_context",
    "node_context",
    "hpa_context",
]

ALL_METRIC_GROUPS = (
    APPLICATION_METRIC_GROUPS
    + SYSTEM_METRIC_GROUPS
    + INFRASTRUCTURE_METRIC_GROUPS
)
