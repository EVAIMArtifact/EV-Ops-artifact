import sys
import json
import argparse
from pathlib import Path
import csv
import time
from src.experiment.run_experiment import run_single_experiment
from src.experiment.rule_based_recovery import run_single_rule_based_experiment
from src.monitoring.config import APP_TO_NAMESPACE

DEFAULT_EXPERIMENT_RESULTS_PATH = Path(__file__).parent.parent / "experiment_results"


def parse_args():
    parser = argparse.ArgumentParser(description="Run EV-AIM or rule-based batch experiments.")
    parser.add_argument("--file", required=True, help="Path to experiment JSON file")
    parser.add_argument(
        "--mode",
        choices=["evaim", "rule_based"],
        default="evaim",
        help="evaim = LLM planner/executor, rule_based = deterministic recovery baseline",
    )
    parser.add_argument(
        "--results-dir",
        default=str(DEFAULT_EXPERIMENT_RESULTS_PATH),
        help="Directory where experiment results will be written",
    )
    parser.add_argument(
    "--sleep-between",
    type=int,
    default=60,
    help="Seconds to wait between experiments (default: 60)",
)
    return parser.parse_args()



if __name__ == "__main__":
    cli_args = parse_args()

    exp_args_json_file_path = Path(cli_args.file)
    exp_results_path = Path(cli_args.results_dir)
    exp_results_path.mkdir(parents=True, exist_ok=True)

    with open(exp_args_json_file_path, "r") as f:
        args = json.load(f)

    if not args:
        print("Error: No arguments provided in the JSON file.", file=sys.stderr)
        sys.exit(1)

    llm_model_args = args.get("llm_model", {})
    exp_args = args.get("experiments", [])

    if not exp_args:
        print("Error: Missing or empty 'experiments' section in JSON file.", file=sys.stderr)
        sys.exit(1)

    if cli_args.mode == "evaim" and not llm_model_args:
        print("Error: Missing 'llm_model' section for EV-AIM mode.", file=sys.stderr)
        sys.exit(1)

    print("\n========== BATCH EXPERIMENT RUNNER ==========")
    print(f"[INFO] Mode: {cli_args.mode}")
    print(f"[INFO] Config file: {exp_args_json_file_path}")
    print(f"[INFO] Results directory: {exp_results_path}")
    print(f"[INFO] Number of experiments: {len(exp_args)}")

    for exp_arg in exp_args:
        experiment_name = exp_arg.get("name", "")
        fault_type = exp_arg.get("type", "")
        service = exp_arg.get("service", "")

        if not fault_type or not service:
            print(
                f"Error: missing 'type' or 'service' in experiment {experiment_name}",
                file=sys.stderr,
            )
            continue

        print(f"\n========== RUNNING EXPERIMENT: {experiment_name} ==========")
        print(f"[INFO] Fault type: {fault_type}")
        print(f"[INFO] Service: {service}")
        print(f"[INFO] Mode: {cli_args.mode}")

        common_args = dict(
            experiment_name=experiment_name,
            fault_type=fault_type,
            service=service,
            duration=exp_arg.get("duration", 120),

            app=exp_arg.get("app", "robot-shop"),
            # Omit "namespace" in the JSON to auto-resolve it from the app
            # (sock-shop -> sock-shop2, etc. via APP_TO_NAMESPACE). An explicit
            # "namespace" still wins. Previously this defaulted to the app
            # STRING, which silently pinned sock-shop/online-boutique to their
            # un-instrumented legacy namespaces.
            namespace=exp_arg.get("namespace")
            or APP_TO_NAMESPACE.get(
                exp_arg.get("app", "robot-shop"), exp_arg.get("app", "robot-shop")
            ),
            deployment=exp_arg.get("deployment", service),
            pod=exp_arg.get("pod"),
            container=exp_arg.get("container"),
            users=exp_arg.get("users"),
            spawn_rate=exp_arg.get("spawn_rate"),
            pressure_type=exp_arg.get("pressure_type"),
            bad_image=exp_arg.get("bad_image"),
            memory_percent=exp_arg.get("memory_percent"),
            memory_mb=exp_arg.get("memory_mb"),
            cpu_cores=exp_arg.get("cpu_cores"),
            metric_collection=exp_arg.get("metric_collection", {}),

            metrics_to_fetch=exp_arg.get("metrics", []),
            exp_results_path=exp_results_path,
            slo_thresholds=exp_arg.get("slo_thresholds", args.get("slo_thresholds")),
            mode=exp_arg.get("mode"),
            workers=exp_arg.get("workers"),
            size=exp_arg.get("size"),
            load=exp_arg.get("load"),
            latency=exp_arg.get("latency"),
            jitter=exp_arg.get("jitter"),
            loss=exp_arg.get("loss"),
            correlation=exp_arg.get("correlation"),
            direction=exp_arg.get("direction"),
            action=exp_arg.get("action"),
            limit=exp_arg.get("limit"),
            env=exp_arg.get("env"),
            bad_value=exp_arg.get("bad_value"),
            size_mb=exp_arg.get("size_mb"),
            max_sec=exp_arg.get("max_sec"),
        )

        # try:
        if cli_args.mode == "evaim":
            result = run_single_experiment(
                **common_args,
                client=llm_model_args.get("client", ""),
                model_id=llm_model_args.get("model_id", ""),
                api_key=llm_model_args.get("api_key", ""),
                endpoint=llm_model_args.get("endpoint", ""),
                temperature=float(llm_model_args.get("temperature", 0.0)),
                max_tokens=int(llm_model_args.get("max_tokens", 1000)),
                use_normalized_feedback=bool(
                    exp_arg.get(
                        "use_normalized_feedback",
                        args.get("use_normalized_feedback", False),
                    )
                ),
            )

        else:
            
            try:
                result = run_single_rule_based_experiment(
                            **common_args
                        )
            except Exception as e:
                print(f"[BATCH ERROR] Experiment failed: {exp.get('name')}")
                print(f"{type(e).__name__}: {e}")
                continue

        print("[INFO] Experiment completed.")
        if result:
            print(f"[INFO] Reward: {result.get('reward')}")
            print(f"[INFO] SHS before: {result.get('SHS_before')}")
            print(f"[INFO] SHS after: {result.get('SHS_after')}")
            print(f"[INFO] ΔSHS: {result.get('delta_SHS')}")
        if exp_arg != exp_args[-1]:
            print(f"[INFO] Sleeping {cli_args.sleep_between} seconds before next experiment...")
            time.sleep(cli_args.sleep_between)

        # except Exception as e:
        #     print(
        #         f"[ERROR] Experiment failed: {experiment_name} "
        #         f"({fault_type}/{service})"
        #     )
        #     print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        #     continue
