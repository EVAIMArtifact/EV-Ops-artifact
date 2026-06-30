from typing import List, Dict, Any


def load_canonical_experiences() -> List[Dict[str, Any]]:
    """
    Hand-crafted fallback examples for in-context learning.
    These are safe, general mitigation templates.
    """
    return [
        {
            "incident": {
                "fault": "generic_resource_pressure",
                "metrics": {"cpu": "high", "latency": "increasing"},
            },
            "plan": {
                "intent": "Stabilize service under load",
                "strategy": "Perform a rolling restart and verify resource limits"
            },
            "outcome": {"EVS": 0.6, "MU": 0.6},  # EVS = MU with 0 retries
        },
        {
            "incident": {
                "fault": "generic_pod_instability",
                "metrics": {"restarts": "frequent"},
            },
            "plan": {
                "intent": "Restore pod stability",
                "strategy": "Trigger rollout restart and monitor pod health"
            },
            "outcome": {"EVS": 0.64, "MU": 0.7},  # EVS = MU - 0.3*(1/5) = 0.7 - 0.06 = 0.64 (1 retry)
        },
    ]
