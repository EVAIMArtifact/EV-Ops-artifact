from src.fault_injection.fault_catalog import load_fault_catalog
from src.feedback.knowledge_store import load_experiences

def build_context(fault_id: str, service: str, metrics_before: dict):
    """
    Returns structured context for planner or judge.
    """
    # Load fault info
    catalog = load_fault_catalog()
    fault_info = catalog.get(fault_id, {})

    # Load relevant past experience
    experiences = load_experiences()
    similar_experience = [
        e for e in experiences
        if e["incident"].get("service") == service
        and e["incident"].get("fault") == fault_id
    ]

    context = {
        "incident": {
            "fault_id": fault_id,
            "service": service,
            "fault_type": fault_info.get("type"),
            "fault_duration": fault_info.get("duration", None),
        },
        "metrics_before": metrics_before,
        "past_experience": similar_experience
    }

    return context
