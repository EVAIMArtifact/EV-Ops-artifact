"""Namespace-aware EV-AIM monitoring package."""

from .collector import collect_fault_observation, collect_experiment_observation
from .config import (
    MetricTarget,
    FaultEvent,
    CollectionWindow,
    APPLICATION_METRIC_GROUPS,
    SYSTEM_METRIC_GROUPS,
    INFRASTRUCTURE_METRIC_GROUPS,
    ALL_METRIC_GROUPS,
)

__all__ = [
    "MetricTarget",
    "FaultEvent",
    "CollectionWindow",
    "APPLICATION_METRIC_GROUPS",
    "SYSTEM_METRIC_GROUPS",
    "INFRASTRUCTURE_METRIC_GROUPS",
    "ALL_METRIC_GROUPS",
    "collect_fault_observation",
    "collect_experiment_observation",
]
