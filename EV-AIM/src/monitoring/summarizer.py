from __future__ import annotations

from typing import Any, Dict, List, Optional
import math
import statistics


def _safe_float(value: Any) -> Optional[float]:
    try:
        x = float(value)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def _series_key(metric: Dict[str, Any]) -> str:
    # Keep keys specific enough to avoid collisions for status reason metrics.
    if "pod" in metric and "container" in metric and "reason" in metric:
        return f"{metric.get('pod')}/{metric.get('container')}/{metric.get('reason')}"
    if "pod" in metric and "container" in metric:
        return f"{metric.get('pod')}/{metric.get('container')}"

    # IMPORTANT: deployment/HPA keys must come before pod.
    # kube-state-metrics series can carry scrape-target pod labels; using pod first
    # incorrectly labels deployment metrics as kube-state-metrics pods.
    if "deployment" in metric:
        return str(metric["deployment"])
    if "horizontalpodautoscaler" in metric:
        return str(metric["horizontalpodautoscaler"])
    if "pod" in metric:
        return str(metric["pod"])
    if "node" in metric and "condition" in metric:
        return f"{metric.get('node')}/{metric.get('condition')}"
    if "node" in metric:
        return str(metric["node"])
    if "phase" in metric:
        return str(metric["phase"])
    if "reason" in metric:
        return str(metric["reason"])
    if "le" in metric:
        return str(metric["le"])
    return "scalar"


def extract_instant_vector(results: list) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for item in results or []:
        metric = item.get("metric", {})
        key = _series_key(metric)
        value = item.get("value", [None, None])[1]
        f = _safe_float(value)
        if f is not None:
            out[key] = f
    return out


def extract_scalar(results: list) -> Optional[float]:
    if not results:
        return None
    return _safe_float(results[0].get("value", [None, None])[1])


def extract_range_matrix(results: list) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {}
    for item in results or []:
        metric = item.get("metric", {})
        key = _series_key(metric)
        values: List[float] = []
        for _, raw_value in item.get("values", []):
            f = _safe_float(raw_value)
            if f is not None:
                values.append(f)
        out[key] = values
    return out


def stats(values: List[float]) -> Dict[str, Any]:
    values = [v for v in values if v is not None and not math.isnan(v)]
    if not values:
        return {
            "mean": None,
            "p50": None,
            "p95": None,
            "min": None,
            "max": None,
            "last": None,
            "count": 0,
            "sum": 0.0,
        }
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return {
        "mean": float(statistics.mean(values)),
        "p50": float(statistics.median(values)),
        "p95": float(ordered[p95_index]),
        "min": float(min(values)),
        "max": float(max(values)),
        "last": float(values[-1]),
        "count": len(values),
        "sum": float(sum(values)),
    }


def trend(values: List[float], epsilon_ratio: float = 0.05) -> str:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return "unknown"
    first = values[0]
    last = values[-1]
    denom = abs(first) if abs(first) > 1e-9 else 1.0
    change = (last - first) / denom
    if change > epsilon_ratio:
        return "increasing"
    if change < -epsilon_ratio:
        return "decreasing"
    return "stable"


def summarize_vector_series(matrix: Dict[str, List[float]]) -> Dict[str, Any]:
    by_entity = {
        key: {"series": series, "stats": stats(series), "trend": trend(series)}
        for key, series in matrix.items()
    }

    max_len = max((len(v) for v in matrix.values()), default=0)
    bucket_means: List[float] = []
    for index in range(max_len):
        bucket_values = [series[index] for series in matrix.values() if index < len(series)]
        if bucket_values:
            bucket_means.append(float(statistics.mean(bucket_values)))

    all_values = [value for series in matrix.values() for value in series]
    return {
        "series_aggregate_mean": bucket_means,
        "aggregate_stats": stats(all_values),
        "aggregate_trend": trend(bucket_means),
        "by_entity": by_entity,
    }


def summarize_instant_vector(vector: Dict[str, float]) -> Dict[str, Any]:
    values = list(vector.values())
    return {
        "values": vector,
        "aggregate_stats": stats(values),
    }
