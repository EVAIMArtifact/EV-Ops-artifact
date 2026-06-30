from typing import Dict, Optional
import re


def parse_ansible_recap(log_text: str) -> Dict[str, int]:
    """
    Extract the PLAY RECAP stats from Ansible output log.
    Returns a dict with keys: ok, changed, failed, unreachable, skipped, rescued, ignored
    """
    recap_pattern = r"(?i)PLAY RECAP\s*\*+\s*(.*)"
    match = re.search(recap_pattern, log_text, re.DOTALL)
    if not match:
        return {}
    
    recap_line = match.group(1)
    stats = {}
    # Example format: "localhost : ok=9 changed=1 unreachable=0 failed=1 skipped=0 rescued=0 ignored=0"
    stat_pattern = r"(\w+)=(\d+)"
    for key, val in re.findall(stat_pattern, recap_line):
        stats[key.lower()] = int(val)
    return stats