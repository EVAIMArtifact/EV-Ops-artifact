# Prometheus Query Guide for AIM-EVM

This guide explains the key Prometheus queries used in the AIM-EVM fault mitigation system. These queries collect critical metrics that feed into the LLM-based decision-making pipeline for Kubernetes microservice remediation.

## Table of Contents
- [Overview](#overview)
- [Query Categories](#query-categories)
- [CPU Metrics](#cpu-metrics)
- [Memory Metrics](#memory-metrics)
- [Infrastructure State Metrics](#infrastructure-state-metrics)
- [Common Patterns](#common-patterns)
- [Troubleshooting](#troubleshooting)

---

## Overview

### Why Per-Pod Metrics?

AIM-EVM uses a **per-pod metric collection strategy** to ensure scale-invariant analysis:

```python
# ❌ BAD: Aggregated query (breaks during scaling)
sum(rate(container_cpu_usage_seconds_total[5m]))
# Returns: 0.8 cores (4 pods × 0.2 cores each)
# Problem: Value doubles if we scale from 2 to 4 pods

# ✅ GOOD: Per-pod query (scale-invariant)
sum by (pod) (rate(container_cpu_usage_seconds_total[5m]))
# Returns: [
#   {pod="cart-abc", value=0.2},
#   {pod="cart-def", value=0.2},
#   {pod="cart-ghi", value=0.2},
#   {pod="cart-jkl", value=0.2}
# ]
# Python then computes mean=0.2, which stays stable across scaling
```

**Key Principle**: Return per-pod values, then compute statistics in Python. This ensures metrics remain meaningful when remediation changes pod count.

---

## Query Categories

| Category | Purpose | Example Metrics |
|----------|---------|-----------------|
| **CPU Metrics** | Detect CPU pressure, throttling, resource exhaustion | CPU usage, throttle ratio, limits |
| **Memory Metrics** | Detect memory pressure, OOMKills | Working set, RSS, limits |
| **Infrastructure State** | Track pod counts, replica status, resource specs | Pod readiness, deployment replicas |
| **HTTP Metrics** | Application-level performance (OpenTelemetry) | Latency percentiles, error rates |

---

## CPU Metrics

### 1. Average CPU Usage Per Pod

**Purpose**: Measures how much CPU each pod actually uses over a time window.

**Query**:
```promql
avg_over_time(
  (
    sum by (pod) (
      rate(container_cpu_usage_seconds_total{
        namespace="robot-shop",
        pod=~"cart.*",
        container!="",
        container!="POD"
      }[1m])
    )
  )[5m:]
)
```

**Breakdown**:
- `container_cpu_usage_seconds_total`: Counter metric from cAdvisor tracking total CPU time in seconds
- `rate(...[1m])`: Converts counter to rate (CPU cores used per second over last 1 minute)
- `sum by (pod)`: Aggregates across all containers in each pod (excludes pause container)
- `avg_over_time(...[5m:])`: Averages the rate over 5-minute window to smooth out spikes

**Example Output**:
```json
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "0.250"]},
  {"metric": {"pod": "cart-7d8f9b6c4-def34"}, "value": [1640000000, "0.280"]},
  {"metric": {"pod": "cart-7d8f9b6c4-ghi56"}, "value": [1640000000, "0.265"]}
]
```

**Interpretation**:
- `0.250` = 250 millicores = 25% of one CPU core
- Values close to CPU limit suggest resource pressure
- Compare against `cpu_limit_per_pod` to compute utilization percentage

**Common Issues**:
- **Missing data**: Container hasn't been running long enough (needs >1m of data)
- **Unexpected spikes**: Short rate window (1m) may show transient spikes; increase window if needed
- **Zero values**: Pod might be idle or throttled to zero

---

### 2. CPU Throttle Ratio Per Pod

**Purpose**: Detects when pods hit CPU limits and get throttled by the kernel (critical fault signal).

**Query**:
```promql
avg_over_time(
  (
    sum by (pod) (
      rate(
        container_cpu_cfs_throttled_seconds_total{
          namespace="robot-shop",
          pod=~"cart.*",
          container!="",
          container!="POD"
        }[1m]
      )
    )
    /
    sum by (pod) (
      rate(
        container_cpu_cfs_periods_total{
          namespace="robot-shop",
          pod=~"cart.*",
          container!="",
          container!="POD"
        }[1m]
      )
    )
  )[5m:]
)
```

**Breakdown**:
- `container_cpu_cfs_throttled_seconds_total`: Counter tracking total seconds the container was throttled
- `container_cpu_cfs_periods_total`: Counter tracking total CFS scheduler periods (100ms intervals)
- **Ratio**: `throttled_rate / total_periods` gives percentage of time throttled
- `rate(...[1m])`: Must use `rate()` (not `increase()`) to get meaningful ratio

**Example Output**:
```json
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "0.42"]},
  {"metric": {"pod": "cart-7d8f9b6c4-def34"}, "value": [1640000000, "0.38"]}
]
```

**Interpretation**:
- `0.42` = 42% of CPU periods were throttled
- **0-10%**: Normal occasional throttling
- **10-30%**: Moderate throttling, may impact latency
- **30%+**: Severe throttling, definitely impacting performance
- **>50%**: Critical, urgent remediation needed

**Remediation Signals**:
| Throttle Ratio | Action |
|----------------|--------|
| 0-10% | Monitor |
| 10-30% | Consider scale-up (increase CPU limit) |
| 30-50% | Scale-up recommended |
| >50% | Urgent scale-up or scale-out |

**Common Issues**:
- **Division by zero**: Returns `NaN` if pod has no CFS periods recorded (filter these in Python)
- **Missing metric**: Some Kubernetes distributions don't expose throttle metrics
- **Misleading low values**: Throttled pod might be at CPU=0 if fully throttled (check CPU usage too)

---

### 3. CPU Limits and Requests Per Pod

**Purpose**: Shows the resource constraints configured in pod specs (used for capacity planning).

**CPU Limit Query**:
```promql
sum by (pod) (
  kube_pod_container_resource_limits{
    job="kube-state-metrics",
    namespace="robot-shop",
    pod=~"cart.*",
    resource="cpu"
  }
)
```

**CPU Request Query**:
```promql
sum by (pod) (
  kube_pod_container_resource_requests{
    job="kube-state-metrics",
    namespace="robot-shop",
    pod=~"cart.*",
    resource="cpu"
  }
)
```

**Example Output**:
```json
// CPU Limit
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "0.5"]}
]

// CPU Request
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "0.1"]}
]
```

**Interpretation**:
- `0.5` limit = 500 millicores = 50% of one CPU core (max the pod can use)
- `0.1` request = 100 millicores (scheduler reserves this much on node)
- Gap between request and limit allows bursting

**Usage Pattern**:
```python
# Compute CPU utilization percentage
cpu_usage = 0.45  # from CPU usage query
cpu_limit = 0.5   # from CPU limit query
utilization = (cpu_usage / cpu_limit) * 100  # 90%

if utilization > 80:
    print("Pod is near CPU limit, consider scale-up")
```

---

## Memory Metrics

### 1. Memory Working Set Per Pod

**Purpose**: Measures active memory usage (what Kubernetes uses for OOM decisions).

**Query**:
```promql
avg_over_time(
  (
    sum by (pod) (
      container_memory_working_set_bytes{
        job="kubelet",
        metrics_path="/metrics/cadvisor",
        namespace="robot-shop",
        pod=~"cart.*",
        container!="",
        container!="POD",
        image!=""
      }
    )
  )[5m:]
)
```

**Breakdown**:
- `container_memory_working_set_bytes`: Gauge metric (not counter) showing current memory usage
- **Working Set** = Anonymous memory + some page cache (what kernel considers "in use")
- This is the metric Kubernetes uses to decide when to OOMKill a pod
- `avg_over_time(...[5m:])`: Averages gauge values over time window

**Example Output**:
```json
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "134217728"]}
]
```

**Interpretation**:
- `134217728` bytes = 128 MiB
- Compare against memory limit: `usage / limit > 0.9` indicates pressure
- Rising trend over time may indicate memory leak

**Key Differences**:
| Metric | What It Measures | Use Case |
|--------|------------------|----------|
| **Working Set** | Active memory (anonymous + cache) | OOM detection, Kubernetes eviction decisions |
| **RSS** | Resident set size (physical memory) | True memory footprint (more conservative) |
| **Usage** | Working set + inactive file cache | Less accurate for OOM prediction |

**Common Issues**:
- **Metric name confusion**: Ensure using `container_memory_working_set_bytes`, not `container_memory_usage_bytes`
- **Page cache noise**: Working set includes some page cache, so value may fluctuate
- **Sudden drops**: Pod restart or cache eviction

---

### 2. Memory RSS Per Pod

**Purpose**: Measures resident physical memory (more conservative than working set).

**Query**:
```promql
avg_over_time(
  (
    sum by (pod) (
      container_memory_rss{
        job="kubelet",
        metrics_path="/metrics/cadvisor",
        namespace="robot-shop",
        pod=~"cart.*",
        container!="",
        container!="POD",
        image!=""
      }
    )
  )[5m:]
)
```

**Interpretation**:
- RSS = Resident Set Size (actual physical memory in use)
- Does NOT include page cache (more accurate for "true" memory usage)
- Use this when working set seems inflated by file cache

---

### 3. Memory Limits and Requests Per Pod

**Purpose**: Shows configured memory constraints (used for OOM risk assessment).

**Memory Limit Query**:
```promql
sum by (pod) (
  kube_pod_container_resource_limits{
    job="kube-state-metrics",
    namespace="robot-shop",
    pod=~"cart.*",
    resource="memory"
  }
)
```

**Example Output**:
```json
[
  {"metric": {"pod": "cart-7d8f9b6c4-abc12"}, "value": [1640000000, "536870912"]}
]
```

**Interpretation**:
- `536870912` bytes = 512 MiB (max memory before OOMKill)
- If working set approaches this value, pod is at risk of OOMKill

**OOMKill Risk Assessment**:
```python
memory_working_set = 480 * 1024 * 1024  # 480 MiB
memory_limit = 512 * 1024 * 1024         # 512 MiB
memory_pressure = (memory_working_set / memory_limit)  # 0.9375 = 93.75%

if memory_pressure > 0.9:
    print("CRITICAL: OOMKill imminent")
elif memory_pressure > 0.8:
    print("WARNING: High memory pressure")
```

---

## Infrastructure State Metrics

### 1. Pod Count (Ready)

**Purpose**: Count how many pods are passing readiness probes (healthy and serving traffic).

**Query**:
```promql
count(
  kube_pod_status_ready{
    namespace="robot-shop",
    pod=~"cart-.*",
    condition="true"
  }
)
```

**Example Output**:
```json
[
  {"metric": {}, "value": [1640000000, "3"]}
]
```

**Interpretation**:
- `3` = Three pods are ready and healthy
- Compare against `replica_spec_desired` to detect issues
- If `ready < desired`, some pods are unhealthy or starting

---

### 2. Deployment Replica Counts

**Purpose**: Track desired vs actual replica counts (detects scaling issues).

**Desired Replicas (from spec)**:
```promql
kube_deployment_spec_replicas{
  namespace="robot-shop",
  deployment="cart"
}
```

**Ready Replicas (passing readiness probe)**:
```promql
kube_deployment_status_replicas_ready{
  namespace="robot-shop",
  deployment="cart"
}
```

**Available Replicas (ready for minReadySeconds)**:
```promql
kube_deployment_status_replicas_available{
  namespace="robot-shop",
  deployment="cart"
}
```

**Updated Replicas (with new pod template)**:
```promql
kube_deployment_status_replicas_updated{
  namespace="robot-shop",
  deployment="cart"
}
```

**Example Output**:
```json
// During healthy state
{"desired": 3, "ready": 3, "available": 3, "updated": 3}

// During rolling update
{"desired": 3, "ready": 3, "available": 2, "updated": 2}

// During scale-out
{"desired": 5, "ready": 3, "available": 3, "updated": 3}
```

**Interpretation**:
| Scenario | desired | ready | available | updated | Meaning |
|----------|---------|-------|-----------|---------|---------|
| Healthy | 3 | 3 | 3 | 3 | All pods healthy |
| Rolling update | 3 | 4 | 3 | 2 | New pods starting, old pods still running |
| Scale-out in progress | 5 | 3 | 3 | 3 | New pods not yet ready |
| Crash loop | 3 | 1 | 1 | 3 | Pods failing to start |

**Common Issues**:
- **ready > desired**: Temporary during rolling updates (old ReplicaSet hasn't scaled down yet)
- **available < ready**: Pods recently became ready but haven't passed `minReadySeconds` check
- **updated < desired**: Rolling update in progress or failed

---

## Common Patterns

### Pattern 1: Regex Pod Matching

**Why**: Kubernetes pod names include ReplicaSet hash suffix (e.g., `cart-7d8f9b6c4-abc12`).

```promql
# ✅ GOOD: Regex matches all pods for deployment
pod=~"cart-.*"

# ❌ BAD: Won't match any pods (exact match fails)
pod="cart"
```

**Alternative**: Use service label if available (cleaner, but requires label consistency):
```promql
service="cart"
```

---

### Pattern 2: Filtering Infrastructure Pods

**Why**: Exclude Kubernetes infrastructure containers from metrics.

```promql
# ✅ GOOD: Filters out pause container and empty container names
container!="",
container!="POD"

# ❌ BAD: Includes pause container (inflates metrics)
# (no filter)
```

**Additional Filters**:
```promql
# Exclude init containers (if needed)
container!~"init-.*"

# Ensure only running containers
image!=""
```

---

### Pattern 3: Counter vs Gauge Functions

**Critical Rule**: Never apply `rate()` to gauge metrics, never apply `avg_over_time()` to counters without rate.

| Metric Type | Example | Correct Function | Wrong Function |
|-------------|---------|------------------|----------------|
| **Counter** | `container_cpu_usage_seconds_total` | `rate()`, `increase()` | `avg_over_time()` (without rate) |
| **Gauge** | `container_memory_working_set_bytes` | `avg_over_time()`, `max_over_time()` | `rate()` |
| **Histogram** | `*_bucket`, `*_sum`, `*_count` | `histogram_quantile()`, `rate()` | `avg_over_time()` |

**Why This Matters**:
```promql
# ❌ WRONG: Applying rate() to gauge (nonsensical)
rate(container_memory_working_set_bytes[5m])
# Result: Rate of change of memory usage (meaningless for absolute values)

# ✅ RIGHT: Use avg_over_time for gauge
avg_over_time(container_memory_working_set_bytes[5m])
# Result: Average memory usage over 5 minutes
```

---

### Pattern 4: Time Window Selection

**Rate Window**:
```promql
# Short window (1m): More responsive, noisier
rate(container_cpu_usage_seconds_total[1m])

# Long window (5m): Smoother, less responsive
rate(container_cpu_usage_seconds_total[5m])
```

**Recommendation**:
- Use **1m** for rate calculation (matches Prometheus scrape interval)
- Use **5m** for averaging over time (smooths out transient spikes)
- Combine both: `avg_over_time(rate(...[1m])[5m:])`

**Trade-offs**:
| Window | Pros | Cons |
|--------|------|------|
| 1m | Responsive to changes | Noisy, affected by scrape jitter |
| 5m | Smooth, stable | Delayed detection of faults |
| 15m+ | Very stable | Too slow for fault detection |

---

### Pattern 5: Handling Missing Metrics

**Use `or vector(0)` for Graceful Fallback**:
```promql
# Without fallback: Returns empty result if no pods match
sum by (pod) (rate(container_cpu_usage_seconds_total[5m]))

# With fallback: Returns 0 if no pods match
sum by (pod) (rate(container_cpu_usage_seconds_total[5m])) or vector(0)
```

**Use `absent()` to Detect Metric Existence**:
```promql
# Returns 1 if metric doesn't exist, empty otherwise
absent(container_cpu_usage_seconds_total{pod=~"cart-.*"})
```

---

## Troubleshooting

### Issue 1: No Data Returned

**Symptoms**:
```python
results = prom.custom_query(query)
# results = []
```

**Debugging Steps**:

1. **Check metric exists**:
```promql
# List all metrics matching pattern
{__name__=~"container_cpu.*"}
```

2. **Check label values**:
```promql
# Find actual pod names
container_cpu_usage_seconds_total{namespace="robot-shop"}
```

3. **Simplify query progressively**:
```promql
# Step 1: Check raw metric
container_cpu_usage_seconds_total

# Step 2: Add namespace filter
container_cpu_usage_seconds_total{namespace="robot-shop"}

# Step 3: Add pod filter
container_cpu_usage_seconds_total{namespace="robot-shop", pod=~"cart.*"}

# Step 4: Add rate
rate(container_cpu_usage_seconds_total{namespace="robot-shop", pod=~"cart.*"}[1m])

# Step 5: Add aggregation
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="robot-shop", pod=~"cart.*"}[1m]))
```

4. **Check time range** (query might be too far in past):
```python
# Use instant query for current state
prom.custom_query(query)

# Use range query for historical data
prom.custom_query_range(
    query=query,
    start_time=start,
    end_time=end,
    step="15s"
)
```

---

### Issue 2: Unexpected Values

**Problem**: CPU usage shows 50 cores (impossible for pod with 0.5 core limit).

**Cause**: Aggregating across multiple pods without `by (pod)`.

```promql
# ❌ WRONG: Sums across all pods
sum(rate(container_cpu_usage_seconds_total[1m]))
# Returns: 50.0 (total for all 100 pods)

# ✅ RIGHT: Per-pod values
sum by (pod) (rate(container_cpu_usage_seconds_total[1m]))
# Returns: [{pod="cart-abc", value=0.5}, {pod="cart-def", value=0.5}, ...]
```

---

### Issue 3: Throttle Ratio Returns NaN

**Problem**:
```json
{"metric": {"pod": "cart-abc"}, "value": [1640000000, "NaN"]}
```

**Causes**:
1. Division by zero (no CFS periods recorded)
2. Pod just started (not enough data)
3. Throttle metrics not available on this Kubernetes version

**Solution**:
```python
# Filter out NaN values in Python
by_pod = extract_per_pod_values(results)
by_pod = {k: v for k, v in by_pod.items() if not np.isnan(v)}
```

---

### Issue 4: Metric Suddenly Drops to Zero

**Possible Causes**:

1. **Pod restarted**:
```promql
# Check restart count
increase(kube_pod_container_status_restarts_total{pod=~"cart.*"}[10m])
```

2. **OOMKilled**:
```promql
# Check termination reason
kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}
```

3. **Pod evicted**:
```promql
# Check pod phase
kube_pod_status_phase{phase="Failed"}
```

4. **Metric scrape failure**:
```promql
# Check up status
up{job="kubelet"}
```

---

### Issue 5: Query Timeout

**Symptoms**: Query takes >30s or times out.

**Causes & Solutions**:

| Cause | Solution |
|-------|----------|
| Too long time range | Reduce `[5m]` to `[1m]` or use shorter window |
| Too many pods | Add stricter label filters (namespace, pod prefix) |
| High cardinality labels | Avoid regex on labels like `pod_name` |
| Prometheus overloaded | Check Prometheus metrics: `prometheus_tsdb_head_samples` |

**Query Optimization**:
```promql
# ❌ SLOW: Regex at start of pattern
pod=~".*cart.*"

# ✅ FAST: Exact prefix
pod=~"cart-.*"

# ❌ SLOW: Multiple ORs
pod=~"cart-.*|payment-.*|user-.*"

# ✅ FAST: Separate queries in parallel
```

---

## Best Practices Summary

### Do's ✅

1. **Use per-pod queries** (`sum by (pod)`) for scale-invariant metrics
2. **Filter early** (add namespace/pod filters before aggregation)
3. **Use appropriate functions** (`rate()` for counters, `avg_over_time()` for gauges)
4. **Handle missing data** (use `or vector(0)`, filter NaN in Python)
5. **Match scrape interval** (use 1m rate window for 15s scrape interval)
6. **Combine rate + avg** (`avg_over_time(rate(...[1m])[5m:])` for stability)
7. **Test queries in Prometheus UI** before deploying to code

### Don'ts ❌

1. **Don't aggregate without `by (pod)`** (breaks scale-invariance)
2. **Don't use `rate()` on gauges** (meaningless)
3. **Don't use regex `.*` at pattern start** (slow)
4. **Don't ignore NaN values** (causes statistics computation errors)
5. **Don't use very long time ranges** (>5m for rate calculations)
6. **Don't query too frequently** (respect Prometheus scrape interval)
7. **Don't assume metrics exist** (always handle empty results)

---

## Additional Resources

- **Prometheus Query Basics**: https://prometheus.io/docs/prometheus/latest/querying/basics/
- **Kubernetes Metrics Reference**: https://github.com/kubernetes/kube-state-metrics/tree/main/docs
- **cAdvisor Metrics**: https://github.com/google/cadvisor/blob/master/docs/storage/prometheus.md
- **CPU Throttling Deep Dive**: https://engineering.indeedblog.com/blog/2019/12/cpu-throttling-regression-fix/

---

## Contact & Feedback

If you encounter issues with these queries or need help debugging:

1. Check Prometheus UI for manual query testing
2. Verify metric names match your Prometheus version
3. Ensure kube-state-metrics and cAdvisor are deployed
4. Check Kubernetes version compatibility (some metrics added in newer versions)

For project-specific questions, refer to `/CLAUDE.md` for agent selection guidance.
