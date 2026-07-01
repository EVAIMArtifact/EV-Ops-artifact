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

# Ensure numeric types on all required raw metric keys
metric_keys = ["av5_m", "av5_s", "service_MRR_mean", 
               "train_time", "avg_total_infer_time_over_all_tests", "peak_memory_mb", "energy_joules"]
for col in metric_keys:
    data_pd[col] = pd.to_numeric(data_pd[col], errors='coerce').fillna(0.0)

# 3. Group by MSA, Regime, and Model to average over your seeds
group_cols = ["microservice_name", "DUAL_CASE", "model_name"]
agg_dict = {col: ["mean", "std"] for col in metric_keys}
grouped_df = data_pd.groupby(group_cols).agg(agg_dict).reset_index()

# Collapse multi-index columns
grouped_df.columns = [
    f"{col}_{stat}" if stat else col 
    for col, stat in grouped_df.columns
]

# Identify unique microservices
microservices = grouped_df["microservice_name"].unique()

# Configuration Settings
MODEL_ORDER = ["random", "BARO", "timemixerpp", "LFTSAD", "fits", "FreTS", "SFlexRCA_MLP", "EV-RCA (proposed)"]

METRIC_DIRECTIONS = {
    "av5_m": True, "av5_s": True, "service_MRR_mean": True, 
    "train_time": False, "avg_total_infer_time_over_all_tests": False, "peak_memory_mb": False, "energy_joules": False
}

def style_aggregated_metrics(rows, directions):
    """Flags best/second-best styles using the group means, then formats as string with stddev."""
    if not rows:
        return rows
        
    keys = list(directions.keys())
    efficiency_keys = ["train_time", "avg_total_infer_time_over_all_tests", "peak_memory_mb", "energy_joules"]
    
    formatted_val_mappings = []
    for r in rows:
        row_strings = {"model": r["model"]}
        for k in keys:
            mean_v = float(r[f"{k}_mean"])
            std_v = float(r[f"{k}_std"])
            
            prec = ".3f" if "MRR" in k or "Hit" in k else ".2f" if "time" in k or "av5" in k else ".1f"
            std_str = f"{std_v:{prec}}" if not np.isnan(std_v) else "0.00"
            
            row_strings[f"{k}_mean_str"] = f"{mean_v:{prec}}"
            row_strings[f"{k}_std_str"] = std_str
            row_strings[f"{k}_mean_float_rounded"] = round(mean_v, 3 if "MRR" in k or "Hit" in k else 2 if "time" in k or "av5" in k else 1)
        formatted_val_mappings.append(row_strings)

    styles = {}
    for k in keys:
        if k in efficiency_keys:
            filtered_mappings = [r for r in formatted_val_mappings if r["model"] not in ["random", "BARO"]]
        else:
            filtered_mappings = formatted_val_mappings

        if not filtered_mappings:
            styles[k] = {"best": None, "second": None}
            continue

        rounded_vals = np.array([r[f"{k}_mean_float_rounded"] for r in filtered_mappings])
        unique_vals = np.unique(rounded_vals)
        
        if directions[k]:  # Higher is better
            sorted_unique = np.sort(unique_vals)[::-1]
        else:              # Lower is better
            sorted_unique = np.sort(unique_vals)
            
        best_val = sorted_unique[0] if len(sorted_unique) > 0 else None
        second_val = sorted_unique[1] if len(sorted_unique) > 1 else None
        styles[k] = {"best": best_val, "second": second_val}

    for r, f_r in zip(rows, formatted_val_mappings):
        formatted_row = {"model": r["model"]}
        for k in keys:
            if r["model"] in ["random", "BARO"] and k in efficiency_keys:
                formatted_row[k] = "--"
                continue

            mean_str = f_r[f"{k}_mean_str"]
            std_str = f_r[f"{k}_std_str"]
            current_rounded = f_r[f"{k}_mean_float_rounded"]
            
            val_str = f"${mean_str} \\text{{\\tiny $\\pm {std_str}$}}$"
            
            if current_rounded == styles[k]["best"]:
                formatted_row[k] = f"{{\\boldmath {val_str}}}"
            elif current_rounded == styles[k]["second"]:
                formatted_row[k] = f"\\underline{{{val_str}}}"
            else:
                formatted_row[k] = val_str
        r.update(formatted_row)
        
    return rows

# 4. Generate LaTeX tables per microservice
for msa in microservices:
    msa_df = grouped_df[grouped_df["microservice_name"] == msa]
    
    single_raw, dual_raw = [], []
    for _, row in msa_df.iterrows():
        is_dual = str(row["DUAL_CASE"]).strip().lower() == "true"
        processed_row = {"model": str(row["model_name"]).strip()}
        for k in METRIC_DIRECTIONS.keys():
            processed_row[f"{k}_mean"] = row[f"{k}_mean"]
            processed_row[f"{k}_std"] = row[f"{k}_std"]
            
        if is_dual:
            dual_raw.append(processed_row)
        else:
            single_raw.append(processed_row)
            
    single_rows = [r for m in MODEL_ORDER for r in single_raw if r["model"] == m]
    dual_rows = [r for m in MODEL_ORDER for r in dual_raw if r["model"] == m]
    
    single_rows_styled = style_aggregated_metrics(copy.deepcopy(single_rows), METRIC_DIRECTIONS)
    dual_rows_styled = style_aggregated_metrics(copy.deepcopy(dual_rows), METRIC_DIRECTIONS)
            
    clean_label = str(msa).lower().replace("-", "").replace("_", "")
    
    latex_str = []
    latex_str.append(r"\begin{table*}[t]")
    latex_str.append(r"\centering")
    latex_str.append(f"\\caption{{Performance Profile for \\textbf{{{msa}}}. Values denote mean $\\pm$ standard deviation. Best performance is in bold, second best underlined.}}")
    latex_str.append(f"\\label{{tab:rca-evaluation-{clean_label}}}")
    latex_str.append(r"\scriptsize")
    latex_str.append(r"\rowcolors{4}{gray!7}{white}")
    
    # Structural definition with added '|' separator after the RCA Model column
    latex_str.append(r"\begin{tabularx}{\textwidth}{cl|*{3}{>{\centering\arraybackslash}X}|*{4}{>{\centering\arraybackslash}X}}")
    latex_str.append(r"\toprule")
    latex_str.append(
        r" \multirow{2}{*}{\textbf{Regime}} & \multirow{2}{*}{\textbf{RCA Model}} & "
        r"\multicolumn{3}{c}{\textbf{Localization Accuracy Metrics}} & "
        r"\multicolumn{4}{c}{\textbf{Computational Efficiency Metrics}} \\"
    )
    latex_str.append(r" \cmidrule(lr){3-5} \cmidrule(lr){6-9}")
    latex_str.append(
        r" & & \textbf{AV@5 Metr. $\uparrow$} & \textbf{AV@5 Serv. $\uparrow$} & \textbf{Serv. MRR $\uparrow$} "
        r"& \textbf{Train (s) $\downarrow$} & \textbf{Infer. (s) $\downarrow$} & \textbf{Mem (MB) $\downarrow$} & \textbf{Energy (J) $\downarrow$} \\"
    )
    latex_str.append(r"\midrule")
    
    # Process Single injection block
    if single_rows_styled:
        for idx, r in enumerate(single_rows_styled):
            mode_str = "Single" if idx == 2 else ""
            latex_str.append(
                f" {mode_str} & {r['model']} & {r['av5_m']} & {r['av5_s']} & {r['service_MRR_mean']} & {r['train_time']} & {r['avg_total_infer_time_over_all_tests']} & {r['peak_memory_mb']} & {r['energy_joules']} \\\\"
            )
            
    # Process Dual injection block
    if dual_rows_styled:
        latex_str.append(r" \hiderowcolors \midrule \showrowcolors")
        for idx, r in enumerate(dual_rows_styled):
            mode_str = "Dual" if idx == 2 else ""
            latex_str.append(
                f" {mode_str} & {r['model']} & {r['av5_m']} & {r['av5_s']} & {r['service_MRR_mean']} & {r['train_time']} & {r['avg_total_infer_time_over_all_tests']} & {r['peak_memory_mb']} & {r['energy_joules']} \\\\"
            )
            
    latex_str.append(r"\bottomrule")
    latex_str.append(r"\end{tabularx}")
    latex_str.append(r"\end{table*}")
    
    print("\n".join(latex_str))
    print("\n" + "="*80 + "\n")