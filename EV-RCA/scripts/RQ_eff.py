import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import ast
import io
import pandas as pd
import numpy as np
import copy

# 1. Load the full dataset
data_pd = pd.read_csv("out/final_results_25June_COMPLETE_FAULTS.csv")

# Clean filter based on your file criteria
data_pd = data_pd[~((data_pd["model_name"] == "SFlexRCA") &
                    ((data_pd["orth_residual"] != "simple") | \
                     (data_pd["orth_type"] != "fixed") | \
                     (data_pd["temporal_type"] != "mlp")))]
data_pd.loc[data_pd["model_name"] == "SFlexRCA", "model_name"] = "EV-RCA (proposed)"

# 2. Extract metrics from 'avg_recall' string before grouping
def parse_avg_recall(row):
    try:
        recall_dict = ast.literal_eval(str(row["avg_recall"]).strip())
    except:
        recall_dict = {}
    
    is_dual = str(row["DUAL_CASE"]).strip().lower() == "true"
    if is_dual:
        av5_s = recall_dict.get("Avg@5-DUAL_MACRO (Service)", 0.0)
        av5_m = recall_dict.get("Avg@5-DUAL_MACRO (Metric)", 0.0)
    else:
        av5_s = recall_dict.get("Avg@5-OVERALL (Service)", 0.0)
        av5_m = recall_dict.get("Avg@5-OVERALL (Metric)", 0.0)
    return pd.Series([av5_s, av5_m])

data_pd[["av5_s", "av5_m"]] = data_pd.apply(parse_avg_recall, axis=1)

# Ensure numeric types on accuracy metrics only
accuracy_keys = ["av5_m", "av5_s", "service_MRR_mean"]
for col in accuracy_keys:
    data_pd[col] = pd.to_numeric(data_pd[col], errors='coerce').fillna(0.0)

# 3. Group by MSA, Regime, and Model
group_cols = ["microservice_name", "DUAL_CASE", "model_name"]
agg_dict = {col: ["mean", "std"] for col in accuracy_keys}
grouped_df = data_pd.groupby(group_cols).agg(agg_dict).reset_index()

grouped_df.columns = [f"{col}_{stat}" if stat else col for col, stat in grouped_df.columns]

# Filter out random and BARO since they lack resource footprint records
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# 1. Setup professional styling defaults for academic publishing
plt.rcParams.update({
    "font.family": "serif",       # Matches standard LaTeX ACM/IEEE font styles
    "font.size": 11,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "figure.titlesize": 13,
    "lines.linewidth": 1.2        # Globally sets error bar line thickness safely
})

# Filter out random and BARO since they lack resource footprint records
eff_df = data_pd[~data_pd["model_name"].isin(["random", "BARO"])].copy()

# Shortened titles for sleek column fitting
metric_map = {
    "train_time": "Train Time (s)",
    "avg_total_infer_time_over_all_tests": "Infer Time (s)",
    "peak_memory_mb": "Mem (MB)",
    "energy_joules": "Energy (J)"
}

# Map long names to clean abbreviations to eliminate text crowding
eff_df["msa_short"] = eff_df["microservice_name"].replace({
    "online-boutique": "OB",
    "robot-shop": "RS",
    "sock-shop": "SS"
})

# 2. Create the 1x4 layout
fig, axes = plt.subplots(1, 4, figsize=(16, 4), sharex=True) 

# Cohesive, professionally curated academic palette
academic_palette = sns.color_palette("muted", n_colors=len(eff_df["model_name"].unique()))

for idx, (raw_col, clean_label) in enumerate(metric_map.items()):
    ax = axes[idx]
    
    # Draw bars with safe parameters compatible across older Seaborn versions
    sns.barplot(
        data=eff_df,
        x="msa_short",
        y=raw_col,
        hue="model_name",
        ax=ax,
        errorbar="sd",
        capsize=0.1,                                      # Draws clean error caps
        palette=academic_palette,
        edgecolor="#ffffff",                              # Adds a clean white border to bars
        linewidth=0.8
    )
    
    # Stylize titles and gridlines
    ax.set_title(clean_label, fontsize=12, fontweight='bold', pad=10, color='#333333')
    ax.set_ylabel("")
    ax.set_xlabel("")
    
    # Add light horizontal grids ONLY to avoid visual clutter
    ax.set_axisbelow(True)
    ax.grid(axis='y', linestyle=':', linewidth=0.6, color='#cccccc', alpha=0.7)
    
    # Strip unnecessary spines (top/right/bottom frame lines) for a modern look
    sns.despine(ax=ax, top=True, right=True, left=False, bottom=False)
    ax.spines['left'].set_color('#888888')
    ax.spines['bottom'].set_color('#888888')
    
    # Apply log scale for metrics with massive scaling gaps
    if raw_col in ["train_time", "energy_joules"]:
        ax.set_yscale("log")
        
    # Completely remove internal sub-legends
    ax.get_legend().remove()

# 3. Create a single, polished horizontal legend across the top
handles, labels = axes[0].get_legend_handles_labels()
fig.legend(
    handles, 
    labels, 
    loc='upper center', 
    bbox_to_anchor=(0.5, 1.15),   # Perfectly positioned right above the titles
    ncol=len(labels),            # Dynamically adjusts columns horizontally based on models
    frameon=False,               # Removes the box frame for a cleaner look
    fontsize=10.5,
    columnspacing=1.5            # Adds comfortable breathing room between model names
)

# Optimize spacing tight against borders
plt.tight_layout()

# Save with tight bounding boxes to ensure the legend isn't clipped out
plt.savefig("out/efficiency_profile_final.pdf",bbox_inches='tight')
plt.show()