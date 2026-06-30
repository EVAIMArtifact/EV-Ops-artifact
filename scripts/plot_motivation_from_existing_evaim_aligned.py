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
        raise ValueError(f"Missing series for {metric_name} in {run_dir}.")
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


def add_top_box(ax, x, y, text, fontsize=7.5):
    ax.text(
        x,
        y,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#cbd5e1", alpha=0.96),
        zorder=10,
    )


def phase_mean(ax, vals, x0, x1, color):
    mean = float(np.nanmean(vals))
    ax.hlines(mean, x0, x1, color=color, linestyle=(0, (1.2, 2.2)), linewidth=1.2, alpha=0.85)
    return mean


# -----------------------------
# Plot
# -----------------------------

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

    fault_boundary = n_b - 0.5
    observe_end = n_b + n_f - 0.5
    end = len(cpu_values) - 0.5

    lat = avg_latency(cpu_latencies, mem_latencies)

    plan_total = max(lat["llm_planning"] + lat["playbook_generation"], 1.0)
    exec_total = max(lat["playbook_execution"] + lat["rollout_wait"], 1.0)
    feedback_tail = max(
        lat["time_to_feedback"] - plan_total - exec_total,
        lat["feedback_computation"] + lat["experience_storage"],
        1.0,
    )

    t0 = fault_boundary
    t1 = observe_end
    post_len = max(end - t1, 1.0)
    total_post_time = plan_total + exec_total + feedback_tail

    t2 = t1 + post_len * (plan_total / total_post_time)
    t3 = t2 + post_len * (exec_total / total_post_time)
    t4 = end

    fault_duration_seconds = 240.0
    visible_post_seconds = max(lat["time_to_feedback"], 1.0)
    fault_active_end = min(
        t0 + (fault_duration_seconds / visible_post_seconds) * max(t4 - t0, 1.0),
        t4
    )

    cpu_color = "#e03131"
    mem_color = "#845ef7"
    green = "#2b8a3e"
    blue = "#1971c2"
    grey = "#94a3b8"

    plt.rcParams.update({
        "font.family": "sans-serif",
        "axes.edgecolor": "#cbd5e1",
        "axes.linewidth": 0.8,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
    })

    fig = plt.figure(figsize=(5.4, 4.2))
    ax = fig.add_axes([0.12, 0.32, 0.84, 0.56])

    # Phase backgrounds
    ax.axvspan(-0.5, t0, color="#f1f8e9", alpha=0.76, zorder=0)
    ax.axvspan(t0, t2, color="#fff0f3", alpha=0.72, zorder=0)
    ax.axvspan(t2, t3, color="#dbeafe", alpha=0.88, zorder=0)
    ax.axvspan(t3, t4, color="#e7f5ff", alpha=0.72, zorder=0)

    cpu_line, = ax.plot(
        x, cpu_values, color=cpu_color, marker="o", linewidth=1.5,
        markersize=3.5, label="CPU Run: Usage (%)", zorder=4
    )
    mem_line, = ax.plot(
        x, mem_values, color=mem_color, marker="D", linewidth=1.5,
        markersize=3.0, label="Mem Run: Usage (%)", zorder=4
    )

    phase_mean(ax, cpu_values[:n_b], -0.5, fault_boundary, cpu_color)
    phase_mean(ax, cpu_values[n_b:n_b+n_f], fault_boundary, observe_end, cpu_color)
    phase_mean(ax, cpu_values[n_b+n_f:], observe_end, end, cpu_color)
    phase_mean(ax, mem_values[:n_b], -0.5, fault_boundary, mem_color)
    phase_mean(ax, mem_values[n_b:n_b+n_f], fault_boundary, observe_end, mem_color)
    phase_mean(ax, mem_values[n_b+n_f:], observe_end, end, mem_color)

    for xpos in [t0, t2, t3, t4]:
        ax.axvline(xpos, color=grey, linestyle="--", linewidth=0.8, alpha=0.85, zorder=2)

    ymax = max(float(np.nanmax(cpu_values)), float(np.nanmax(mem_values)), 10.0) * 1.35
    ax.set_ylim(0, ymax)
    ax.set_xlim(-0.5, end)

    y_fault_bar = ymax * 0.82
    ax.hlines(
        y_fault_bar, t0, fault_active_end,
        color=cpu_color, linestyle=(0, (1.0, 2.0)),
        linewidth=1.5, alpha=0.95, zorder=6
    )
    ax.text(
        (t0 + fault_active_end) / 2,
        y_fault_bar + ymax * 0.02,
        "Fault Active During Mitigation",
        ha="center", va="bottom",
        fontsize=7, color=cpu_color,
        fontweight="bold", zorder=7
    )

    y_phase = ymax * 0.96
    ax.text((-0.5 + t0) / 2, y_phase, "① Baseline", color=green, ha="center", va="top", fontsize=8, fontweight="bold")
    ax.text((t0 + t2) / 2, y_phase, "② Fault", color="#c92a2a", ha="center", va="top", fontsize=8, fontweight="bold")
    ax.text((t2 + t3) / 2, y_phase, "③ Exec", color=blue, ha="center", va="top", fontsize=8, fontweight="bold")
    ax.text((t3 + end) / 2, y_phase, "④ Valid", color=green, ha="center", va="top", fontsize=8, fontweight="bold")

    # FIX 1: Staggered Landmark Heights to guarantee t2 and t3 never overlap
    add_top_box(ax, t0, ymax * 0.26, "$t_0$\nInject")
    add_top_box(ax, t2, ymax * 0.42, "$t_2$\nStart")  # Shifted up higher
    add_top_box(ax, t3, ymax * 0.20, "$t_3$\nDone")   # Shifted down lower
    add_top_box(ax, t4, ymax * 0.26, "$t_4$\nLoop")

    ax.text(
        (t3 + end) / 2,
        ymax * 0.58,
        "Rollout\nComplete",
        ha="center", va="center",
        fontsize=7.0, fontweight="bold",
        color="#1864ab",
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#93c5fd", alpha=0.92),
        zorder=9,
    )

    ax.set_title("Real-Time Execution-Validated Resource Pressure Mitigation", fontsize=10, fontweight="bold", pad=10)
    ax.set_ylabel("Normalized Usage (%)", fontsize=9.5, fontweight="bold")
    ax.set_xlabel("Phase-Aligned Timeline Steps", fontsize=9.5, fontweight="bold", labelpad=4)

    # FIX 2: Spaced out X-Axis Tick positions centered cleanly per phase block
    ax.set_xticks([(-0.5 + t0)/2, (t0 + t2)/2, (t2 + t3)/2, (t3 + end)/2])
    ax.set_xticklabels(["baseline", "fault / obs", "mitigation", "recovery"], fontsize=7)

    ax.grid(True, axis="y", linestyle="-", color="#e5e7eb", alpha=0.6)

    mean_handle = Line2D([0], [0], color="#334155", linestyle=(0, (1.2, 2.2)), linewidth=1.2, label="Phase Mean")
    exec_patch = Patch(facecolor="#dbeafe", edgecolor="none", alpha=0.88, label="Exec/Rollout\n Window")

    # FIX 3: Moved Legend into the wide, empty top-left space of Phase ① Baseline
    ax.legend(
        handles=[cpu_line, mem_line, mean_handle, exec_patch],
        loc="upper left",
        bbox_to_anchor=(0.01, 0.68),
        frameon=True,
        framealpha=0.95,
        fontsize=7.0,
    )

    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)

    # Re-proportioned bottom metrics panel frame block
    rect = FancyBboxPatch(
        (0.04, 0.15), 0.92, 0.07,
        facecolor="white", edgecolor="#cbd5e1",
        boxstyle="round,pad=0.01",
        transform=fig.transFigure,
        clip_on=False,
        linewidth=0.8,
    )
    fig.patches.append(rect)

    cpu_text = f"CPU Run\n{np.nanmean(cpu_b):.0f}% $\\rightarrow$ {np.nanmean(cpu_f):.0f}% $\\rightarrow$ {np.nanmean(cpu_a):.0f}%"
    mem_text = f"Mem Run\n{np.nanmean(mem_b):.0f}% $\\rightarrow$ {np.nanmean(mem_f):.0f}% $\\rightarrow$ {np.nanmean(mem_a):.0f}%"
    plan_total = lat["llm_planning"] + lat["playbook_generation"]
    exec_total = lat["playbook_execution"] + lat["rollout_wait"]

    y_text_layer = 0.19
    fig.text(0.14, y_text_layer, cpu_text, ha="center", va="center", fontsize=7.5, color=cpu_color, fontweight="bold")
    fig.text(0.36, y_text_layer, mem_text, ha="center", va="center", fontsize=7.5, color=mem_color, fontweight="bold")
    fig.text(0.54, y_text_layer, f"Plan & Gen\n{plan_total:.1f}s", ha="center", va="center", fontsize=7.5, color="#1d4ed8", fontweight="bold")
    fig.text(0.71, y_text_layer, f"Exec & Roll\n{exec_total:.1f}s", ha="center", va="center", fontsize=7.5, color=blue, fontweight="bold")
    fig.text(0.89, y_text_layer, f"Feedback\n{lat['time_to_feedback']:.1f}s", ha="center", va="center", fontsize=7.5, color="#111827", fontweight="bold")

    for x_pos in [0.25, 0.47, 0.62, 0.80]:
        fig.text(x_pos, 0.17, "┊", color="#cbd5e1", fontsize=15, ha="center")

    out_png = out_dir / "motivation_resource_pressure_timeline_aligned3.png"
    out_pdf = out_dir / "motivation_resource_pressure_timeline_aligned3.pdf"

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