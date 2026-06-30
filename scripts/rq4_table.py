# scripts/build_rq4_table.py
import json
import math
import argparse
from pathlib import Path
from collections import Counter

import pandas as pd


def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def fault_group(fault_type):
    f = str(fault_type or "").lower()
    if "cpu" in f:
        return "CPU"
    if "mem" in f or "memory" in f:
        return "Memory"
    if "disk" in f or "fs" in f or "io" in f:
        return "Disk"
    return "Other"


def app_from_path(path):
    p = str(path).lower()
    if "robot-shop" in p:
        return "Robot-Shop"
    if "sock-shop" in p:
        return "Sock-Shop"
    if "online-boutique" in p:
        return "Online Boutique"
    return "Unknown"


def entropy(values):
    values = [v for v in values if v]
    if not values:
        return 0.0
    counts = Counter(values)
    n = sum(counts.values())
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def safe_mean(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    return s.mean() if len(s) else None


def corr(df, x, y):
    tmp = df[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(tmp) < 3:
        return None
    if tmp[x].nunique() < 2 or tmp[y].nunique() < 2:
        return None
    return tmp[x].corr(tmp[y])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--out", default="paper_tables/rq4")
    parser.add_argument(
        "--max-runs-per-app-fault",
        type=int,
        default=3,
        help="Use only top N runs for each app/fault group. Use 0 for all runs.",
    )
    args = parser.parse_args()

    base = Path(args.base)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []

    for feedback_path in base.rglob("feedback.json"):
        run_dir = feedback_path.parent

        feedback = load_json(run_dir / "feedback.json")
        plan = load_json(run_dir / "plan.json")
        lat = load_json(run_dir / "latencies.json")
        infra = load_json(run_dir / "infrastructure_comparison.json")

        fault_type = feedback.get("fault_type") or run_dir.name
        fault = fault_group(fault_type)
        app = app_from_path(run_dir)

        normalized_action = (
            feedback.get("normalized_action")
            or plan.get("normalized_action")
            or {}
        )

        action_type = (
            normalized_action.get("action_type")
            or feedback.get("plan_action")
            or plan.get("target_changes", {}).get("type")
            or "none"
        )

        target_value = (
            normalized_action.get("value")
            or plan.get("target_changes", {}).get("target_value")
            or ""
        )

        target = (
            normalized_action.get("target")
            or feedback.get("service")
            or plan.get("target_changes", {}).get("target")
            or ""
        )

        # This is the stronger diversity unit:
        # action alone: scale_up_cpu
        # action+value: scale_up_cpu:0.75 cores
        action_value = f"{action_type}:{target_value}".strip(":")
        action_target_value = f"{action_type}:{target}:{target_value}".strip(":")

        symptoms_before = feedback.get("symptoms_before", {})
        symptoms_after = feedback.get("symptoms_after", {})

        latency_before = symptoms_before.get("latency_p95_ms")
        latency_after = symptoms_after.get("latency_p95_ms")
        error_before = symptoms_before.get("error_rate")
        error_after = symptoms_after.get("error_rate")

        latency_reduction = None
        if latency_before is not None and latency_after is not None:
            latency_reduction = float(latency_before) - float(latency_after)

        error_reduction = None
        if error_before is not None and error_after is not None:
            error_reduction = float(error_before) - float(error_after)

        rows.append({
            "run_dir": str(run_dir),
            "app": app,
            "fault_type": fault_type,
            "fault": fault,
            "service": feedback.get("service"),
            "action": action_type,
            "target": target,
            "target_value": target_value,
            "action_value": action_value,
            "action_target_value": action_target_value,

            "reward": feedback.get("reward"),
            "FRQ": feedback.get("FRQ") or feedback.get("fault_recovery_score"),
            "recovery_success": int(bool(feedback.get("recovery_success", False))),
            "delta_SHS": feedback.get("delta_SHS"),
            "latency_reduction": latency_reduction,
            "error_reduction": error_reduction,
            "resource_cost": feedback.get("resource_cost"),

            "retrieval_time": lat.get("experience_retrieval"),
            "planning_time": lat.get("llm_planning"),
            "playbook_generation": lat.get("playbook_generation"),
            "execution_time": lat.get("playbook_execution"),
            "feedback_time": lat.get("feedback_computation"),
            "rollout_time": lat.get("rollout_wait"),

            # Do not use total_experiment_time as EV-AIM overhead.
            "evaim_overhead": sum(
                float(x or 0.0)
                for x in [
                    lat.get("experience_retrieval"),
                    lat.get("llm_planning"),
                    lat.get("playbook_generation"),
                    lat.get("feedback_computation"),
                ]
            ),

            "cpu_changed": infra.get("cpu_limit_changed"),
            "memory_changed": infra.get("memory_limit_changed"),
            "scale_out": infra.get("scale_out_occurred"),
        })

    df = pd.DataFrame(rows)

    # Keep only CPU/Memory/Disk.
    df = df[df["fault"].isin(["CPU", "Memory", "Disk"])].copy()

    # Limit to top N runs per app/fault.
    # This prevents one app/fault from dominating RQ4.
    if args.max_runs_per_app_fault and args.max_runs_per_app_fault > 0:
        df = (
            df.sort_values("run_dir")
              .groupby(["app", "fault"], group_keys=False)
              .head(args.max_runs_per_app_fault)
              .reset_index(drop=True)
        )

    df.to_csv(out / "rq4_raw_runs_limited.csv", index=False)

    # Global reward validity.
    reward_rows = []
    for label, col in [
        ("Reward vs Recovery Success", "recovery_success"),
        ("Reward vs Delta SHS", "delta_SHS"),
        ("Reward vs FRQ", "FRQ"),
        ("Reward vs Latency Reduction", "latency_reduction"),
        ("Reward vs Error Reduction", "error_reduction"),
        ("Reward vs Resource Cost", "resource_cost"),
    ]:
        reward_rows.append({
            "Metric": label,
            "Correlation": corr(df, "reward", col),
        })

    reward_df = pd.DataFrame(reward_rows)
    reward_df.to_csv(out / "rq4_reward_validity.csv", index=False)

    # Fault-wise policy behavior.
    summary_rows = []

    for fault, g in df.groupby("fault"):
        action_counts = Counter(g["action"])
        action_value_counts = Counter(g["action_value"])

        dominant_action = action_counts.most_common(1)[0][0]
        dominant_action_value = action_value_counts.most_common(1)[0][0]

        summary_rows.append({
            "Fault": fault,
            "Runs": len(g),
            "Dominant Action": dominant_action,
            "Dominant Action+Value": dominant_action_value,
            "Success %": safe_mean(g["recovery_success"]) * 100,
            "Mean Reward": safe_mean(g["reward"]),
            "Mean Delta SHS": safe_mean(g["delta_SHS"]),
            "Mean FRQ": safe_mean(g["FRQ"]),
            "Mean Resource Cost": safe_mean(g["resource_cost"]),
            "Mean EV-AIM Overhead": safe_mean(g["evaim_overhead"]),
        })

    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(out / "rq4_faultwise_summary.csv", index=False)

    # Global diversity.
    diversity = pd.DataFrame([
        {
            "Metric": "Distinct actions",
            "Value": df["action"].nunique(),
        },
        {
            "Metric": "Distinct action+value policies",
            "Value": df["action_value"].nunique(),
        },
        {
            "Metric": "Action entropy",
            "Value": entropy(df["action"].tolist()),
        },
        {
            "Metric": "Action+value entropy",
            "Value": entropy(df["action_value"].tolist()),
        },
        {
            "Metric": "Most common action %",
            "Value": Counter(df["action"]).most_common(1)[0][1] / len(df) * 100,
        },
        {
            "Metric": "Most common action+value %",
            "Value": Counter(df["action_value"]).most_common(1)[0][1] / len(df) * 100,
        },
    ])

    diversity.to_csv(out / "rq4_global_diversity.csv", index=False)

    # print("\nSaved:")
    # print(out / "rq4_raw_runs_limited.csv")
    # print(out / "rq4_reward_validity.csv")
    # print(out / "rq4_faultwise_summary.csv")
    # print(out / "rq4_global_diversity.csv")

    print("\nReward validity:")
    print(reward_df.to_string(index=False))

    print("\nFault-wise summary:")
    print(summary_df.to_string(index=False))

    print("\nGlobal diversity:")
    print(diversity.to_string(index=False))


if __name__ == "__main__":
    main()