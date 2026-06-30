from pathlib import Path
import argparse
import json
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Patch
from matplotlib.lines import Line2D


# -----------------------------
# Data loading helpers
# -----------------------------

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
    service = before.get("target_service") or before.get("service") or before.get("deployment") or "target"
    return baseline, before, after, service


def extract_metric(run_dir: Path, metric_name: str):
    baseline, before, after, service = load_phase_metrics(run_dir)
    b = find_series(baseline, service, "container_resources", metric_name) * 100.0
    f = find_series(before, service, "container_resources", metric_name) * 100.0
    a = find_series(after, service, "container_resources", metric_name) * 100.0
    if len(b) == 0 or len(f) == 0 or len(a) == 0:
        raise ValueError(
            f"Missing series for {metric_name} in {run_dir}. "
            "Expected metrics_baseline.json, metrics_before.json, and metrics_after.json "
            "with container_resources series_aggregate_mean."
        )
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
        "feedback_computation": mean_key("feedback_computation"),
        "experience_storage": mean_key("experience_storage"),
    }


def add_top_box(ax, x, y, text, fontsize=8.2):
    ax.text(
        x,
        y,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#cbd5e1", alpha=0.96),
        zorder=10,
    )


def phase_mean(ax, vals, x0, x1, color):
    mean = float(np.nanmean(vals))
    ax.hlines(mean, x0, x1, color=color, linestyle=(0, (1.2, 2.2)), linewidth=1.8, alpha=0.95)
    return mean


# -----------------------------
# Plot
# -----------------------------

def make_plot(cpu_run_dir, mem_run_dir, cpu_latencies, mem_latencies, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)

    cpu_b, cpu_f, cpu_a, cpu_service = extract_metric(cpu_run_dir, "cpu_usage_to_limit_ratio")
    mem_b, mem_f, mem_a, mem_service = extract_metric(mem_run_dir, "memory_usage_to_limit_ratio")

    # Use the CPU run as the reference phase length and interpolate the memory run
    # onto the same phase grid. This makes the plot explicitly phase-aligned,
    # not a claim that CPU and memory values were from the same run.
    n_b, n_f, n_a = len(cpu_b), len(cpu_f), len(cpu_a)
    mem_b = align_length(mem_b, n_b)
    mem_f = align_length(mem_f, n_f)
    mem_a = align_length(mem_a, n_a)

    cpu_values = np.concatenate([cpu_b, cpu_f, cpu_a])
    mem_values = np.concatenate([mem_b, mem_f, mem_a])
    x = np.arange(len(cpu_values))

    fault_boundary = n_b - 0.5
    observe_end = n_b + n_f - 0.5
    end = len(cpu_values) - 0.5

    lat = avg_latency(cpu_latencies, mem_latencies)
    post_len = max(end - observe_end, 1.0)

    # Place validation milestones in phase space. The widths are illustrative,
    # but the labels report real measured latency values.
    planning_width = min(max(post_len * 0.18, 0.65), post_len * 0.25)
    exec_width = min(max(post_len * 0.30, 1.15), post_len * 0.45)
    t0 = fault_boundary
    t1 = observe_end
    t2 = min(t1 + planning_width, end - 2.0)
    t3 = min(t2 + exec_width, end - 0.9)
    t4 = end

    cpu_color = "#e03131"
    mem_color = "#845ef7"
    green = "#2b8a3e"
    orange = "#e67700"
    blue = "#1971c2"
    grey = "#94a3b8"

    plt.rcParams.update({
        "font.family": "sans-serif",
        "axes.edgecolor": "#d0d7de",
        "axes.linewidth": 0.9,
    })

    fig = plt.figure(figsize=(14.2, 7.8))
    ax = fig.add_axes([0.075, 0.255, 0.88, 0.61])

    # Phase backgrounds: baseline, observed degradation, planning, execution/rollout, validation.
    ax.axvspan(-0.5, fault_boundary, color="#f1f8e9", alpha=0.76, zorder=0)
    ax.axvspan(fault_boundary, observe_end, color="#fff0f3", alpha=0.72, zorder=0)
    ax.axvspan(observe_end, t2, color="#fff8db", alpha=0.85, zorder=0)
    ax.axvspan(t2, t3, color="#dbeafe", alpha=0.88, zorder=0)
    ax.axvspan(t3, end, color="#e7f5ff", alpha=0.72, zorder=0)

    # Lines.
    cpu_line, = ax.plot(
        x, cpu_values, color=cpu_color, marker="o", linewidth=2.4,
        markersize=5.4, label="CPU pressure run: CPU usage / limit (%)", zorder=4
    )
    mem_line, = ax.plot(
        x, mem_values, color=mem_color, marker="D", linewidth=2.4,
        markersize=4.9, label="Memory pressure run: memory usage / limit (%)", zorder=4
    )

    # Phase means, shown with an explicit legend entry.
    phase_mean(ax, cpu_values[:n_b], -0.2, fault_boundary, cpu_color)
    phase_mean(ax, cpu_values[n_b:n_b+n_f], fault_boundary, observe_end, cpu_color)
    phase_mean(ax, cpu_values[n_b+n_f:], observe_end, end, cpu_color)
    phase_mean(ax, mem_values[:n_b], -0.2, fault_boundary, mem_color)
    phase_mean(ax, mem_values[n_b:n_b+n_f], fault_boundary, observe_end, mem_color)
    phase_mean(ax, mem_values[n_b+n_f:], observe_end, end, mem_color)

    # Timeline markers.
    for xpos in [t0, t1, t2, t3, t4]:
        ax.axvline(xpos, color=grey, linestyle="--", linewidth=1.15, alpha=0.95, zorder=2)

    ymax = max(float(np.nanmax(cpu_values)), float(np.nanmax(mem_values)), 10.0) * 1.36
    ax.set_ylim(0, ymax)
    ax.set_xlim(-0.5, end)

    # Top phase labels; compact to avoid overlap.
    y_phase = ymax * 0.955
    ax.text((-.5 + fault_boundary) / 2, y_phase, "① Baseline\nhealthy", color=green, ha="center", va="top", fontsize=9.4, fontweight="bold")
    ax.text((fault_boundary + observe_end) / 2, y_phase, "② Fault observation\nresource pressure", color="#c92a2a", ha="center", va="top", fontsize=9.4, fontweight="bold")
    ax.text((observe_end + t2) / 2, y_phase, "③ Plan + generate", color=orange, ha="center", va="top", fontsize=9.4, fontweight="bold")
    ax.text((t2 + t3) / 2, y_phase, "④ Execute + rollout", color=blue, ha="center", va="top", fontsize=9.4, fontweight="bold")
    ax.text((t3 + end) / 2, y_phase, "⑤ Validate\nrecovery", color=green, ha="center", va="top", fontsize=9.4, fontweight="bold")

    # Milestone boxes. Lower than phase labels to prevent crowding.
    y_box = ymax * 0.78
    add_top_box(ax, t0, y_box, "$t_0$\nFault injected")
    add_top_box(ax, t1, y_box, "$t_1$\nObservation done")
    add_top_box(ax, t2, y_box, "$t_2$\nMitigation starts")
    add_top_box(ax, t3, y_box, "$t_3$\nRollout done")
    add_top_box(ax, t4, y_box, "$t_4$\nFeedback loop")

    # Execution-validation callout.
    callout = (
        "Execution validation\n"
        "rollout complete + post-action health\n"
        "before storing experience"
    )
    ax.text(
        (t3 + end) / 2,
        ymax * 0.46,
        callout,
        ha="center",
        va="center",
        fontsize=9.0,
        fontweight="bold",
        color="#1864ab",
        bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="#93c5fd", alpha=0.92),
        zorder=9,
    )

    ax.set_title(
        "Motivating Example: Execution-Validated Resource Pressure Mitigation",
        fontsize=14.3,
        fontweight="bold",
        pad=18,
    )
    ax.set_ylabel("Normalized resource usage (% of configured limit)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Phase-aligned timeline steps", fontsize=11, fontweight="bold")

    ax.set_xticks([0, max(0, n_b - 1), n_b, n_b + n_f - 1, len(cpu_values) - 1])
    ax.set_xticklabels(["baseline", "fault inj.", "observed", "mitigated", "after"], fontsize=9)

    ax.grid(True, axis="y", linestyle="-", color="#e5e7eb", alpha=0.8)
    ax.grid(False, axis="x")

    mean_handle = Line2D([0], [0], color="#334155", linestyle=(0, (1.2, 2.2)), linewidth=1.8, label="Phase mean")
    patch_handles = [
        Patch(facecolor="#fff8db", edgecolor="none", alpha=0.85, label="Planning/generation window"),
        Patch(facecolor="#dbeafe", edgecolor="none", alpha=0.88, label="Execution/rollout window"),
    ]
    ax.legend(
        handles=[cpu_line, mem_line, mean_handle] + patch_handles,
        loc="upper left",
        frameon=True,
        framealpha=0.95,
        fontsize=9.1,
    )

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)

    # Bottom summary box.
    rect = FancyBboxPatch(
        (0.035, 0.045), 0.93, 0.135,
        facecolor="white", edgecolor="#aab4c0",
        boxstyle="round,pad=0.012",
        transform=fig.transFigure, clip_on=False,
        linewidth=1.0,
    )
    fig.patches.append(rect)

    cpu_text = f"CPU run\n{np.nanmean(cpu_b):.1f}% → {np.nanmean(cpu_f):.1f}%\npost {np.nanmean(cpu_a):.1f}%"
    mem_text = f"Memory run\n{np.nanmean(mem_b):.1f}% → {np.nanmean(mem_f):.1f}%\npost {np.nanmean(mem_a):.1f}%"
    plan_total = lat["llm_planning"] + lat["playbook_generation"]
    exec_total = lat["playbook_execution"] + lat["rollout_wait"]
    validation_total = lat["feedback_computation"] + lat["experience_storage"]

    fig.text(0.145, 0.112, cpu_text, ha="center", va="center", fontsize=10.0, color=cpu_color, fontweight="bold")
    fig.text(0.325, 0.112, mem_text, ha="center", va="center", fontsize=10.0, color=mem_color, fontweight="bold")
    fig.text(0.515, 0.112, f"Plan + generate\nLLM {lat['llm_planning']:.1f}s + playbook {lat['playbook_generation']:.1f}s\n= {plan_total:.1f}s", ha="center", va="center", fontsize=10.0, color="#1d4ed8", fontweight="bold")
    fig.text(0.715, 0.112, f"Execute + rollout\nplaybook {lat['playbook_execution']:.1f}s + rollout {lat['rollout_wait']:.1f}s\n= {exec_total:.1f}s", ha="center", va="center", fontsize=10.0, color=blue, fontweight="bold")
    fig.text(0.895, 0.112, f"Outcome\nfeedback at {lat['time_to_feedback']:.1f}s\nvalidation {validation_total:.2f}s", ha="center", va="center", fontsize=10.0, color="#111827", fontweight="bold")

    for x_pos in [0.235, 0.425, 0.615, 0.805]:
        fig.text(x_pos, 0.065, "┊", color="#aab4c0", fontsize=26, ha="center")

    # Use both PNG and vector PDF for papers.
    out_png = out_dir / "motivation_resource_pressure_timeline_fixed.png"
    out_pdf = out_dir / "motivation_resource_pressure_timeline_fixed.pdf"
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.savefig(out_pdf, bbox_inches="tight")
    plt.close()

    print(f"Saved: {out_png}")
    print(f"Saved: {out_pdf}")


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
