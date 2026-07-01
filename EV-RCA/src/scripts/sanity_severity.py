import pandas as pd
import numpy as np


def classify_severity(effect_size):
    """
    Effect size = |fault_mean - baseline_mean| / baseline_std
    """

    if effect_size < 0.5:
        return "Too Weak"
    elif effect_size < 1.5:
        return "Good RCA Difficulty"
    elif effect_size < 3:
        return "Easy"
    elif effect_size < 5:
        return "Very Easy"
    else:
        return "Trivial / Saturated"


def analyze_fault_csv(
    csv_file,
    fault_start_idx,
    fault_end_idx,
    metric_columns=None,
):
    """
    fault_start_idx : first fault sample
    fault_end_idx   : first sample AFTER fault

    Example:
        analyze_fault_csv(
            "cpu_hog.csv",
            fault_start_idx=30,
            fault_end_idx=40
        )
    """

    df = pd.read_csv(csv_file)

    if metric_columns is None:
        metric_columns = [
            c
            for c in df.columns
            if c.lower() != "timestamp"
        ]

    results = []

    for col in metric_columns:

        x = pd.to_numeric(df[col], errors="coerce")
        x = x.dropna()

        baseline = x.iloc[:fault_start_idx]
        fault = x.iloc[fault_start_idx:fault_end_idx]

        if len(baseline) < 5 or len(fault) < 2:
            continue

        baseline_mean = baseline.mean()
        baseline_std = baseline.std() + 1e-8

        fault_mean = fault.mean()

        effect_size = abs(
            fault_mean - baseline_mean
        ) / baseline_std

        peak_shift = (
            np.max(np.abs(fault - baseline_mean))
            / baseline_std
        )

        results.append(
            {
                "metric": col,
                "baseline_mean": baseline_mean,
                "fault_mean": fault_mean,
                "effect_size": effect_size,
                "peak_shift": peak_shift,
                "severity": classify_severity(effect_size),
            }
        )

    results_df = pd.DataFrame(results)

    if len(results_df) == 0:
        print("No valid metrics found.")
        return

    print("\n========================")
    print("PER-METRIC SEVERITY")
    print("========================")

    # save to csv
    results_df.sort_values(
            "effect_size",
            ascending=False,
        ).to_csv("sanity_severity_results.csv", index=False)
    print(
        results_df[
            [
                "metric",
                "effect_size",
                "peak_shift",
                "severity",
            ]
        ].sort_values(
            "effect_size",
            ascending=False,
        )
    )

    overall_effect = results_df["effect_size"].median()
    overall_peak = results_df["peak_shift"].median()

    print("\n========================")
    print("SUMMARY")
    print("========================")

    print(f"Median Effect Size : {overall_effect:.2f}")
    print(f"Median Peak Shift  : {overall_peak:.2f}")

    if overall_effect < 0.5:
        verdict = """
DATASET TOO HARD

Most faults are buried in natural noise.
Many models will fail.
"""
    elif overall_effect < 1.5:
        verdict = """
GOOD RCA REGIME

Faults are detectable but not obvious.
Good for comparing forecasting models.
"""
    elif overall_effect < 3:
        verdict = """
EASY RCA REGIME

Strong signal.
Useful benchmark but weaker models may already perform well.
"""
    elif overall_effect < 5:
        verdict = """
VERY EASY RCA REGIME

Many models will saturate.
Expect high Hit@1.
"""
    else:
        verdict = """
TRIVIAL / SATURATED REGIME

Faults are extremely obvious.
Even simple baselines may achieve near-perfect rankings.

Recommendation:
- reduce injection duration
- reduce injection magnitude
- add temporal variability
- add propagation delays
"""
    print(verdict)

    return results_df




if __name__ == "__main__":
    analyze_fault_csv(
        "src/data/2min_17June_withclearning/14june_web_cpu_hog.csv",
        fault_start_idx=87,
        fault_end_idx=129
    )

