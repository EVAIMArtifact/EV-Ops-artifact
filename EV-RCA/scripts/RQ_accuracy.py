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

microservices = sorted(grouped_df["microservice_name"].unique())  # e.g., online-boutique, robot-shop, sock-shop
MODEL_ORDER = ["random", "NSigma", "BARO", "timemixerpp", "LFTSAD", "fits", "FreTS", "EV-RCA (proposed)"]

def style_accuracy_block(rows_dict):
    """
    Expects rows_dict: { model_name: { msa_key: {metric_mean, metric_std} } }
    Applies bold/underline highlighting within each microservice group independently.
    """
    styled_outputs = {m: {"model": m} for m in MODEL_ORDER}
    
    for msa in microservices:
        # Find best and second best per MSA
        for k in accuracy_keys:
            vals = []
            for m in MODEL_ORDER:
                if m in rows_dict and msa in rows_dict[m]:
                    mean_v = round(float(rows_dict[m][msa][f"{k}_mean"]), 3 if "MRR" in k else 2)
                    vals.append(mean_v)
            
            unique_vals = np.sort(np.unique(vals))[::-1]  # Higher is better for all accuracy metrics
            best_val = unique_vals[0] if len(unique_vals) > 0 else None
            second_val = unique_vals[1] if len(unique_vals) > 1 else None
            
            for m in MODEL_ORDER:
                if m not in rows_dict or msa not in rows_dict[m]:
                    styled_outputs[m][f"{msa}_{k}"] = "--"
                    continue
                
                mean_v = float(rows_dict[m][msa][f"{k}_mean"])
                std_v = float(rows_dict[m][msa][f"{k}_std"])
                prec = ".3f" if "MRR" in k else ".2f"
                
                current_rounded = round(mean_v, 3 if "MRR" in k else 2)
                val_str = f"${mean_v:{prec}} \\text{{\\tiny $\\pm {std_v:{prec}}$}}$"
                
                if current_rounded == best_val:
                    styled_outputs[m][f"{msa}_{k}"] = f"{{\\boldmath {val_str}}}"
                elif current_rounded == second_val:
                    styled_outputs[m][f"{msa}_{k}"] = f"\\underline{{{val_str}}}"
                else:
                    styled_outputs[m][f"{msa}_{k}"] = val_str
                    
    return [styled_outputs[m] for m in MODEL_ORDER]

# Separate out single vs dual cases structurally
single_data_tree = {m: {} for m in MODEL_ORDER}
dual_data_tree = {m: {} for m in MODEL_ORDER}

for _, row in grouped_df.iterrows():
    m_name = str(row["model_name"]).strip()
    if m_name not in MODEL_ORDER: continue
    msa_name = str(row["microservice_name"]).strip()
    is_dual = str(row["DUAL_CASE"]).strip().lower() == "true"
    
    target_tree = dual_data_tree if is_dual else single_data_tree
    target_tree[m_name][msa_name] = {f"{k}_{stat}": row[f"{k}_{stat}"] for k in accuracy_keys for stat in ["mean", "std"]}

single_styled = style_accuracy_block(single_data_tree)
dual_styled = style_accuracy_block(dual_data_tree)

# 4. Generate the Unified LateX String
latex_str = []
latex_str.append(r"\begin{table*}[t]")
latex_str.append(r"\centering")
latex_str.append(r"\caption{Consolidated Localization Accuracy Profile. Values denote mean $\pm$ standard deviation. Best performance is in bold, second best underlined.}")
latex_str.append(r"\label{tab:unified-accuracy}")
latex_str.append(r"\scriptsize")
latex_str.append(r"\rowcolors{4}{gray!7}{white}")

# Column config: Regime, Model, plus 3 columns per each of the 3 microservices = 11 columns total
latex_str.append(r"\begin{tabularx}{\textwidth}{cl|*{3}{>{\centering\arraybackslash}X}|*{3}{>{\centering\arraybackslash}X}|*{3}{>{\centering\arraybackslash}X}}")
latex_str.append(r"\toprule")

# Top Level Headers (Microservices)
msa_headers = " & ".join([f"\\multicolumn{{3}}{{c}}{{\\textbf{{{msa.upper()}}}}}" for msa in microservices])
latex_str.append(f" \\multirow{{2}}{{*}}{{\\textbf{{Regime}}}} & \\multirow{{2}}{{*}}{{\\textbf{{RCA Model}}}} & {msa_headers} \\\\")

# Mid level rules
latex_str.append(r" \cmidrule(lr){3-5} \cmidrule(lr){6-8} \cmidrule(lr){9-11}")

# Metrics subheaders repeating per benchmark
subheaders = " & ".join([r"\textbf{AV@5 M.} & \textbf{AV@5 S.} & \textbf{MRR}" for _ in microservices])
latex_str.append(f" & & {subheaders} \\\\")
latex_str.append(r"\midrule")

# Build rows helper
def append_rows(styled_source, regime_label):
    for idx, r in enumerate(styled_source):
        mode_str = regime_label if idx == 2 else ""
        row_cells = [mode_str, r["model"]]
        for msa in microservices:
            row_cells.extend([r[f"{msa}_av5_m"], r[f"{msa}_av5_s"], r[f"{msa}_service_MRR_mean"]])
        latex_str.append(" " + " & ".join(row_cells) + r" \\")

append_rows(single_styled, "Single")
latex_str.append(r" \hiderowcolors \midrule \showrowcolors")
append_rows(dual_styled, "Concurrent")

latex_str.append(r"\bottomrule")
latex_str.append(r"\end{tabularx}")
latex_str.append(r"\end{table*}")

print("\n".join(latex_str))