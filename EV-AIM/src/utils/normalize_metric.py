from typing import Dict, Any

def normalize_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Prometheus-style metric lists into scalar values
    without removing any metrics or adding interpretation.
    """
    normalized = {}

    for key, value in metrics.items():
        # Preserve non-metric fields as-is
        if not isinstance(value, list):
            normalized[key] = value
            continue

        # Prometheus metric list → scalar
        if value and isinstance(value[0], dict) and "value" in value[0]:
            normalized[key] = float(value[0]["value"])
        else:
            normalized[key] = value

    return normalized