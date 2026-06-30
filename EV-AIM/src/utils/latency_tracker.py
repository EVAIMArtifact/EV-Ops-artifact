import time
from typing import Optional


class LatencyTracker:
    def __init__(self):
        self.timestamps = {}

    def mark(self, stage: str):
        self.timestamps[stage] = time.time()

    def latency(self, start: str, end: str) -> Optional[float]:
        if start not in self.timestamps or end not in self.timestamps:
            return None
        return self.timestamps[end] - self.timestamps[start]

    def summary(self) -> dict:
        return {
            # Fault injection + observation
            "detect_to_fault_injected": self.latency("detect", "fault_injected"),
            "fault_init_wait": self.latency("fault_injected", "fault_initialized"),
            "metric_ingestion_wait": self.latency("fault_initialized", "metrics_available"),

            # Pre-mitigation observation
            "metrics_before_collection": self.latency("metrics_available", "metrics_before"),
            "infrastructure_before_capture": self.latency("metrics_before", "infra_state_before"),
            "planner_context_build": self.latency("infra_state_before", "planner_context_built"),

            # Planning
            "experience_retrieval": self.latency("planner_context_built", "experience_retrieved"),
            "llm_planning": self.latency("experience_retrieved", "plan_generated"),

            # Executor/remediation
            "executor_icl_retrieval": self.latency("plan_generated", "executor_icl_retrieved"),
            "playbook_generation": self.latency("executor_icl_retrieved", "playbook_generated"),
            "playbook_execution": self.latency("playbook_generated", "playbook_executed"),
            "rollout_wait": self.latency("playbook_executed", "rollout_complete"),
            "warmup_period": self.latency("rollout_complete", "warmup_complete"),

            # Post-remediation observation
            "metrics_after_collection": self.latency("warmup_complete", "metrics_after"),
            "infrastructure_after_capture": self.latency("metrics_after", "infra_state_after"),
            "infrastructure_comparison": self.latency("infra_state_after", "infra_comparison_done"),
            "feedback_computation": self.latency("infra_comparison_done", "feedback_computed"),
            "experience_storage": self.latency("feedback_computed", "experience_stored"),
            "final_recovery": self.latency("experience_stored", "final_recovery_done"),

            # High-level timings
            "time_to_plan": self.latency("detect", "plan_generated"),
            "time_to_remediation_execution": self.latency("metrics_available", "playbook_executed"),
            "time_to_rollout_complete": self.latency("metrics_available", "rollout_complete"),
            "time_to_feedback": self.latency("metrics_available", "feedback_computed"),
            "total_experiment_time": self.latency("detect", "final_recovery_done"),
        }

    def detailed_summary(self) -> dict:
        stages = list(self.timestamps.keys())
        return {
            f"{stages[i]}_to_{stages[i + 1]}": self.latency(stages[i], stages[i + 1])
            for i in range(len(stages) - 1)
        }

    def raw_timestamps(self) -> dict:
        return self.timestamps