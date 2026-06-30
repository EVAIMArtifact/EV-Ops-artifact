"""Fault injection module for EV-AIM multi-MSA experiments.

Supports infrastructure faults through kubectl and Chaos Mesh, plus workload
faults through Locust.

Fault catalog (fault["type"]):
    Ported from rca-service (Chaos Mesh + kubectl):
        cpu_hog, mem_stress, net_delay, net_loss, pod_kill   (Chaos Mesh)
        disk_stress, dependency_failure, config_error, cpu_throttle (kubectl)
    EV-native:
        bad_image, stuck_deployment, db_overload, load_spike

Typical usage:
    from src.fault_injection import inject_fault, recover_fault

    fault = {
        "type": "cpu_hog",
        "app": "robot-shop",
        "namespace": "robot-shop",
        "service": "cart",
    }

    result = inject_fault(fault)
    recover_fault(fault, result)
"""

from src.fault_injection.fault_inject import (
    inject_fault,
    recover_fault,
    start_load,
    stop_load,
    is_category_a_fault,
    is_category_b_fault,
    reset_load_stats,
    get_pods,
    get_first_pod,
    scale_deployment,
    restart_deployment,
    rollback_deployment,
    inject_load_spike,
    inject_bad_image,
    inject_stuck_deployment,
    inject_db_overload,
)
from src.fault_injection.chaos_faults import (
    inject_cpu_hog,
    inject_mem_stress,
    inject_net_delay,
    inject_net_loss,
    inject_pod_kill,
    inject_disk_stress,
    inject_dependency_failure,
    inject_config_error,
    inject_cpu_throttle,
    recover_chaos_fault,
)

__all__ = [
    "inject_fault",
    "recover_fault",
    "start_load",
    "stop_load",
    "reset_load_stats",
    "get_pods",
    "get_first_pod",
    "scale_deployment",
    "restart_deployment",
    "rollback_deployment",
    # EV-native faults
    "inject_load_spike",
    "inject_bad_image",
    "inject_stuck_deployment",
    "inject_db_overload",
    # Ported from rca-service
    "inject_cpu_hog",
    "inject_mem_stress",
    "inject_net_delay",
    "inject_net_loss",
    "inject_pod_kill",
    "inject_disk_stress",
    "inject_dependency_failure",
    "inject_config_error",
    "inject_cpu_throttle",
    "recover_chaos_fault",
]
