"""Chaos Mesh + kubectl fault handlers ported from the robot-shop rca-service.

These implement the "core 5" resource/network faults via Chaos Mesh
(StressChaos / NetworkChaos / PodChaos) plus disk / config / cpu-throttle /
dependency faults via plain kubectl. They are the rca-service equivalents that
EV-AIM now reuses for CPU, memory, dependency, network, disk, config and
cpu-throttle pressure.

This module is intentionally self-contained (its own ``run_kubectl`` wrapper)
so that ``fault_inject`` can import it without a circular dependency.

Faults target pods by label, and the label KEY differs per app (robot-shop
``service=``, sock-shop ``name=``, online-boutique ``app=``). It is resolved
per-fault from ``fault["app"]`` via :func:`pod_label_key` (overridable with
``fault["pod_label_key"]`` or the ``FAULT_POD_LABEL_KEY`` env var). Using one
fixed key makes the Chaos Mesh CR select zero pods on the other apps.

Every ``inject_*`` returns a result dict carrying a ``recover`` key; pass that
dict to :func:`recover_chaos_fault` to undo it.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional

CHAOS_API = "chaos-mesh.org/v1alpha1"

# Pod label key that identifies a "service" — DIFFERS PER APP: robot-shop labels
# pods `service=<name>`, sock-shop uses `name=<name>`, online-boutique uses
# `app=<name>`. A single global key silently matches zero pods on the other apps
# (the Chaos Mesh CR applies but selects nothing), so resolve it per-fault.
APP_TO_POD_LABEL = {
    "robot-shop": "service",
    "sock-shop": "name",
    "online-boutique": "app",
}
# Global fallback (used when the fault has no app/override). FAULT_POD_LABEL_KEY
# overrides the default for single-app deployments.
DEFAULT_POD_LABEL_KEY = os.getenv("FAULT_POD_LABEL_KEY", "service")
# Backwards-compatible alias.
POD_LABEL_KEY = DEFAULT_POD_LABEL_KEY


def pod_label_key(f: Dict[str, Any]) -> str:
    """Resolve the pod-label key for a fault: explicit override > per-app map >
    env/default. ``app`` comes from the fault dict (e.g. "sock-shop")."""
    return (
        f.get("pod_label_key")
        or APP_TO_POD_LABEL.get(f.get("app"))
        or DEFAULT_POD_LABEL_KEY
    )


def run_kubectl(args: List[str], stdin: Optional[str] = None, check: bool = True) -> str:
    r = subprocess.run(["kubectl"] + args, input=stdin, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout.strip()


def _selector(f: Dict[str, Any]) -> Dict[str, Any]:
    return {"namespaces": [f["namespace"]],
            "labelSelectors": {pod_label_key(f): f["service"]}}


def _chaos(kind: str, name: str, namespace: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "apiVersion": CHAOS_API,
        "kind": kind,
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def _apply_chaos(cr: Dict[str, Any]) -> Dict[str, Any]:
    run_kubectl(["apply", "-f", "-"], stdin=json.dumps(cr))
    md = cr["metadata"]
    return {
        "recover": "delete_chaos",
        "kind": cr["kind"],
        "name": md["name"],
        "namespace": md["namespace"],
    }


# --------------------------------------------------------------------------- #
# Chaos Mesh faults (core 5)
# --------------------------------------------------------------------------- #
def inject_cpu_hog(f: Dict[str, Any]) -> Dict[str, Any]:
    name = f"cpu-{f['service']}"
    spec = {
        "mode": f.get("mode", "all"),
        "selector": _selector(f),
        "stressors": {"cpu": {"workers": int(f.get("workers", 2)),
                              "load": int(f.get("load", 100))}},
    }
    return _apply_chaos(_chaos("StressChaos", name, f["namespace"], spec))


def inject_mem_stress(f: Dict[str, Any]) -> Dict[str, Any]:
    name = f"mem-{f['service']}"
    spec = {
        "mode": f.get("mode", "all"),
        "selector": _selector(f),
        "stressors": {"memory": {"workers": int(f.get("workers", 1)),
                                 "size": f.get("size", "256MB")}},
    }
    return _apply_chaos(_chaos("StressChaos", name, f["namespace"], spec))


def inject_net_delay(f: Dict[str, Any]) -> Dict[str, Any]:
    name = f"netdelay-{f['service']}"
    spec = {
        "action": "delay",
        "mode": f.get("mode", "all"),
        "selector": _selector(f),
        "delay": {"latency": f.get("latency", "200ms"), "jitter": f.get("jitter", "50ms"),
                  "correlation": str(f.get("correlation", 50))},
        "direction": f.get("direction", "to"),
    }
    return _apply_chaos(_chaos("NetworkChaos", name, f["namespace"], spec))


def inject_net_loss(f: Dict[str, Any]) -> Dict[str, Any]:
    name = f"netloss-{f['service']}"
    spec = {
        "action": "loss",
        "mode": f.get("mode", "all"),
        "selector": _selector(f),
        "loss": {"loss": str(f.get("loss", 20)), "correlation": str(f.get("correlation", 50))},
        "direction": f.get("direction", "to"),
    }
    return _apply_chaos(_chaos("NetworkChaos", name, f["namespace"], spec))


def inject_pod_kill(f: Dict[str, Any]) -> Dict[str, Any]:
    # pod-failure keeps the pod unavailable while the resource exists (sustained
    # availability fault). Use action=pod-kill for a one-shot kill instead.
    name = f"pod-{f['service']}"
    spec = {
        "action": f.get("action", "pod-failure"),
        "mode": f.get("mode", "one"),
        "selector": _selector(f),
    }
    return _apply_chaos(_chaos("PodChaos", name, f["namespace"], spec))


# --------------------------------------------------------------------------- #
# disk_stress: real in-pod block I/O (Chaos Mesh can't generate it here)
# --------------------------------------------------------------------------- #
# STOP is a sentinel file: touching it tells the loop to finish its current cycle
# and exit. We do NOT kill the process -- one backgrounded by `kubectl exec` is in a
# different PID namespace from later execs and can't be signalled (kill -> EPERM).
# The filesystem IS shared across execs, so a stop-FILE works. Both the data file
# and STOP live in the probe-chosen dir, so STOP is always writable (even on hardened
# pods whose /tmp is read-only).
_DISKSTRESS_NAME = ".rca-diskstress.dat"
_DISKSTRESS_STOP_NAME = ".rca-diskstress.stop"
# Hard self-terminate cap (s): a backstop so the loop dies on its own even if
# recovery never runs (e.g. the caller dies). Normal stop is the STOP file.
_DISKSTRESS_MAX_SEC = 1800
# Distroless Go apps (online-boutique cartservice/checkoutservice/frontend/
# productcatalogservice/shippingservice) have no shell or dd, so dd can't be exec'd in
# the app container. They are provisioned with this idle busybox sidecar (mounts
# /rca-scratch); the dd loop runs there instead. cAdvisor charges its writes to the
# pod, so the per-pod fs_write metric rises the same as for shell apps.
_DISKSTRESS_SIDECAR = "rca-diskstress"
# Pick the first writable, NON-tmpfs/ramfs directory: only disk-backed writes
# move the block-I/O counters. Prints the dir, or nothing if the container has
# no disk-writable path (read-only rootfs + tmpfs /tmp -> disk_stress impossible).
# /rca-scratch is checked first: hardened apps (read-only rootfs, e.g. sock-shop /
# online-boutique) are provisioned with a disk-backed emptyDir mounted there so
# disk_stress can run; falls back to the rootfs dirs on apps with a writable root.
_DISK_DIR_PROBE = (
    'for d in /rca-scratch /var/tmp /tmp /run /data .; do '
    '[ -d "$d" ] || continue; '
    '( echo x > "$d/.rcaprobe" ) 2>/dev/null || continue; rm -f "$d/.rcaprobe" 2>/dev/null; '
    't=$(stat -f -c %T "$d" 2>/dev/null); '
    'case "$t" in tmpfs|ramfs) continue;; esac; '
    'printf %s "$d"; break; done'
)


def _diskstress_pods(ns: str, service: str, mode: str, label_key: str) -> List[str]:
    sel = f"{label_key}={service}"
    out = run_kubectl(["get", "pods", "-n", ns, "-l", sel,
                       "--field-selector=status.phase=Running",
                       "-o", "jsonpath={.items[*].metadata.name}"], check=False)
    pods = (out or "").split()
    return pods[:1] if mode == "one" else pods


def _diskstress_loop(ddir: str, mb: int, max_sec: int):
    """Build the in-pod dd write/read loop for ``ddir``; returns (loop_cmd, stop_path).

    O_DIRECT bypasses the page cache so reads hit the device too, falling back to
    conv=fsync / buffered read where O_DIRECT is rejected. Runs until the STOP file
    appears (recovery touches it) or max_sec elapses, then deletes its own files.
    """
    d = ddir.rstrip("/")
    dat, stop = f"{d}/{_DISKSTRESS_NAME}", f"{d}/{_DISKSTRESS_STOP_NAME}"
    wr = (f"dd if=/dev/zero of={dat} bs=1M count={mb} oflag=direct 2>/dev/null "
          f"|| dd if=/dev/zero of={dat} bs=1M count={mb} conv=fsync 2>/dev/null")
    rd = (f"dd if={dat} of=/dev/null bs=1M iflag=direct 2>/dev/null "
          f"|| dd if={dat} of=/dev/null bs=1M 2>/dev/null")
    loop = (f"rm -f {stop}; S=$(date +%s); "
            f"while [ ! -e {stop} ] && [ $(( $(date +%s) - S )) -lt {max_sec} ]; do {wr}; {rd}; done; "
            f"rm -f {dat} {stop}")
    return loop, stop


def inject_disk_stress(f: Dict[str, Any]) -> Dict[str, Any]:
    # Drive REAL block I/O via `kubectl exec dd` from inside the pod, so cAdvisor
    # charges the I/O to that pod. Distroless apps have no shell in the app container,
    # so we fall back to the rca-diskstress sidecar (busybox, mounts /rca-scratch).
    ns, service = f["namespace"], f["service"]
    mode = f.get("mode", "all")
    count = int(f.get("size_mb", 128))   # file size (MB), rewritten each cycle
    max_sec = int(f.get("max_sec", _DISKSTRESS_MAX_SEC))
    label_key = pod_label_key(f)
    pods = _diskstress_pods(ns, service, mode, label_key)
    if not pods:
        raise RuntimeError(f"no running pods for {label_key}={service} in {ns}")
    started, skipped = [], []
    for pod in pods:
        # Try the app container; if it has no shell (distroless), the probe comes back
        # empty -> retry in the sidecar. container=None means the default app container.
        for container in (None, _DISKSTRESS_SIDECAR):
            cargs = ["-c", container] if container else []
            ddir = run_kubectl(["exec", pod, "-n", ns, *cargs, "--", "sh", "-c",
                                _DISK_DIR_PROBE], check=False).strip()
            if ddir:
                break
        if not ddir:
            skipped.append(pod)
            continue
        # ponytail: the /rca-scratch sizeLimit is enforced by kubelet EVICTION, not the
        # filesystem, so an over-size write evicts the pod mid-run. Clamp to 90% of it.
        # No such volume (writable rootfs) -> lim is "" -> no clamp. Ceiling: assumes Mi.
        lim = run_kubectl(["get", "pod", pod, "-n", ns, "-o",
                           'jsonpath={.spec.volumes[?(@.name=="rca-scratch")].emptyDir.sizeLimit}'],
                          check=False)
        mb = min(count, int(lim[:-2]) * 9 // 10) if lim.endswith("Mi") else count
        if mb < count:
            print(f"[FAULT] disk_stress: clamping {count}MB->{mb}MB to fit {lim} on {pod}")
        loop, stop = _diskstress_loop(ddir, mb, max_sec)
        outer = f"nohup sh -c '{loop}' </dev/null >/dev/null 2>&1 & echo started"
        try:
            run_kubectl(["exec", pod, "-n", ns, *cargs, "--", "sh", "-c", outer])
            started.append({"pod": pod, "container": container, "stop": stop})
        except RuntimeError as exc:
            print(f"[FAULT] disk_stress: could not start on {pod}: {exc}")
            skipped.append(pod)
    if not started:
        raise RuntimeError(
            f"disk_stress could not run on any pod of {service}: no disk-backed writable "
            f"path and no {_DISKSTRESS_SIDECAR} sidecar. Skipped: {skipped}")
    return {"recover": "stop_diskstress", "namespace": ns, "service": service,
            "stops": started, "pods": [s["pod"] for s in started], "skipped": skipped}


# --------------------------------------------------------------------------- #
# kubectl faults
# --------------------------------------------------------------------------- #
def _workload_kind(ns: str, name: str) -> str:
    # robot-shop datastores differ: mysql/rabbitmq are Deployments, redis is a
    # StatefulSet. Detect which one exists so scale (and restore) target the
    # right kind.
    out = run_kubectl(["get", "deployment", name, "-n", ns, "-o", "name"], check=False)
    if out and out.strip():
        return "deployment"
    out = run_kubectl(["get", "statefulset", name, "-n", ns, "-o", "name"], check=False)
    if out and out.strip():
        return "statefulset"
    return "deployment"  # fall back; scale surfaces a clear error if truly absent


def inject_dependency_failure(f: Dict[str, Any]) -> Dict[str, Any]:
    ns, dep = f["namespace"], f["deployment"]
    kind = _workload_kind(ns, dep)
    orig = run_kubectl(["get", kind, dep, "-n", ns, "-o", "jsonpath={.spec.replicas}"]) or "1"
    run_kubectl(["scale", kind, dep, "--replicas=0", "-n", ns])
    return {"recover": "scale_back", "namespace": ns, "kind": kind, "deployment": dep,
            "original_replicas": int(orig)}


def inject_config_error(f: Dict[str, Any]) -> Dict[str, Any]:
    ns, dep = f["namespace"], f["deployment"]
    env, bad = f["env"], f.get("bad_value", "invalid.broken.host")
    cont = f.get("container", dep)
    orig = run_kubectl(["get", "deploy", dep, "-n", ns, "-o",
                        f"jsonpath={{.spec.template.spec.containers[?(@.name=='{cont}')].env[?(@.name=='{env}')].value}}"])
    run_kubectl(["set", "env", f"deployment/{dep}", f"{env}={bad}", "-n", ns, "-c", cont])
    return {"recover": "restore_env", "namespace": ns, "deployment": dep,
            "container": cont, "env": env, "original_value": orig, "bad_value": bad}


def inject_cpu_throttle(f: Dict[str, Any]) -> Dict[str, Any]:
    # Realistic resource-starvation: shrink the CPU limit so the EXISTING steady
    # traffic causes throttling (no synthetic load). Triggers a rollout.
    # NOTE: this MUTATES the deployment spec (limits + requests). The original
    # values are captured here and restored on recovery; the EV baseline restore
    # is a second safety net.
    # The CPU *request* must stay <= the new limit, else the API rejects the
    # patch, so lower the request to the throttle value too and restore both.
    ns, dep = f["namespace"], f["deployment"]
    cont = f.get("container", dep)
    limit = f.get("limit", "50m")
    base = f"jsonpath={{.spec.template.spec.containers[?(@.name=='{cont}')].resources"
    orig_lim = run_kubectl(["get", "deploy", dep, "-n", ns, "-o", base + ".limits.cpu}"]) or "500m"
    orig_req = run_kubectl(["get", "deploy", dep, "-n", ns, "-o", base + ".requests.cpu}"])
    patch = {"spec": {"template": {"spec": {"containers": [
        {"name": cont, "resources": {"limits": {"cpu": limit}, "requests": {"cpu": limit}}}]}}}}
    run_kubectl(["patch", "deploy", dep, "-n", ns, "--type", "strategic", "-p", json.dumps(patch)])
    return {"recover": "restore_cpu_limit", "namespace": ns, "deployment": dep,
            "container": cont, "original_limit": orig_lim, "original_request": orig_req,
            "new_limit": limit}


# --------------------------------------------------------------------------- #
# Dispatch + recovery
# --------------------------------------------------------------------------- #
CHAOS_HANDLERS = {
    "cpu_hog": inject_cpu_hog,
    "mem_stress": inject_mem_stress,
    "net_delay": inject_net_delay,
    "net_loss": inject_net_loss,
    "pod_kill": inject_pod_kill,
    "disk_stress": inject_disk_stress,
    "dependency_failure": inject_dependency_failure,
    "config_error": inject_config_error,
    "cpu_throttle": inject_cpu_throttle,
}

# Fault types whose cleanup is handled by recover_chaos_fault().
CHAOS_RECOVER_TYPES = set(CHAOS_HANDLERS.keys())

# Chaos Mesh CR kinds (vs. kubectl-only faults).
CHAOS_MESH_FAULTS = {"cpu_hog", "mem_stress", "net_delay", "net_loss", "pod_kill"}


def recover_chaos_fault(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Undo a fault injected by one of the handlers in this module.

    Dispatches on the ``recover`` key set by each inject_* function. Returns a
    small dict describing what it did (so callers can log/store it).
    """
    rec = (result or {}).get("recover")
    out: Dict[str, Any] = {"recover": rec}

    if rec == "delete_chaos":
        out["delete_chaos"] = run_kubectl(
            ["delete", result["kind"], result["name"], "-n", result["namespace"],
             "--ignore-not-found"], check=False)

    elif rec == "scale_back":
        out["scale_back"] = run_kubectl(
            ["scale", result.get("kind", "deployment"), result["deployment"],
             f"--replicas={result['original_replicas']}", "-n", result["namespace"]],
            check=False)

    elif rec == "restore_cpu_limit":
        resources = {"limits": {"cpu": result["original_limit"]}}
        if result.get("original_request"):
            resources["requests"] = {"cpu": result["original_request"]}
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": result["container"], "resources": resources}]}}}}
        out["restore_cpu_limit"] = run_kubectl(
            ["patch", "deploy", result["deployment"], "-n", result["namespace"],
             "--type", "strategic", "-p", json.dumps(patch)], check=False)

    elif rec == "stop_diskstress":
        # Touch each loop's STOP sentinel (in the dir it writes to); the loop sees it,
        # finishes its dd cycle (~1s) and cleans up. Can't kill it directly (different
        # PID namespace -> EPERM); the shared filesystem makes the signal reliable.
        # -c targets the rca-diskstress sidecar for loops hosted there (distroless apps).
        touched = []
        for t in result.get("stops", []):
            cargs = ["-c", t["container"]] if t.get("container") else []
            run_kubectl(["exec", t["pod"], "-n", result["namespace"], *cargs,
                         "--", "sh", "-c", f"touch {t['stop']}"], check=False)
            touched.append(t["pod"])
        out["stop_diskstress"] = touched

    elif rec == "restore_env":
        if result.get("original_value"):
            out["restore_env"] = run_kubectl(
                ["set", "env", f"deployment/{result['deployment']}",
                 f"{result['env']}={result['original_value']}", "-n", result["namespace"],
                 "-c", result["container"]], check=False)
        else:
            out["restore_env"] = run_kubectl(
                ["set", "env", f"deployment/{result['deployment']}",
                 f"{result['env']}-", "-n", result["namespace"], "-c", result["container"]],
                check=False)
    else:
        out["message"] = f"no chaos recovery handler for recover={rec}"

    return out
