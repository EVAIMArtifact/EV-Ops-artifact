from pathlib import Path
import json
import numpy as np
import matplotlib.pyplot as plt

# ============================================================
# CONFIGURATION FOR 3x3 GRID
# ============================================================

BASE_DIR = Path(
    r"/experiment_results/old_4_50_pm/"
)

# Shared structural row labels for headers
ROW_LABELS = ["Approach A (LLM Only)", "Approach B (CoT)", "Approach C (Our Framework)"]

# A explicit 3x3 layout mapping. You can change folders/reasons per subfigure here.
GRID_CONFIG = [
    # --- ROW 1 ---
    [
        {
            "title": "CPU Hog",
            "folder": "evaim_rs_all_noReward_1/llm-robot-shop-cpu_hog-cart-1782582907",
            "fault_type": "cpu_hog",
            "metric_label": "CPU usage / limit (%)",
            "color": "#e03131",
            "planner_reason": "active degradation",
        },
        {
            "title": "Memory Stress",
            "folder": "evaim_rs_all_noReward_1/llm-robot-shop-mem_stress-cart-1782581201",
            "fault_type": "mem_stress",
            "metric_label": "Memory usage / limit (%)",
            "color": "#845ef7",
            "planner_reason": "insufficient evidence",
        },
        {
            "title": "Disk Stress",
            "folder": "evaim_rs_all_noReward_1/llm-robot-shop-disk_stress-cart-1782586823",
            "fault_type": "disk_stress",
            "metric_label": "Disk throughput (MB/s)",
            "color": "#1c7ed6",
            "planner_reason": "insufficient evidence",
        },
    ],
    # --- ROW 2 ---
    [
        {
            "title": "CPU Hog",
            "folder": "evaim_rs_all_noAPL_2/llm-robot-shop-cpu_hog-cart-1782574892",  # Update folder when ready
            "fault_type": "cpu_hog",
            "metric_label": "CPU usage / limit (%)",
            "color": "#e03131",
            "planner_reason": "active degradation",
        },
        {
            "title": "Memory Stress",
            "folder": "evaim_rs_all_noAPL_2/llm-robot-shop-mem_stress-cart-1782572968",  # Update folder when ready
            "fault_type": "mem_stress",
            "metric_label": "Memory usage / limit (%)",
            "color": "#845ef7",
            "planner_reason": "insufficient evidence",
        },
        {
            "title": "Disk Stress",
            "folder": "evaim_rs_all_noAPL_2/llm-robot-shop-disk_stress-cart-1782579122",  # Update folder when ready
            "fault_type": "disk_stress",
            "metric_label": "Disk throughput (MB/s)",
            "color": "#1c7ed6",
            "planner_reason": "insufficient evidence",
        },
    ],
    # --- ROW 3 ---s
    [
        {
            "title": "CPU Hog",
            "folder": "evaim_rs_cpu_1/llm-robot-shop-cpu_hog-cart-1782497077",  # Update folder when ready
            "fault_type": "cpu_hog",
            "metric_label": "CPU usage / limit (%)",
            "color": "#e03131",
            "planner_reason": "active degradation",
        },
        {
            "title": "Memory Stress",
            "folder": "evaim_rs_mem_1/llm-robot-shop-mem_stress-cart-1782503892",  # Update folder when ready
            "fault_type": "mem_stress",
            "metric_label": "Memory usage / limit (%)",
            "color": "#845ef7",
            "planner_reason": "insufficient evidence",
        },
        {
            "title": "Disk Stress",
            "folder": "evaim_rs_disk_1/llm-robot-shop-disk_stress-cart-1782515579",  # Update folder when ready
            "fault_type": "disk_stress",
            "metric_label": "Disk throughput (MB/s)",
            "color": "#1c7ed6",
            "planner_reason": "insufficient evidence",
        },
    ]
]

# ============================================================
# HELPERS
# ============================================================

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
        ["service_observations", service, group, metric, "values"],
        ["service_observations", service, group, metric, "series"],
    ]
    for p in paths:
        vals = safe_get(metrics, p, None)
        if vals:
            return np.array([float(v) for v in vals if v is not None], dtype=float)
    return np.array([], dtype=float)


def extract_fault_metric(metrics, fault_type, service):
    fault_type = fault_type.lower()
    if fault_type == "cpu_hog":
        vals = find_series(metrics, service, "container_resources", "cpu_usage_to_limit_ratio") * 100.0
    elif fault_type == "mem_stress":
        vals = find_series(metrics, service, "container_resources", "memory_usage_to_limit_ratio") * 100.0
    elif fault_type == "disk_stress":
        read_vals = find_series(metrics, service, "container_resources", "fs_read_bytes_per_sec")
        write_vals = find_series(metrics, service, "container_resources", "fs_write_bytes_per_sec")
        n = min(len(read_vals), len(write_vals))
        vals = (read_vals[:n] + write_vals[:n]) / (1024 * 1024) if n > 0 else np.array([], dtype=float)
    else:
        raise ValueError(f"Unsupported fault type: {fault_type}")
    return vals


def load_phase_metrics(run_dir: Path):
    baseline_path = run_dir / "metrics_baseline.json"
    before_path = run_dir / "metrics_before.json"
    after_path = run_dir / "metrics_after.json"
    if not (baseline_path.exists() and before_path.exists() and after_path.exists()):
        raise FileNotFoundError(f"Missing metrics JSONs in: {run_dir}")
    return load_json(baseline_path), load_json(before_path), load_json(after_path)


# ============================================================
# MAIN 3x3 PLOT GENERATOR
# ============================================================

def generate_rq2_9_subplot_grid(base_dir: Path, grid_config, row_labels):
    # Modern styling profiles
    plt.rcParams['font.family'] = 'sans-serif'
    plt.rcParams['text.color'] = '#212529'
    plt.rcParams['axes.labelcolor'] = '#212529'
    plt.rcParams['xtick.color'] = '#495057'
    plt.rcParams['ytick.color'] = '#495057'

    fig, axes = plt.subplots(3, 3, figsize=(16, 12))
    # Elegant grid structure margins leaving clear breathing room for labels
    fig.subplots_adjust(wspace=0.28, hspace=0.40, bottom=0.10, top=0.90)

    for r_idx in range(3):
        for c_idx in range(3):
            ax = axes[r_idx, c_idx]
            cfg = grid_config[r_idx][c_idx]
            
            run_dir = base_dir / cfg["folder"]
            baseline, before, after = load_phase_metrics(run_dir)
            service = before.get("target_service", "cart")

            baseline_vals = extract_fault_metric(baseline, cfg["fault_type"], service)
            before_vals = extract_fault_metric(before, cfg["fault_type"], service)
            after_vals = extract_fault_metric(after, cfg["fault_type"], service)

            # Fix the anomalous metric baseline drop to 0
            if cfg["fault_type"] == "mem_stress" and len(before_vals) > 0 and before_vals[0] < 5.0:
                before_vals[0] = (baseline_vals[-1] + before_vals[1]) / 2.0

            values = np.concatenate([baseline_vals, before_vals, after_vals])
            x = np.arange(len(values))

            b0 = len(baseline_vals) - 0.5
            b1 = len(baseline_vals) + len(before_vals) - 0.5
            end = len(values) - 0.5

            # ----------------------------------------------------
            # Phase Shading Bands
            # ----------------------------------------------------
            ax.axvspan(-0.5, b0, color="#f4fce3", alpha=0.6, zorder=0)  # Pre-fault
            ax.axvspan(b0, b1, color="#fff0f6", alpha=0.6, zorder=0)   # Fault Observation
            ax.axvspan(b1, end, color="#e7f5ff", alpha=0.6, zorder=0)  # Post-Planner

            # ----------------------------------------------------
            # Metric Line & Stage Benchmarks
            # ----------------------------------------------------
            ax.plot(x, values, color=cfg["color"], marker="o", linewidth=1.8, markersize=4, zorder=3)

            b_mean = float(np.nanmean(baseline_vals))
            f_mean = float(np.nanmean(before_vals))
            a_mean = float(np.nanmean(after_vals))

            ax.hlines(b_mean, xmin=-0.25, xmax=b0, color=cfg["color"], linestyle=":", linewidth=1.4, zorder=2)
            ax.hlines(f_mean, xmin=b0, xmax=b1, color=cfg["color"], linestyle=":", linewidth=1.4, zorder=2)
            ax.hlines(a_mean, xmin=b1, xmax=end, color=cfg["color"], linestyle=":", linewidth=1.4, zorder=2)

            ax.axvline(b0, color="#adb5bd", linestyle="--", linewidth=1.0, zorder=1)
            ax.axvline(b1, color="#adb5bd", linestyle="--", linewidth=1.0, zorder=1)

            # --- Phase Top Labels (Only rendered on the topmost row) ---
            if r_idx == 0:
                ax.text(b0 * 0.5, 0.93, "Pre-fault", transform=ax.get_xaxis_transform(), ha="center", fontsize=7.5, color="#2b8a3e", fontweight="bold")
                ax.text((b0 + b1) * 0.5, 0.93, "Fault Obs.", transform=ax.get_xaxis_transform(), ha="center", fontsize=7.5, color="#c92a2a", fontweight="bold")
                ax.text((b1 + end) * 0.5, 0.93, "Post-Planner", transform=ax.get_xaxis_transform(), ha="center", fontsize=7.5, color="#1c7ed6", fontweight="bold")

            # ----------------------------------------------------
            # Skip Notification Overlay
            # ----------------------------------------------------
            box_y = 0.38 if cfg["fault_type"] == "cpu_hog" else 0.70
            ax.text(
                0.50 if cfg["fault_type"] == "cpu_hog" else 0.68,
                box_y,
                f"Planner Skipped\nReason: {cfg['planner_reason']}",
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=7.5,
                fontweight="bold",
                color="#1c7ed6",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="#ffffff", edgecolor="#74c0fc", linewidth=1.0, alpha=0.85),
            )

            # ----------------------------------------------------
            # Titles & Axis Formatting
            # ----------------------------------------------------
            # Title reflects both specific experiment variant (Row) and Fault Type (Col)
            ax.set_title(f"{row_labels[r_idx]} — {cfg['title']}", fontsize=9.5, fontweight="bold", pad=8)
            ax.set_ylabel(cfg["metric_label"], fontsize=8.5, fontweight="bold")
            
            if r_idx == 2:
                ax.set_xlabel("Timeline Steps", fontsize=8.5)
            
            ax.grid(True, axis="y", linestyle="-", color="#dee2e6", alpha=0.5, zorder=1)
            ax.set_xlim(-0.5, end)
            
            ymax = np.nanmax(values) * 1.35 if np.nanmax(values) > 0 else 1.0
            ax.set_ylim(min(0, np.nanmin(values) * 0.9), ymax)

            ax.set_xticks([0, max(0, len(baseline_vals) - 1), len(baseline_vals), len(baseline_vals) + len(before_vals) - 1, len(values) - 1])
            ax.set_xticklabels(["base", "fault inj.", "obs.", "planner", "after"], fontsize=7.5)

            # Modern Clean Borders
            for spine in ["top", "right"]:
                ax.spines[spine].set_visible(False)
            for spine in ["left", "bottom"]:
                ax.spines[spine].set_color("#ced4da")

    # Unified Global Aesthetics
    fig.suptitle(
        "Ablation Analysis Across Strategies: Fault Degradation Patterns and Mitigations",
        fontsize=15,
        fontweight="bold",
        y=0.96
    )

    # Consolidated global legend centered safely underneath the figure grid
    fig.text(0.24, 0.04, "■ Pre-fault baseline phase", ha="center", fontsize=10, color="#2b8a3e", fontweight="bold")
    fig.text(0.50, 0.04, "■ Fault injection & observation phase", ha="center", fontsize=10, color="#c92a2a", fontweight="bold")
    fig.text(0.76, 0.04, "■ Post-planner evaluation phase", ha="center", fontsize=10, color="#1c7ed6", fontweight="bold")

    out_png = base_dir / "rq2_9_subplot_comprehensive_grid.png"
    plt.savefig(out_png, dpi=300, bbox_inches="tight")
    plt.close()
    print(f"Comprehensive grid generated and saved to: {out_png}")


if __name__ == "__main__":
    generate_rq2_9_subplot_grid(BASE_DIR, GRID_CONFIG, ROW_LABELS)