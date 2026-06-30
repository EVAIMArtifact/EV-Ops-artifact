#!/usr/bin/env python3
"""
Power Analysis for AIM-EVM Thesis Experiments

This script calculates required sample sizes for detecting meaningful effects
in the LLM-driven remediation experiments.

Author: Anonymous
Date: 2026
"""

import numpy as np
from scipy import stats
from typing import Tuple, Dict, Any
import json


def cohens_d_to_sample_size(
    d: float,
    alpha: float = 0.05,
    power: float = 0.80,
    two_tailed: bool = True
) -> int:
    """
    Calculate required sample size per group for two-sample t-test.

    Args:
        d: Cohen's d effect size (0.2=small, 0.5=medium, 0.8=large)
        alpha: Significance level
        power: Desired statistical power
        two_tailed: Whether to use two-tailed test

    Returns:
        Required sample size per group
    """
    from scipy.stats import norm

    # Z-scores for alpha and power
    if two_tailed:
        z_alpha = norm.ppf(1 - alpha / 2)
    else:
        z_alpha = norm.ppf(1 - alpha)
    z_beta = norm.ppf(power)

    # Sample size formula for two-sample t-test
    n = 2 * ((z_alpha + z_beta) / d) ** 2

    return int(np.ceil(n))


def power_analysis_for_evs() -> Dict[str, Any]:
    """
    Power analysis for EVS (Execution Validity Score) comparisons.

    EVS is binary (0 or 1), so we use proportion comparison.
    """
    # Expected proportions based on literature and preliminary data
    p_baseline = 0.30  # Baseline remediation success rate (estimate)
    p_treatment = 0.60  # Expected treatment success rate (2x improvement)

    # Effect size for proportions (Cohen's h)
    h = 2 * np.arcsin(np.sqrt(p_treatment)) - 2 * np.arcsin(np.sqrt(p_baseline))

    # Sample size calculation
    alpha = 0.05
    power = 0.80

    from scipy.stats import norm
    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)

    # Sample size for proportion comparison
    p_avg = (p_baseline + p_treatment) / 2
    n = ((z_alpha * np.sqrt(2 * p_avg * (1 - p_avg)) +
          z_beta * np.sqrt(p_baseline * (1 - p_baseline) + p_treatment * (1 - p_treatment))) ** 2) / \
        ((p_treatment - p_baseline) ** 2)

    return {
        "metric": "EVS (binary success rate)",
        "baseline_expected": p_baseline,
        "treatment_expected": p_treatment,
        "effect_size_h": round(h, 3),
        "effect_size_interpretation": "large" if abs(h) >= 0.8 else "medium" if abs(h) >= 0.5 else "small",
        "alpha": alpha,
        "power": power,
        "required_n_per_group": int(np.ceil(n)),
        "total_experiments_needed": int(np.ceil(n)) * 2,
        "recommendation": f"Run at least {int(np.ceil(n))} experiments per condition"
    }


def power_analysis_for_ttr() -> Dict[str, Any]:
    """
    Power analysis for TTR (Time To Remediation) comparisons.

    TTR is continuous, use t-test assumptions.
    """
    # Based on preliminary data
    ttr_baseline_mean = 48.29  # seconds (from post-mortem mode)
    ttr_treatment_mean = 36.67  # seconds (from reactive mode)
    ttr_pooled_std = 15.0  # estimated standard deviation

    # Cohen's d
    d = abs(ttr_baseline_mean - ttr_treatment_mean) / ttr_pooled_std

    n = cohens_d_to_sample_size(d, alpha=0.05, power=0.80)

    return {
        "metric": "TTR (Time To Remediation)",
        "baseline_mean": ttr_baseline_mean,
        "treatment_mean": ttr_treatment_mean,
        "pooled_std_estimate": ttr_pooled_std,
        "cohens_d": round(d, 3),
        "effect_size_interpretation": "large" if d >= 0.8 else "medium" if d >= 0.5 else "small",
        "required_n_per_group": n,
        "total_experiments_needed": n * 2,
        "recommendation": f"Run at least {n} experiments per condition"
    }


def power_analysis_for_mu() -> Dict[str, Any]:
    """
    Power analysis for MU (Mitigation Utility) comparisons.

    MU ranges from -1 to 1, treat as continuous.
    """
    # Expected values
    mu_baseline = -0.1  # Slight degradation expected with no/poor remediation
    mu_treatment = 0.3  # Moderate improvement expected with good remediation
    mu_std = 0.4  # Estimated standard deviation

    d = abs(mu_treatment - mu_baseline) / mu_std
    n = cohens_d_to_sample_size(d, alpha=0.05, power=0.80)

    return {
        "metric": "MU (Mitigation Utility)",
        "baseline_mean": mu_baseline,
        "treatment_mean": mu_treatment,
        "pooled_std_estimate": mu_std,
        "cohens_d": round(d, 3),
        "effect_size_interpretation": "large" if d >= 0.8 else "medium" if d >= 0.5 else "small",
        "required_n_per_group": n,
        "total_experiments_needed": n * 2,
        "recommendation": f"Run at least {n} experiments per condition"
    }


def sample_size_for_rqs() -> Dict[str, Dict[str, Any]]:
    """
    Calculate sample sizes needed to answer each research question.
    """
    return {
        "RQ1_execution_vs_plan_only": {
            "comparison": "LLM execution vs. plan-only",
            "conditions": ["full_execution", "plan_only"],
            "primary_metric": "EVS",
            "secondary_metrics": ["TTR", "MU"],
            "min_n_per_condition": 30,
            "recommended_n_per_condition": 50,
            "justification": "30 provides 80% power for large effects; 50 for medium effects",
            "total_experiments": 100
        },
        "RQ2_feedback_learning": {
            "comparison": "With feedback vs. without feedback",
            "conditions": ["with_feedback", "without_feedback"],
            "primary_metric": "EVS improvement over time",
            "design": "longitudinal with learning curve analysis",
            "min_trials_per_condition": 20,
            "min_iterations": 5,
            "recommended_n_per_condition": 30,
            "justification": "Learning curves require multiple sequential observations",
            "total_experiments": 150  # 30 trials x 5 iterations
        },
        "RQ3_dual_llm_architecture": {
            "comparison": "Single LLM vs. Planning+Execution LLMs",
            "conditions": ["single_llm", "dual_llm"],
            "primary_metric": "Execution failure rate",
            "secondary_metrics": ["ansible_score", "EVS"],
            "min_n_per_condition": 30,
            "recommended_n_per_condition": 40,
            "justification": "Failure rates may be low, need sufficient n for rare events",
            "total_experiments": 80
        }
    }


def handling_partial_failures() -> Dict[str, Any]:
    """
    Recommendations for handling partial execution failures in analysis.
    """
    return {
        "analysis_approaches": {
            "intent_to_treat": {
                "description": "Include all experiments regardless of execution success",
                "pros": ["Realistic estimate of system performance", "No selection bias"],
                "cons": ["Conflates planning and execution quality"],
                "when_to_use": "Primary analysis for real-world effectiveness"
            },
            "per_protocol": {
                "description": "Include only experiments with ansible_score = 1.0",
                "pros": ["Clean estimate of planning quality", "Isolates LLM contribution"],
                "cons": ["May overestimate real-world performance", "Reduced sample size"],
                "when_to_use": "Secondary analysis for planning quality assessment"
            },
            "stratified": {
                "description": "Analyze separately by execution success level",
                "pros": ["Detailed understanding of failure modes", "Identifies improvement areas"],
                "cons": ["Complex reporting", "Subgroup sizes may be small"],
                "when_to_use": "Exploratory analysis and failure characterization"
            }
        },
        "recommended_approach": {
            "primary": "intent_to_treat",
            "secondary": "per_protocol",
            "exploratory": "stratified",
            "reporting": "Report all three with clear labeling"
        },
        "partial_failure_handling": {
            "ansible_score_threshold": 0.8,
            "description": "Experiments with ansible_score >= 0.8 considered 'substantially executed'",
            "sensitivity_analysis": "Vary threshold from 0.6 to 1.0 and report stability of conclusions"
        }
    }


def minimum_viable_thesis() -> Dict[str, Any]:
    """
    Calculate minimum experiments needed for a defensible thesis.
    """
    evs_analysis = power_analysis_for_evs()
    ttr_analysis = power_analysis_for_ttr()
    rq_requirements = sample_size_for_rqs()

    return {
        "minimum_viable": {
            "per_fault_type": 15,
            "fault_types": 3,  # CPU stress, memory stress, pod crash
            "per_condition": 2,  # reactive vs baseline
            "total_minimum": 15 * 3 * 2,  # 90 experiments
            "justification": "Provides preliminary evidence, may lack power for small effects"
        },
        "recommended": {
            "per_fault_type": 25,
            "fault_types": 3,
            "per_condition": 2,
            "total_recommended": 25 * 3 * 2,  # 150 experiments
            "justification": "80% power for medium effects, sufficient for thesis defense"
        },
        "ideal_for_publication": {
            "per_fault_type": 40,
            "fault_types": 4,  # Add network partition
            "per_condition": 3,  # Add rule-based baseline
            "total_ideal": 40 * 4 * 3,  # 480 experiments
            "justification": "Publication-quality with multiple baselines and fault types"
        },
        "current_progress": {
            "completed": 1,
            "successful_execution": 0,
            "percentage_of_minimum": 1.1,
            "percentage_of_recommended": 0.7,
            "remaining_minimum": 89,
            "remaining_recommended": 149
        }
    }


if __name__ == "__main__":
    print("=" * 70)
    print("POWER ANALYSIS FOR AIM-EVM THESIS EXPERIMENTS")
    print("=" * 70)

    print("\n1. EVS (Execution Validity Score) Power Analysis:")
    print("-" * 50)
    evs = power_analysis_for_evs()
    for k, v in evs.items():
        print(f"   {k}: {v}")

    print("\n2. TTR (Time To Remediation) Power Analysis:")
    print("-" * 50)
    ttr = power_analysis_for_ttr()
    for k, v in ttr.items():
        print(f"   {k}: {v}")

    print("\n3. MU (Mitigation Utility) Power Analysis:")
    print("-" * 50)
    mu = power_analysis_for_mu()
    for k, v in mu.items():
        print(f"   {k}: {v}")

    print("\n4. Sample Size Requirements by Research Question:")
    print("-" * 50)
    rqs = sample_size_for_rqs()
    for rq, details in rqs.items():
        print(f"\n   {rq}:")
        for k, v in details.items():
            print(f"      {k}: {v}")

    print("\n5. Handling Partial Failures:")
    print("-" * 50)
    partial = handling_partial_failures()
    print(json.dumps(partial, indent=2))

    print("\n6. Minimum Viable Thesis Requirements:")
    print("-" * 50)
    mvt = minimum_viable_thesis()
    print(json.dumps(mvt, indent=2))

    print("\n" + "=" * 70)
    print("SUMMARY RECOMMENDATIONS")
    print("=" * 70)
    print("""
    1. IMMEDIATE: Run at least 30 more experiments per condition (reactive vs baseline)
    2. SHORT-TERM: Achieve 90 total experiments (minimum viable)
    3. THESIS-READY: Achieve 150 total experiments (recommended)
    4. PUBLICATION: Consider 480 experiments with additional baselines

    CRITICAL: Fix Jinja2 type issue before continuing experiments
             Current data with execution failures has limited value for RQ1
    """)
