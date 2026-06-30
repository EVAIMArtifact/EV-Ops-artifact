#!/usr/bin/env python3
"""Manually stop on-going fault injections.

Use this when an experiment was interrupted (Ctrl-C, crash, killed runner) and
its faults were never cleaned up. The normal ``recover_fault()`` path needs the
in-memory ``fault_result`` / baseline that a crashed run no longer has, so this
script instead DISCOVERS active faults from live cluster state and stops them.

It stops every kind of fault that keeps running on its own:

  1. Locust load        (load_spike + any background traffic) -> HTTP /stop
  2. Chaos Mesh CRs      (cpu_hog/mem_stress/net_delay/net_loss/pod_kill)
                         -> delete StressChaos / NetworkChaos / PodChaos
  3. In-pod stressors    (db_overload, disk_stress) -> touch STOP sentinel files
  4. Paused rollouts     (stuck_deployment) -> rollout resume

NOTE on what this does NOT undo: faults that mutate a deployment spec
(cpu_throttle, config_error, dependency_failure scale-to-0, bad_image) leave the
cluster in a changed-but-stable state. Reversing those to the *exact* pre-fault
spec needs the captured baseline, so they are reported here but must be reset by
re-running the experiment recovery or by `kubectl rollout undo` / re-apply. This
script focuses on stopping the actively-running fault processes.

Examples:
    # Stop faults across all known namespaces (robot-shop, sock-shop, online-boutique)
    python3 -m scripts.stop_faults

    # Only one app / namespace
    python3 -m scripts.stop_faults --app robot-shop
    python3 -m scripts.stop_faults --namespace robot-shop

    # Preview without changing anything
    python3 -m scripts.stop_faults --dry-run

    # Skip the per-pod sentinel touch (faster; skips db_overload/disk_stress)
    python3 -m scripts.stop_faults --skip-inpod
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Static config (kept in sync with src/fault_injection/*)
# --------------------------------------------------------------------------- #

# app -> namespace. An explicit --namespace overrides.
APP_TO_NAMESPACE = {
    "robot-shop": "robot-shop",
    "sock-shop": "sock-shop",
    "online-boutique": "online-boutique",
}

# Locust ingress URL shape differs per app (see fault_inject.LOCUST_URL_PATTERNS).
LOCUST_URL_PATTERNS = {
    "robot-shop": "{base}/{app}/locust/{action}",
    "sock-shop": "{base}/locust/{app}/{action}",
    "online-boutique": "{base}/locust/{app}/{action}",
}

# Chaos Mesh CR kinds injected by chaos_faults.py.
CHAOS_KINDS = ["StressChaos", "NetworkChaos", "PodChaos"]

# In-pod STOP sentinels. Touching one tells the in-pod watchdog loop to kill its
# own stressor children and clean up (see fault_inject.inject_db_overload /
# chaos_faults.inject_disk_stress for why a file, not a signal, is used).
SENTINEL_FILES = [
    "/tmp/evaim_db_overload.stop",   # db_overload
    "/tmp/.rca-diskstress.stop",     # disk_stress
]


# --------------------------------------------------------------------------- #
# kubectl helper
# --------------------------------------------------------------------------- #

def kubectl(args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl"] + args, capture_output=True, text=True)


def namespace_exists(ns: str) -> bool:
    return kubectl(["get", "namespace", ns]).returncode == 0


# --------------------------------------------------------------------------- #
# 1. Locust load
# --------------------------------------------------------------------------- #

def stop_locust(app: str, dry_run: bool) -> Dict[str, Any]:
    base = os.getenv("BASE_URL")
    user = os.getenv("BASIC_AUTH_USER")
    password = os.getenv("BASIC_AUTH_PASSWORD")

    if not base:
        return {"status": "skipped", "reason": "BASE_URL not set"}

    pattern = LOCUST_URL_PATTERNS.get(app, "{base}/locust/{app}/{action}")
    url = pattern.format(base=base.rstrip("/"), app=app, action="stop")

    if dry_run:
        return {"status": "dry_run", "url": url}

    try:
        import requests
        from requests.auth import HTTPBasicAuth

        auth = HTTPBasicAuth(user, password) if user and password else None
        r = requests.get(url, auth=auth, timeout=15)
        r.raise_for_status()
        return {"status": "stopped", "url": url}
    except Exception as e:  # noqa: BLE001 - best effort
        return {"status": "error", "url": url, "error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------- #
# 2. Chaos Mesh CRs
# --------------------------------------------------------------------------- #

def delete_chaos_crs(ns: str, dry_run: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for kind in CHAOS_KINDS:
        get = kubectl(["get", kind, "-n", ns, "-o",
                       "jsonpath={.items[*].metadata.name}"])
        if get.returncode != 0:
            # CRD not installed in this cluster -> nothing of this kind to delete.
            result[kind] = {"status": "skipped",
                            "reason": "kind not found (Chaos Mesh CRD absent?)"}
            continue

        names = (get.stdout or "").split()
        if not names:
            result[kind] = {"status": "none"}
            continue

        if dry_run:
            result[kind] = {"status": "dry_run", "would_delete": names}
            continue

        deleted, errors = [], {}
        for name in names:
            d = kubectl(["delete", kind, name, "-n", ns, "--ignore-not-found"])
            if d.returncode == 0:
                deleted.append(name)
            else:
                errors[name] = d.stderr.strip()
        result[kind] = {"status": "deleted", "names": deleted}
        if errors:
            result[kind]["errors"] = errors
    return result


# --------------------------------------------------------------------------- #
# 3. In-pod stressors (db_overload, disk_stress)
# --------------------------------------------------------------------------- #

def running_pods(ns: str) -> List[str]:
    out = kubectl(["get", "pods", "-n", ns,
                   "--field-selector=status.phase=Running",
                   "-o", "jsonpath={.items[*].metadata.name}"])
    return (out.stdout or "").split() if out.returncode == 0 else []


def touch_sentinels(ns: str, dry_run: bool) -> Dict[str, Any]:
    pods = running_pods(ns)
    if not pods:
        return {"status": "none", "reason": "no running pods"}

    touch_cmd = "touch " + " ".join(SENTINEL_FILES)
    if dry_run:
        return {"status": "dry_run", "pods": pods, "cmd": touch_cmd}

    touched, failed = [], []
    for pod in pods:
        # Best effort: many pods have no /bin/sh (distroless) or no matching
        # stressor; those just fail silently and are recorded as "failed".
        r = kubectl(["exec", pod, "-n", ns, "--", "sh", "-c", touch_cmd])
        (touched if r.returncode == 0 else failed).append(pod)
    return {"status": "done", "touched": touched, "failed": failed}


# --------------------------------------------------------------------------- #
# 4. Paused rollouts (stuck_deployment)
# --------------------------------------------------------------------------- #

def resume_paused_deployments(ns: str, dry_run: bool) -> Dict[str, Any]:
    out = kubectl(["get", "deployments", "-n", ns, "-o", "json"])
    if out.returncode != 0:
        return {"status": "error", "error": out.stderr.strip()}

    try:
        items = json.loads(out.stdout).get("items", [])
    except json.JSONDecodeError as e:
        return {"status": "error", "error": str(e)}

    paused = [it["metadata"]["name"] for it in items
              if it.get("spec", {}).get("paused")]
    if not paused:
        return {"status": "none"}
    if dry_run:
        return {"status": "dry_run", "would_resume": paused}

    resumed, errors = [], {}
    for name in paused:
        r = kubectl(["rollout", "resume", f"deployment/{name}", "-n", ns])
        if r.returncode == 0:
            resumed.append(name)
        else:
            errors[name] = r.stderr.strip()
    res = {"status": "resumed", "names": resumed}
    if errors:
        res["errors"] = errors
    return res


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def resolve_targets(args) -> List[Dict[str, Optional[str]]]:
    """Return a list of {app, namespace} targets to clean up."""
    if args.namespace:
        # app may be unknown (used only for locust URL); try to reverse-map it.
        app = args.app or next(
            (a for a, n in APP_TO_NAMESPACE.items() if n == args.namespace), None)
        return [{"app": app, "namespace": args.namespace}]
    if args.app:
        return [{"app": args.app,
                 "namespace": APP_TO_NAMESPACE.get(args.app, args.app)}]
    return [{"app": a, "namespace": n} for a, n in APP_TO_NAMESPACE.items()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manually stop on-going fault injections.")
    parser.add_argument("--app", choices=sorted(APP_TO_NAMESPACE),
                        help="Only clean up this app (default: all known apps)")
    parser.add_argument("--namespace",
                        help="Only clean up this namespace (overrides --app's namespace)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be stopped without making changes")
    parser.add_argument("--skip-locust", action="store_true",
                        help="Do not stop Locust load")
    parser.add_argument("--skip-chaos", action="store_true",
                        help="Do not delete Chaos Mesh CRs")
    parser.add_argument("--skip-inpod", action="store_true",
                        help="Do not touch in-pod STOP sentinels (db_overload/disk_stress)")
    parser.add_argument("--skip-resume", action="store_true",
                        help="Do not resume paused deployments (stuck_deployment)")
    args = parser.parse_args()

    if kubectl(["version", "--client"]).returncode != 0:
        print("[ERROR] kubectl not found on PATH.", file=sys.stderr)
        return 2

    targets = resolve_targets(args)
    summary: Dict[str, Any] = {"dry_run": args.dry_run, "targets": {}}

    for t in targets:
        app, ns = t["app"], t["namespace"]
        label = f"{app or '?'} / {ns}"
        print(f"\n=== Stopping faults for {label} ===")
        report: Dict[str, Any] = {}

        if not args.skip_locust and app:
            report["locust"] = stop_locust(app, args.dry_run)
            print(f"  [locust] {report['locust']}")

        if not namespace_exists(ns):
            report["namespace"] = {"status": "missing"}
            print(f"  [skip] namespace {ns} not found")
            summary["targets"][ns] = report
            continue

        if not args.skip_chaos:
            report["chaos"] = delete_chaos_crs(ns, args.dry_run)
            print(f"  [chaos] {report['chaos']}")

        if not args.skip_inpod:
            report["inpod_sentinels"] = touch_sentinels(ns, args.dry_run)
            print(f"  [inpod] {report['inpod_sentinels']}")

        if not args.skip_resume:
            report["paused_rollouts"] = resume_paused_deployments(ns, args.dry_run)
            print(f"  [rollout] {report['paused_rollouts']}")

        summary["targets"][ns] = report

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    print(
        "\nNote: spec-mutating faults (cpu_throttle / config_error / "
        "dependency_failure / bad_image) are NOT auto-reverted here. "
        "Reset those via the experiment recovery or kubectl rollout undo / re-apply."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
