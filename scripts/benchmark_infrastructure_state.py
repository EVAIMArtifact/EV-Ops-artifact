#!/usr/bin/env python3
"""
Benchmark script for infrastructure state capture performance.

This script measures the actual performance improvement of parallel query execution
when connected to a live Prometheus instance.

Usage:
    python scripts/benchmark_infrastructure_state.py [--service SERVICE] [--namespace NAMESPACE]

Example:
    python scripts/benchmark_infrastructure_state.py --service cart --namespace robot-shop
"""

import argparse
import sys
import time
from typing import List

from prometheus_api_client import PrometheusConnect

from src.monitoring.infrastructure_state import capture_infrastructure_state


def benchmark_capture(
    prom: PrometheusConnect,
    service: str,
    namespace: str,
    parallel: bool,
    num_runs: int = 5,
) -> List[float]:
    """
    Benchmark infrastructure state capture.

    Args:
        prom: PrometheusConnect client
        service: Service name
        namespace: Kubernetes namespace
        parallel: Use parallel or sequential execution
        num_runs: Number of benchmark runs

    Returns:
        List of execution times in seconds
    """
    times = []

    # Warm-up run (not counted)
    try:
        _ = capture_infrastructure_state(prom, service, namespace, parallel=parallel)
    except Exception as e:
        print(f"Warm-up run failed: {e}")
        print("Note: Make sure Prometheus is running and the service exists")
        return []

    # Benchmark runs
    for i in range(num_runs):
        start = time.time()
        try:
            state = capture_infrastructure_state(prom, service, namespace, parallel=parallel)
            elapsed = time.time() - start
            times.append(elapsed)
            print(f"  Run {i+1}/{num_runs}: {elapsed:.3f}s ({state['pod_count_ready']} pods)")
        except Exception as e:
            print(f"  Run {i+1}/{num_runs} failed: {e}")
            break

    return times


def print_statistics(times: List[float], label: str):
    """Print timing statistics."""
    if not times:
        print(f"{label}: No successful runs")
        return

    avg = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\n{label} Statistics:")
    print(f"  Average: {avg:.3f}s")
    print(f"  Min:     {min_time:.3f}s")
    print(f"  Max:     {max_time:.3f}s")
    print(f"  Runs:    {len(times)}/{len(times)}")

    return avg


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark infrastructure state capture performance"
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9090",
        help="Prometheus URL (default: http://localhost:9090)",
    )
    parser.add_argument(
        "--service",
        default="cart",
        help="Service name to query (default: cart)",
    )
    parser.add_argument(
        "--namespace",
        default="robot-shop",
        help="Kubernetes namespace (default: robot-shop)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of benchmark runs (default: 5)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=8,
        help="Maximum parallel workers (default: 8)",
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Infrastructure State Capture Benchmark")
    print("=" * 80)
    print(f"\nConfiguration:")
    print(f"  Prometheus URL: {args.prometheus_url}")
    print(f"  Service:        {args.service}")
    print(f"  Namespace:      {args.namespace}")
    print(f"  Runs:           {args.runs}")
    print(f"  Max workers:    {args.max_workers}")

    # Connect to Prometheus
    try:
        prom = PrometheusConnect(url=args.prometheus_url, disable_ssl=True)
        print(f"\n✓ Connected to Prometheus")
    except Exception as e:
        print(f"\n✗ Failed to connect to Prometheus: {e}")
        sys.exit(1)

    # Benchmark sequential execution
    print(f"\n{'=' * 80}")
    print("Sequential Execution Benchmark")
    print("=" * 80)
    sequential_times = benchmark_capture(
        prom, args.service, args.namespace, parallel=False, num_runs=args.runs
    )

    if not sequential_times:
        print("\n✗ Sequential benchmark failed")
        sys.exit(1)

    sequential_avg = print_statistics(sequential_times, "Sequential")

    # Benchmark parallel execution
    print(f"\n{'=' * 80}")
    print("Parallel Execution Benchmark")
    print("=" * 80)
    parallel_times = benchmark_capture(
        prom, args.service, args.namespace, parallel=True, num_runs=args.runs
    )

    if not parallel_times:
        print("\n✗ Parallel benchmark failed")
        sys.exit(1)

    parallel_avg = print_statistics(parallel_times, "Parallel")

    # Performance comparison
    print(f"\n{'=' * 80}")
    print("Performance Comparison")
    print("=" * 80)

    speedup = sequential_avg / parallel_avg
    reduction_pct = (1 - parallel_avg / sequential_avg) * 100
    time_saved = sequential_avg - parallel_avg

    print(f"\nSequential average: {sequential_avg:.3f}s")
    print(f"Parallel average:   {parallel_avg:.3f}s")
    print(f"\nSpeedup:           {speedup:.2f}x")
    print(f"Reduction:         {reduction_pct:.1f}%")
    print(f"Time saved:        {time_saved:.3f}s per call")

    # Per-experiment impact (2 calls)
    print(f"\nPer-experiment impact (2 calls):")
    print(f"  Before: {2 * sequential_avg:.3f}s")
    print(f"  After:  {2 * parallel_avg:.3f}s")
    print(f"  Saved:  {2 * time_saved:.3f}s")

    # Batch impact
    for batch_size in [10, 30, 100]:
        total_saved = batch_size * 2 * time_saved
        print(f"\n{batch_size} experiments:")
        print(f"  Before: {batch_size * 2 * sequential_avg:.1f}s ({batch_size * 2 * sequential_avg / 60:.1f} min)")
        print(f"  After:  {batch_size * 2 * parallel_avg:.1f}s ({batch_size * 2 * parallel_avg / 60:.1f} min)")
        print(f"  Saved:  {total_saved:.1f}s ({total_saved / 60:.1f} min)")

    # Success indicator
    if reduction_pct >= 70.0:
        print(f"\n{'=' * 80}")
        print("✓ SUCCESS: Achieved target reduction (≥70%)")
        print("=" * 80)
    else:
        print(f"\n{'=' * 80}")
        print(f"✗ WARNING: Did not achieve target reduction (got {reduction_pct:.1f}%, target ≥70%)")
        print("=" * 80)


if __name__ == "__main__":
    main()
