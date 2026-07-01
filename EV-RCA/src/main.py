import glob
import argparse
from collections import defaultdict
from os.path import basename, exists, join

import torch

from configs import SEQ_LEN, RESULT_PATH, OUT_PATH, SEED_NUM, NUM_USERS 
from utils.benchmark.evaluation import Evaluator
from utils.classes.graph import Node

from configs import FITSConfig, TimeMixerRCAConfig, LFTSADConfig, SFlexRCAConfig, FreTSConfig

from models.Fits import Model as FITS
from models.timemixerpp import Model as TimeMixerPP
from models.IdentityModel import IdentityModel
from models.MeanModel import MeanModel
from models.RandomMode import RandomModel
from models.BaroModel import BaroModel
from models.NSigmaModel import NsigmaModel
from models.RMDnet import RMDnet
from models.LFTSAD import Model as LFTSAD
from models.SFlexRCA import Model as SFlexRCA, OrthTransform
from models.FreTS import Model as FreTS

from utils.utils import load_json, summarize, reciprocal_rank, hit_at_k, ndcg_at_k, rank_position, set_seed

# ---------------------------
# RUN ALL EXPERIMENTS
# ---------------------------
def run_all(model_name):

    train_file = f"data/{MICRO_SERVICE}/Normal_data_23June_{NUM_USERS}_users_180_minutes.csv"
    dual = DUAL_CASE
    test_files = {}
    import os
    for fname in os.listdir(f"src/data/{MICRO_SERVICE}/{INJECTION_DIRECTORY}"):
        if fname.startswith("14june") and fname.endswith(".csv"):
            key = fname.replace(".csv", "")
            test_files[key] = os.path.join(f"src/data/{MICRO_SERVICE}/{INJECTION_DIRECTORY}", fname)

    # FEATURE NAMES
    import pandas as pd
    train = pd.read_csv(train_file)
    tests = [(name, pd.read_csv(path)) for name, path in test_files.items()]

    # LOAD DATA
    print("Loading data...")
    if exists(RESULT_PATH):
        import shutil
        shutil.rmtree(RESULT_PATH)
        os.makedirs(RESULT_PATH, exist_ok=True)
        
    if model_name == "fits":
        configs = FITSConfig()
        model_arch = FITS
    elif model_name == "timemixerpp":
        configs = TimeMixerRCAConfig()
        model_arch = TimeMixerPP
    elif model_name == "identity":
        configs = TimeMixerRCAConfig()
        model_arch = IdentityModel
    elif model_name == "mean":
        configs = TimeMixerRCAConfig()
        model_arch = MeanModel
    elif model_name == "random":
        configs = TimeMixerRCAConfig()
        model_arch = RandomModel
    elif model_name == "LFTSAD":
        configs = LFTSADConfig()
        model_arch = LFTSAD
    elif model_name == "SFlexRCA":
        configs = SFlexRCAConfig()  
        configs.orth_type = ORTH_TYPE
        configs.orth_residual = ORTH_RESIDUAL
        configs.temporal_type = TEMPORAL_TYPE
        model_arch = SFlexRCA
    elif model_name == "FreTS":
        configs = FreTSConfig()  
        model_arch = FreTS
    elif model_name == "BARO":
        configs = SFlexRCAConfig()  #placeholder, not used for BARO
        model_arch = BaroModel
    elif model_name == "NSigma":
        configs = SFlexRCAConfig()  #placeholder, not used for NSigma
        model_arch = NsigmaModel

    out = RMDnet(
        data=train,
        testdata=tests,
        dataset="custom",
        model_class=model_arch,
        model_config=configs
    )
    print("run successful, results:")

    # ======== EVALUATION ===========
    rps = glob.glob(join(RESULT_PATH, "*.json"))
    services = sorted(list(set([basename(x).split("_")[0] for x in rps])))
    if dual:
        faults = sorted(
            list(
                set(
                    [
                        tuple(basename(x).replace(".json", "").split("_")[1:])
                        for x in rps
                    ]
                )
            )
        )
    else:
        faults = sorted(list(set([basename(x).replace(".json", "").split("_")[1] for x in rps])))

    eval_data = {
        "service-fault": [],
        "top_1_service": [],
        "top_3_service": [],
        "top_5_service": [],
        "avg@5_service": [],
        "top_1_metric": [],
        "top_3_metric": [],
        "top_5_metric": [],
        "avg@5_metric": [],
    }
    
    if dual:
        for service in services:
            for fault in faults:
                eval_data[f"DUAL_{fault}_service_MRR"] = []
                eval_data[f"DUAL_{fault}_service_Hit@5"] = []
                eval_data[f"DUAL_{service}_{fault}_metric_MRR"] = []
                eval_data[f"DUAL_{service}_{fault}_metric_Hit@5"] = []

    service_metrics = defaultdict(list)
    metric_metrics = defaultdict(list)
    service_metrics_by_fault = defaultdict(lambda: defaultdict(list))
    metric_metrics_by_fault = defaultdict(lambda: defaultdict(list))
    
    # ------------------------------------------------------------
    # DYNAMIC REGISTRY REPLACEMENT FOR EXPERIMENT-WIDE TRACKING
    # ------------------------------------------------------------
    fault_s_evaluators = defaultdict(Evaluator)
    fault_f_evaluators = defaultdict(Evaluator)
    s_evaluator_all = Evaluator()
    f_evaluator_all = Evaluator()

    for service in services:
        for fault in faults:
            s_evaluator = Evaluator()
            f_evaluator = Evaluator()
            dual_hit5_scores = []  
            
            for rp in rps:
                parts = basename(rp).split("_")
                parts = [p.replace(".json", "") for p in parts]
                if dual:
                    s, *m_parts = parts[:1 + len(fault)]
                    m = tuple(m_parts)
                    if s != service or set(m) != set(fault):
                        continue
                else:
                    s, m = parts[:2]
                    if s != service or m != fault:
                        continue

                data = load_json(rp)
                if "error" in data:
                    continue
                
                for i, ranks in data.items():
                    s_ranks = [Node(x.split("_")[0].replace("-db", ""), "unknown") for x in ranks]
                    old_s_ranks = s_ranks.copy()
                    s_ranks = (
                        [old_s_ranks[0]]
                        + [
                            old_s_ranks[i]
                            for i in range(1, len(old_s_ranks))
                            if old_s_ranks[i] not in old_s_ranks[:i]
                        ]
                        if old_s_ranks
                        else []
                    )

                    METRIC_TO_FAULT_CATEGORY = {
                        "cpu": "cpu",
                        "cpu_throttle": "cpu",
                        "cpu_usage": "cpu",
                        "mem": "mem",
                        "memory": "mem",
                        "mem_usage": "mem",
                        "net": "net",
                        "net_tx": "net",
                        "net_rx": "net",
                        "transmit": "net",
                        "disk": "disk",
                        "diskio": "disk",
                        "disk_read": "disk",
                        "disk_write": "disk",
                    }

                    f_ranks = []
                    for x in ranks:
                        parts_x = x.split("_")
                        service_name = parts_x[0]
                        raw_metric = "_".join(parts_x[1:]) if len(parts_x) > 1 else "unknown"
                        
                        mapped_fault = "unknown"
                        for keyword, category in METRIC_TO_FAULT_CATEGORY.items():
                            if keyword in raw_metric:
                                mapped_fault = category
                                break
                        f_ranks.append(Node(service_name, mapped_fault))

                    print(f"DEBUG | Service='{service}' Fault='{fault}'")

                    s_evaluator.add_case(ranks=s_ranks, answer=Node(service, "unknown"))

                    service_answer = Node(service, "unknown")
                    if dual:
                        metric_answers = [Node(service, f) for f in fault]
                    else:
                        metric_answers = [Node(service, fault)]

                    mrr = reciprocal_rank(s_ranks, service_answer)
                    hit1 = hit_at_k(s_ranks, service_answer, 1)
                    hit3 = hit_at_k(s_ranks, service_answer, 3)
                    hit5 = hit_at_k(s_ranks, service_answer, 5)
                    ndcg5 = ndcg_at_k(s_ranks, service_answer, 5)
                    rank = rank_position(s_ranks, service_answer)
#
                    service_metrics["MRR"].append(mrr)
                    service_metrics["Hit@1"].append(hit1)
                    service_metrics["Hit@3"].append(hit3)
                    service_metrics["Hit@5"].append(hit5)
                    service_metrics["NDCG@5"].append(ndcg5)
                    service_metrics["Rank"].append(rank)
#
                    service_metrics_by_fault[fault]["MRR"].append(mrr)
                    service_metrics_by_fault[fault]["Hit@1"].append(hit1)
                    service_metrics_by_fault[fault]["Hit@3"].append(hit3)
                    service_metrics_by_fault[fault]["Hit@5"].append(hit5)
                    service_metrics_by_fault[fault]["NDCG@5"].append(ndcg5)
                    service_metrics_by_fault[fault]["Rank"].append(rank)

                    if dual:
                        def dual_avg(k):
                            return sum(hit_at_k(f_ranks, ans, k) for ans in metric_answers) / len(metric_answers)
                        def dual_mrr():
                            return sum(reciprocal_rank(f_ranks, ans) for ans in metric_answers) / len(metric_answers)
                        def dual_ndcg(k=5):
                            return sum(ndcg_at_k(f_ranks, ans, k) for ans in metric_answers) / len(metric_answers)
                        def dual_rank():
                            return sum(rank_position(f_ranks, ans) for ans in metric_answers) / len(metric_answers)

                        mrr = dual_mrr()
                        hit1 = dual_avg(1)
                        hit3 = dual_avg(3)
                        hit5 = dual_avg(5)
                        ndcg5 = dual_ndcg(5)
                        rank = dual_rank()
                        dual_hit5_scores.append(int(all(hit_at_k(f_ranks, ans, 5) for ans in metric_answers)))
                    else:
                        mrr = reciprocal_rank(f_ranks, metric_answers[0])
                        hit1 = hit_at_k(f_ranks, metric_answers[0], 1)
                        hit3 = hit_at_k(f_ranks, metric_answers[0], 3)
                        hit5 = hit_at_k(f_ranks, metric_answers[0], 5)
                        ndcg5 = ndcg_at_k(f_ranks, metric_answers[0], 5)
                        rank = rank_position(f_ranks, metric_answers[0])

                    metric_metrics["MRR"].append(mrr)
                    metric_metrics["Hit@1"].append(hit1)
                    metric_metrics["Hit@3"].append(hit3)
                    metric_metrics["Hit@5"].append(hit5)
                    metric_metrics["NDCG@5"].append(ndcg5)
                    metric_metrics["Rank"].append(rank)
#
                    # NEW
                    metric_metrics_by_fault[fault]["MRR"].append(mrr)
                    metric_metrics_by_fault[fault]["Hit@1"].append(hit1)
                    metric_metrics_by_fault[fault]["Hit@3"].append(hit3)
                    metric_metrics_by_fault[fault]["Hit@5"].append(hit5)
                    metric_metrics_by_fault[fault]["NDCG@5"].append(ndcg5)
                    metric_metrics_by_fault[fault]["Rank"].append(rank)

                    # =======================================================
                    # FULLY DYNAMIC TARGET FAULT REGISTRY ENGINE
                    # =======================================================
                    current_components = fault if isinstance(fault, tuple) else (fault,)

                    # Log master global trackers
                    s_evaluator_all.add_case(ranks=s_ranks, answer=Node(service, "unknown"))
                    
                    for comp in current_components:
                        # Normalize 'io' keyword into consistent disk metrics
                        norm_comp = "disk" if comp == "io" else comp
                        
                        # Populate component specific metrics seamlessly
                        fault_s_evaluators[norm_comp].add_case(ranks=s_ranks, answer=Node(service, "unknown"))
                        fault_f_evaluators[norm_comp].add_case(ranks=f_ranks, answer=Node(service, comp))
                        f_evaluator_all.add_case(ranks=f_ranks, answer=Node(service, comp))

            eval_data["service-fault"].append(f"{service}_{fault}")
            eval_data["top_1_service"].append(s_evaluator.accuracy(1))
            eval_data["top_3_service"].append(s_evaluator.accuracy(3))
            eval_data["top_5_service"].append(s_evaluator.accuracy(5))
            eval_data["avg@5_service"].append(s_evaluator.average(5))
            eval_data["top_1_metric"].append(f_evaluator.accuracy(1))
            eval_data["top_3_metric"].append(f_evaluator.accuracy(3))
            eval_data["top_5_metric"].append(f_evaluator.accuracy(5))
            eval_data["avg@5_metric"].append(f_evaluator.average(5))

    print("--- Evaluation results ---")
    result = {"cpu":"", "mem":"", "io":"", "socket":"", "delay":"", "loss":""}
    avg_recall = {}
    
    # ------------------------------------------------------------
    # DYNAMIC AGGREGATION LOOKUP FOR FINAL REPORTING
    # ------------------------------------------------------------
    active_keys = sorted(list(fault_s_evaluators.keys()))
    
    for name in active_keys:
        s_evaluator = fault_s_evaluators[name]
        f_evaluator = fault_f_evaluators[name]
        
        eval_data["service-fault"].append(f"overall_{name}")
        eval_data["top_1_service"].append(s_evaluator.accuracy(1))
        eval_data["top_3_service"].append(s_evaluator.accuracy(3))
        eval_data["top_5_service"].append(s_evaluator.accuracy(5))
        eval_data["avg@5_service"].append(s_evaluator.average(5))
        eval_data["top_1_metric"].append(f_evaluator.accuracy(1))
        eval_data["top_3_metric"].append(f_evaluator.accuracy(3))
        eval_data["top_5_metric"].append(f_evaluator.accuracy(5))
        eval_data["avg@5_metric"].append(f_evaluator.average(5))

        if s_evaluator.average(5) is not None and f_evaluator.average(5) is not None:
            s_avg = round(s_evaluator.average(5), 2)
            f_avg = round(f_evaluator.average(5), 2)
            
            print(f"Avg@5-{name.upper()} (Service):".ljust(25), s_avg)
            print(f"Avg@5-{name.upper()} (Metric):".ljust(25), f_avg)
            avg_recall[f"Avg@5-{name.upper()} (Metric):".ljust(25)] = f_avg
            avg_recall[f"Avg@5-{name.upper()} (Service):".ljust(25)] = s_avg
            
            res_key = "io" if name == "disk" else name
            result[f"{res_key}_service"] = f"{s_avg}"
            result[f"{res_key}_metric"] = f"{f_avg}"

    # ------------------------------------------------------------
    # DYNAMIC DUAL MACRO CALCULATION RUNNER
    # ------------------------------------------------------------
    if dual and active_keys:
        valid_s_scores = [fault_s_evaluators[k].average(5) for k in active_keys if fault_s_evaluators[k].average(5) is not None]
        valid_f_scores = [fault_f_evaluators[k].average(5) for k in active_keys if fault_f_evaluators[k].average(5) is not None]
        
        if valid_s_scores and valid_f_scores:
            macro_service_avg5 = round(sum(valid_s_scores) / len(valid_s_scores), 2)
            macro_metric_avg5 = round(sum(valid_f_scores) / len(valid_f_scores), 2)
            
            print(f"Avg@5-DUAL_MACRO (Service):".ljust(25), macro_service_avg5)
            print(f"Avg@5-DUAL_MACRO (Metric):".ljust(25), macro_metric_avg5)
            
            avg_recall["Avg@5-DUAL_MACRO (Service)"] = macro_service_avg5
            avg_recall["Avg@5-DUAL_MACRO (Metric)"] = macro_metric_avg5
            result["dual_macro_service_avg5"] = macro_service_avg5
            result["dual_macro_metric_avg5"] = macro_metric_avg5

    # Master baseline evaluation logs
    eval_data["service-fault"].append("overall_global")
    eval_data["top_1_service"].append(s_evaluator_all.accuracy(1))
    eval_data["top_3_service"].append(s_evaluator_all.accuracy(3))
    eval_data["top_5_service"].append(s_evaluator_all.accuracy(5))
    eval_data["avg@5_service"].append(s_evaluator_all.average(5))
    eval_data["top_1_metric"].append(f_evaluator_all.accuracy(1))
    eval_data["top_3_metric"].append(f_evaluator_all.accuracy(3))
    eval_data["top_5_metric"].append(f_evaluator_all.accuracy(5))
    eval_data["avg@5_metric"].append(f_evaluator_all.average(5))

    s_all_avg = round(s_evaluator_all.average(5), 2) if s_evaluator_all.average(5) is not None else 0.0
    f_all_avg = round(f_evaluator_all.average(5), 2) if f_evaluator_all.average(5) is not None else 0.0
    print(f"Avg@5-OVERALL (Service):".ljust(25), s_all_avg)
    print(f"Avg@5-OVERALL (Metric):".ljust(25), f_all_avg)
    if not dual:
        avg_recall["Avg@5-OVERALL (Service)"] = s_all_avg
        avg_recall["Avg@5-OVERALL (Metric)"] = f_all_avg
    result["overall_service"] = f"{s_all_avg}"
    result["overall_metric"] = f"{f_all_avg}"

    if dual and len(dual_hit5_scores) > 0:
        dual_joint_hit5 = round(sum(dual_hit5_scores) / len(dual_hit5_scores), 2)
        print(f"Avg@5-OVERALL (Dual Joint):".ljust(25), dual_joint_hit5)
        avg_recall["Avg@5-OVERALL (Dual Joint)"] = dual_joint_hit5
        result["overall_dual_joint_metric"] = dual_joint_hit5

    for metric_name, values in service_metrics.items():
        stats = summarize(values)
        result[f"service_{metric_name}_mean"] = stats["mean"]
        result[f"service_{metric_name}_std"] = stats["std"]
        result[f"service_{metric_name}_median"] = stats["median"]

    for metric_name, values in metric_metrics.items():
        stats = summarize(values)
        result[f"metric_{metric_name}_mean"] = stats["mean"]
        result[f"metric_{metric_name}_std"] = stats["std"]
        result[f"metric_{metric_name}_median"] = stats["median"]

    for fault_name, metrics in service_metrics_by_fault.items():
        for metric_name, values in metrics.items():
            stats = summarize(values)
            result[f"service_{fault_name}_{metric_name}_mean"] = stats["mean"]
            result[f"service_{fault_name}_{metric_name}_std"] = stats["std"]
            result[f"service_{fault_name}_{metric_name}_median"] = stats["median"]

    for fault_name, metrics in metric_metrics_by_fault.items():
        for metric_name, values in metrics.items():
            stats = summarize(values)
            result[f"metric_{fault_name}_{metric_name}_mean"] = stats["mean"]
            result[f"metric_{fault_name}_{metric_name}_std"] = stats["std"]
            result[f"metric_{fault_name}_{metric_name}_median"] = stats["median"]

    print("---")
    
    # Save parameters block structure unchanged
    final_result = {
        "orth_residual": ORTH_RESIDUAL,
        "orth_type": ORTH_TYPE,
        "temporal_type": TEMPORAL_TYPE,
        "INJECTION_DIRECTORY": INJECTION_DIRECTORY,
        "DUAL_CASE": DUAL_CASE,
        "seed": SEED_NUM,
        "microservice_name": MICRO_SERVICE,
        "num_users": NUM_USERS,
        "model_name": model_name,
        "SEQ_LEN": SEQ_LEN,
        "avg_recall": avg_recall,
        "service_MRR_mean": result["service_MRR_mean"],
        "service_Hit@5_mean": result["service_Hit@5_mean"],
        "train_time": out["train_time"],
        "train_time_per_epoch": out["train_time_per_epoch"],
        "total_epochs_used": out["total_epochs_used"],
        "avg_total_infer_time_over_all_tests": out["avg_total_infer_time_over_all_tests"],
        "num_params": out["num_params"],
        "num_trainable_params": out["num_trainable_params"],
        "model_size_mb": out["model_size_mb"],
        "peak_memory_mb": out["peak_memory_mb"],
        "energy_joules": out["energy_joules"],
    }

    import csv
    csv_file = join(OUT_PATH, "final_results_25June_COMPLETE_FAULTS.csv")
    file_exists = exists(csv_file)
    with open(csv_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=final_result.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(final_result)
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run RCA experiments")
    parser.add_argument("--model_name", type=str, default="SFlexRCA", help="Model name: fits, FreTS, timemixerpp, LFTSAD, SFlexRCA, random, BARO")
    parser.add_argument("--microservice_name", type=str, default="online-boutique", help="Microservice name")
    parser.add_argument("--seed", type=int, default=SEED_NUM, help="Random seed")
    parser.add_argument("--seq_len", type=int, default=SEQ_LEN, help="Sequence length")
    parser.add_argument("--dual_case", type=str, default="0", help="Whether to run dual case evaluation")
    parser.add_argument("--orth_type", type=str, default="fixed", help="Orthogonal transformation type: fixed, learnable, none")
    parser.add_argument("--orth_residual", type=str, default="simple", help="Whether to use orthogonal residual connection: 0 or 1")
    parser.add_argument("--temporal_type", type=str, default="mlp", help="Temporal type: temporal or non-temporal")
    args = parser.parse_args()
    set_seed(args.seed)
    SEED_NUM = args.seed
    model_name = args.model_name 
    SEQ_LEN = args.seq_len
    MICRO_SERVICE = args.microservice_name
    DUAL_CASE = args.dual_case
    if DUAL_CASE == "1":
        DUAL_CASE = True
    else:
        DUAL_CASE = False

    if DUAL_CASE:
        INJECTION_DIRECTORY = "23June_WithDisk_RAMP_concurrent_1000_users"
    else:
        INJECTION_DIRECTORY = "23June_WithDisk_RAMP_1000_users"

    ORTH_TYPE = args.orth_type
    ORTH_RESIDUAL = args.orth_residual
    TEMPORAL_TYPE = args.temporal_type
    run_all(model_name)