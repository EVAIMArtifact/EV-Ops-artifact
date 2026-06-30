# run_observation_experiment.py

import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.fault_injection.fault_inject import inject_fault, recover_fault
from src.monitoring.collector import collect_multi_service_observation
from src.monitoring.config import CollectionWindow, ALL_METRIC_GROUPS

from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
    
RESULTS_DIR = Path("experiment_results_observation")
RESULTS_DIR.mkdir(exist_ok=True)


def run_fault_observation(exp):
    fault = {
        "type": exp["type"],
        "app": exp["app"],
        "namespace": exp["namespace"],
        "service": exp["service"],
        "deployment": exp.get("deployment", exp["service"]),
    }

    for key in ["users", "spawn_rate", "pressure_type", "bad_image", "container", "pod"]:
        if key in exp:
            fault[key] = exp[key]

    metric_cfg = exp.get("metric_collection", {})
    observe_services = metric_cfg.get("observe_services", [fault["service"]])

    before_window = CollectionWindow(
        lookback_seconds=metric_cfg.get("before_lookback_seconds", 120),
        step_seconds=metric_cfg.get("before_step_seconds", 15),
        rate_interval=metric_cfg.get("rate_interval", "1m"),
    )

    after_window = CollectionWindow(
        lookback_seconds=metric_cfg.get("after_lookback_seconds", 120),
        step_seconds=metric_cfg.get("after_step_seconds", 15),
        rate_interval=metric_cfg.get("rate_interval", "1m"),
    )

    fault_id = f'{fault["app"]}-{fault["type"]}-{fault["service"]}-{int(time.time())}'
    exp_dir = RESULTS_DIR / fault_id
    exp_dir.mkdir(parents=True, exist_ok=True)

    result = {"fault_id": fault_id, "fault": fault}

    try:
        injection_result = inject_fault(fault)
        result["injection_result"] = injection_result

        time.sleep(metric_cfg.get("fault_init_wait_seconds", 30))
        time.sleep(metric_cfg.get("fault_observation_wait_seconds", 60))

        metrics_during_fault = collect_multi_service_observation(
            prometheus_url="unused",
            fault=fault,
            services=observe_services,
            window=before_window,
            metric_groups=metric_cfg.get("groups", ALL_METRIC_GROUPS),
        )
        result["metrics_during_fault"] = metrics_during_fault

    finally:
        recovery_result = recover_fault(fault, result.get("injection_result", {}))
        result["recovery_result"] = str(recovery_result)

    time.sleep(metric_cfg.get("recovery_wait_seconds", 60))

    metrics_after_recovery = collect_multi_service_observation(
        prometheus_url="unused",
        fault=fault,
        services=observe_services,
        window=after_window,
        metric_groups=metric_cfg.get("groups", ALL_METRIC_GROUPS),
    )
    result["metrics_after_recovery"] = metrics_after_recovery

    with open(exp_dir / "observation.json", "w") as f:
        json.dump(result, f, indent=2, default=str)

    return result


def run_batch(config):
    experiments = config["experiments"]
    mode = config.get("execution_mode", "sequential")
    max_workers = config.get("max_workers", 1)

    if mode == "sequential":
        return [run_fault_observation(exp) for exp in experiments]

    if mode == "parallel":
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(run_fault_observation, exp) for exp in experiments]
            for fut in as_completed(futures):
                results.append(fut.result())
        return results

    raise ValueError(f"Unknown execution_mode: {mode}")


if __name__ == "__main__":
    import sys
    with open(sys.argv[1]) as f:
        config = json.load(f)

    results = run_batch(config)
    print(json.dumps(results, indent=2, default=str))