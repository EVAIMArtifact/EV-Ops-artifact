# src/executor/validator.py

import yaml


ALLOWED_TASK_MODULES = {"shell", "ansible.builtin.shell"}

FORBIDDEN_TASK_MODULES = {
    "ansible.builtin.command",
    "command",
    "raw",
    "script",
    "kubernetes.core.k8s",
    "kubernetes.core.k8s_scale",
    "kubernetes.core.k8s_info",
}

TASK_METADATA_KEYS = {
    "name",
    "register",
    "changed_when",
    "failed_when",
    "when",
    "retries",
    "delay",
    "until",
    "ignore_errors",
    "environment",
    "vars",
    "loop",
    "with_items",
    "loop_control",
    "tags",
}

FORBIDDEN_COMMAND_PATTERNS = {
    "kubectl autoscale",
    "minReplicas",
    "maxReplicas",
    "horizontalpodautoscaler",
    "horizontalpodautoscalers",
    " hpa ",
    "kubectl delete namespace",
    "kubectl delete deployment",
    "kubectl delete service",
    "kubectl delete svc",
    "kubectl delete pvc",
    "kubectl delete pv",
    "kubectl delete node",
    "rm -rf",
    "kubectl rollout status",
}


def validate_ansible_playbook(playbook_yaml: str) -> None:
    """
    Validate EV-AIM kubectl-shell-based Ansible playbook.

    Raises ValueError if playbook violates executor constraints.
    """

    try:
        playbook = yaml.safe_load(playbook_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")

    if not isinstance(playbook, list) or len(playbook) != 1:
        raise ValueError("Playbook must contain exactly one play")

    play = playbook[0]

    if not isinstance(play, dict):
        raise ValueError("Play must be a dictionary")

    if play.get("hosts") != "localhost":
        raise ValueError("Play must use hosts: localhost")

    if play.get("connection") != "local":
        raise ValueError("Play must use connection: local")

    if play.get("gather_facts") not in (False, "false", "False"):
        raise ValueError("Play must set gather_facts: false")

    tasks = play.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Play must contain a non-empty tasks list")

    has_kubectl = False

    for task_idx, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"Task {task_idx} must be a dictionary")

        module_keys = [
                            key for key in task.keys()
                            if key not in TASK_METADATA_KEYS
                        ]

        if len(module_keys) != 1:
            raise ValueError(
                f"Task {task_idx} must use exactly one module, found: {module_keys}"
            )

        module = module_keys[0]

        if module in FORBIDDEN_TASK_MODULES:
            raise ValueError(f"Forbidden module used in task {task_idx}: {module}")

        if module not in ALLOWED_TASK_MODULES:
            raise ValueError(f"Only ansible.builtin.shell is allowed, found: {module}")

        command_text = task.get(module)

        if not isinstance(command_text, str):
            raise ValueError(
                f"Task {task_idx} ansible.builtin.shell content must be a string"
            )

        normalized = " ".join(command_text.lower().split())

        if "kubectl" in normalized:
            has_kubectl = True

        for forbidden in FORBIDDEN_COMMAND_PATTERNS:
            if forbidden.lower() in normalized:
                raise ValueError(
                    f"Forbidden command pattern in task {task_idx}: {forbidden}"
                )

        if "retries" in task and not isinstance(task["retries"], int):
            raise ValueError(f"Task {task_idx} retries must be an integer")

        if "delay" in task and not isinstance(task["delay"], int):
            raise ValueError(f"Task {task_idx} delay must be an integer")

    if not has_kubectl:
        raise ValueError("Playbook must contain at least one kubectl command")
