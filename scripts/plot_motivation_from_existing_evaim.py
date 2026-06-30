from pathlib import Path
import argparse
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def load_json(path: Path):
    with open(path, "r") as f:
        return json.load(f)


def safe_get(d, keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def find_series(metrics, service, group, metric):
    paths = [
        ["service_observations", service, "system_metrics", group, metric, "series_aggregate_mean"],
        ["service_observations", service, "application_metrics", group, metric, "series_aggregate_mean"],
        ["service_observations", service, "metrics", group, metric, "series_aggregate_mean"],
        ["service_observations", service, group, metric, "values"],
        ["service_observations", service, group, metric, "series"],
    ]
    for p in paths:
        vals = safe_get(metrics, p, None)
        if vals:
            return np.array([float(v) for v in vals if v is not None], dtype=float)
    return np.array([], dtype=float)


def load_phase_metrics(run_dir: Path):
    baseline = load_json(run_dir / "metrics_baseline.json")
    before = load_json(run_dir / "metrics_before.json")
    after = load_json(run_dir / "metrics_after.json")
    service = before.get("target_service") or before.get("service") or before.get("deployment") or "cart"
    return baseline, before, after, service


def extract_metric(run_dir: Path, metric_name: str):
    baseline, before, after, service = load_phase_metrics(run_dir)
    b = find_series(baseline, service, "container_resources", metric_name) * 100.0
    f = find_series(before, service, "container_resources", metric_name) * 100.0
    a = find_series(after, service, "container_resources", metric_name) * 100.0
    return b, f, a, service


def align_length(arr, target_len):
    if len(arr) == target_len:
        return arr
    if len(arr) == 0:
        return np.full(target_len, np.nan)
    old_x = np.linspace(0, 1, len(arr))
    new_x = np.linspace(0, 1, target_len)
    return np.interp(new_x, old_x, arr)


def avg_latency(cpu_lat_path: Path, mem_lat_path: Path):
    cpu = load_json(cpu_lat_path)
    mem = load_json(mem_lat_path)

    def mean_key(k, default=0.0):
        vals = []
        for d in [cpu, mem]:
            v = d.get(k)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return float(np.mean(vals)) if vals else default

    return {
        "time_to_plan": mean_key("time_to_plan"),
        "time_to_remediation_execution": mean_key("time_to_remediation_execution"),
        "time_to_rollout_complete": mean_key("time_to_rollout_complete"),
        "time_to_feedback": mean_key("time_to_feedback"),
        "llm_planning": mean_key("llm_planning"),
        "playbook_generation": mean_key("playbook_generation"),
        "playbook_execution": mean_key("playbook_execution"),
        "rollout_wait": mean_key("rollout_wait"),
    }


def make_plot(cpu_run_dir, mem_run_dir, cpu_latencies, mem_latencies, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)

    cpu_b, cpu_f, cpu_a, cpu_service = extract_metric(cpu_run_dir, "cpu_usage_to_limit_ratio")
    mem_b, mem_f, mem_a, mem_service = extract_metric(mem_run_dir, "memory_usage_to_limit_ratio")

    n_b, n_f, n_a = len(cpu_b), len(cpu_f), len(cpu_a)
    mem_b = align_length(mem_b, n_b)
    mem_f = align_length(mem_f, n_f)
    mem_a = align_length(mem_a, n_a)

    cpu_values = np.concatenate([cpu_b, cpu_f, cpu_a])
    mem_values = np.concatenate([mem_b, mem_f, mem_a])
    x = np.arange(len(cpu_values))

    b0 = n_b - 0.5
    b1 = n_b + n_f - 0.5
    end = len(cpu_values) - 0.5

    lat = avg_latency(cpu_latencies, mem_latencies)

    # Map latency timing onto plot step-space.
    t_fault = b0
    t_plan = b1
    rollout_duration = max(lat["time_to_rollout_complete"] - lat["time_to_remediation_execution"], 1.0)
    feedback_duration = max(lat["time_to_feedback"], 1.0)

    post_len = max(end - b1, 1.0)
    rollout_width = min(post_len * 0.45, max(1.5, rollout_duration / feedback_duration * post_len))
    t_exec = b1 + 0.6
    t_rollout = min(t_exec + rollout_width, end - 0.8)
    t_feedback = end

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["axes.edgecolor"] = "#ced4da"

    fig = plt.figure(figsize=(14, 7.5)) # Slightly taller to prevent text crowding
    ax = fig.add_axes([0.08, 0.26, 0.86, 0.60])

    # 1. Background Spans (Cleaned up pastel colors)
    ax.axvspan(-0.5, b0, color="#f4fce3", alpha=0.6)
    ax.axvspan(b0, b1, color="#fff0f6", alpha=0.6)
    ax.axvspan(b1, end, color="#e7f5ff", alpha=0.6)
    ax.axvspan(t_exec, t_rollout, color="#bcd0f7", alpha=0.4)

    # Data lines
    cpu_line, = ax.plot(x, cpu_values, color="#e03131", marker="o", linewidth=2.2,
                        markersize=5, label="CPU usage / limit (%)")
    mem_line, = ax.plot(x, mem_values, color="#845ef7", marker="D", linewidth=2.2,
                        markersize=4.5, label="Memory usage / limit (%)")

    # Means
    for vals, color in [(cpu_values, "#e03131"), (mem_values, "#845ef7")]:
        ax.hlines(np.nanmean(vals[:n_b]), -0.25, b0, color=color, linestyle=":", linewidth=1.5)
        ax.hlines(np.nanmean(vals[n_b:n_b+n_f]), b0, b1, color=color, linestyle=":", linewidth=1.5)
        ax.hlines(np.nanmean(vals[n_b+n_f:]), b1, end, color=color, linestyle=":", linewidth=1.5)

    # 2. Clearer Vertical Timeline Dividers
    for xpos in [b0, b1, t_exec, t_rollout, t_feedback]:
        ax.axvline(xpos, color="#adb5bd", linestyle="--", linewidth=1.2)

    # Give 40% headroom above max data point for clean text layouts
    ymax = max(np.nanmax(cpu_values), np.nanmax(mem_values), 10.0) * 1.40
    ax.set_ylim(0, ymax)
    ax.set_xlim(-0.5, end)

    # 3. Clean Phase Labels placed inside the top background space
    y_phase = ymax * 0.91
    ax.text((-.5 + b0) / 2, y_phase, "① Baseline\n(Healthy)", color="#2b8a3e", ha="center", va="top", fontsize=9.5, fontweight="bold")
    ax.text((b0 + b1) / 2, y_phase, "② Fault Injected\n(Resource Pressure)", color="#c92a2a", ha="center", va="top", fontsize=9.5, fontweight="bold")
    ax.text((b1 + t_exec) / 2, y_phase, "③ Degradation\n(Observed)", color="#e67e22", ha="center", va="top", fontsize=9.5, fontweight="bold")
    ax.text((t_exec + t_rollout) / 2, y_phase, "④ Mitigation\n(Resource Update)", color="#1c7ed6", ha="center", va="top", fontsize=9.5, fontweight="bold")
    ax.text((t_rollout + end) / 2, y_phase, "⑤ Recovery\n(Validation)", color="#2b8a3e", ha="center", va="top", fontsize=9.5, fontweight="bold")

    # 4. Discrete Time Ticks (Lowered slightly so they don't overlap phase text)
    y_ticks = ymax * 0.78
    ax.text(t_fault, y_ticks, "$t_0$\nFault Injected", ha="center", va="center", fontsize=8.5, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ced4da", alpha=0.8))
    ax.text(t_plan, y_ticks, "$t_1$\nPlanner Done", ha="center", va="center", fontsize=8.5, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ced4da", alpha=0.8))
    ax.text(t_exec, y_ticks, "$t_2$\nMitigation Start", ha="center", va="center", fontsize=8.5, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ced4da", alpha=0.8))
    ax.text(t_rollout, y_ticks, "$t_3$\nRollout Done", ha="center", va="center", fontsize=8.5, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ced4da", alpha=0.8))
    ax.text(t_feedback, y_ticks, "$t_4$\nFeedback Loop", ha="center", va="center", fontsize=8.5, bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#ced4da", alpha=0.8))

    # Middle execution text
    ax.text((t_exec + t_rollout) / 2, ymax * 0.50,
            f"Execution + Rollout\n({lat['time_to_rollout_complete']:.1f}s avg)",
            ha="center", va="center", fontsize=9, fontweight="bold", color="#1c7ed6")

    # Labels & Title
    ax.set_title("Real-time Example: Execution Validation During Resource Pressure Mitigation",
                 fontsize=14, fontweight="bold", pad=20)
    ax.set_ylabel("Resource usage / limit (%)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Timeline Steps", fontsize=11, fontweight="bold")

    ax.set_xticks([0, max(0, n_b-1), n_b, n_b+n_f-1, len(cpu_values)-1])
    ax.set_xticklabels(["base", "fault inj.", "obs.", "mitigation", "after"], fontsize=9)

    ax.grid(True, axis="y", linestyle="-", color="#dee2e6", alpha=0.65)
    ax.legend(handles=[cpu_line, mem_line], loc="upper left", frameon=True, fontsize=10)

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)

    # Bottom summary box (Adjusted coordinates to fit new figure scale)
    rect = FancyBboxPatch(
        (0.04, 0.04), 0.92, 0.13,
        facecolor="white", edgecolor="#adb5bd",
        boxstyle="round,pad=0.012",
        transform=fig.transFigure, clip_on=False,
    )
    fig.patches.append(rect)

    fig.text(0.15, 0.105,
             f"CPU Pressure\n{np.nanmean(cpu_b):.1f}% → {np.nanmean(cpu_f):.1f}%\npost {np.nanmean(cpu_a):.1f}%",
             ha="center", va="center", fontsize=10, color="#e03131", fontweight="bold")
    fig.text(0.34, 0.105,
             f"Memory Pressure\n{np.nanmean(mem_b):.1f}% → {np.nanmean(mem_f):.1f}%\npost {np.nanmean(mem_a):.1f}%",
             ha="center", va="center", fontsize=10, color="#845ef7", fontweight="bold")
    fig.text(0.53, 0.105,
             f"Planning + Generation\nLLM {lat['llm_planning']:.1f}s\nPlaybook {lat['playbook_generation']:.1f}s",
             ha="center", va="center", fontsize=10, color="blue", fontweight="bold")
    fig.text(0.72, 0.105,
             f"Execution Window\nPlaybook {lat['playbook_execution']:.1f}s\nRollout {lat['rollout_wait']:.1f}s",
             ha="center", va="center", fontsize=10, color="#1c7ed6", fontweight="bold")
    fig.text(0.89, 0.105,
             f"Outcome\nFeedback at {lat['time_to_feedback']:.1f}s\nvalidated recovery",
             ha="center", va="center", fontsize=10, color="#212529", fontweight="bold")

    for x_pos in [0.245, 0.435, 0.625, 0.805]:
        fig.text(x_pos, 0.055, "┊", color="#adb5bd", fontsize=28, ha="center")

    out_png = out_dir / "motivation_cpu_memory_timeline.png"
    out_pdf = out_dir / "motivation_cpu_memory_timeline.pdf"
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.savefig(out_pdf, bbox_inches="tight")
    plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-run-dir", required=True)
    parser.add_argument("--mem-run-dir", required=True)
    parser.add_argument("--cpu-latencies", required=True)
    parser.add_argument("--mem-latencies", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    make_plot(
        Path(args.cpu_run_dir),
        Path(args.mem_run_dir),
        Path(args.cpu_latencies),
        Path(args.mem_latencies),
        Path(args.out_dir),
    )


if __name__ == "__main__":
    main()