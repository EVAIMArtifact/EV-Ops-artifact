from src.experiment.run_experiment import ensure_average_cpu_metric


def test_ensure_average_cpu_metric_adds_default():
    metrics = {}
    ensure_average_cpu_metric(metrics)
    assert "average_cpu_usage" in metrics
    assert metrics["average_cpu_usage"] == 0.4


def test_ensure_average_cpu_metric_preserves_existing():
    metrics = {"average_cpu_usage": 0.123}
    ensure_average_cpu_metric(metrics)
    assert metrics["average_cpu_usage"] == 0.123
