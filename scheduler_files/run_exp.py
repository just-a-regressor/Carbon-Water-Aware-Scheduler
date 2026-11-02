"""Runs the four-way comparison, each run under the identical scripted scenario.

  default      the stock Kubernetes scheduler (carbon/water blind)
  carbon_only  your scheduler with CARBON_WEIGHT=1, WATER_WEIGHT=0, no descheduler
  cw           your scheduler (carbon + water), no descheduler
  cw_desched   your scheduler + descheduler

Run from the project folder (the one containing config_files/, scoring.py, gen_cluster.py):

  kwokctl create cluster --name cw ; kubectl config use-context kwok-cw
  python gen_cluster.py ; kubectl apply -f nodes.yaml
  python run_experiment.py --reps 1          # quick check, about 20 minutes
  python run_experiment.py --reps 3          # the real thing

Each run: reset -> start scheduler(s) -> apply workload -> play scenario while recording ->
stop everything -> save logs. Results land in runs/<mode>_<rep>/ and runs/results.csv.
"""
import argparse
import csv
import datetime
import os
import shutil
import subprocess
import sys
import threading
import time
import statistics

import recorder
from scenario_driver import load_json, restore_base, run_scenario

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = "config_files/metrics.json"
BASE = "config_files/metrics_base.json"
LOGS = ["log_files/simulation_log.csv", "log_files/eviction_log.csv"]

MODES = {
    "default":     dict(scheduler=False, descheduler=False, env={}),
    "carbon_only": dict(scheduler=True,  descheduler=False, env={"CARBON_WEIGHT": "1.0", "WATER_WEIGHT": "0.0"}),
    "cw":          dict(scheduler=True,  descheduler=False, env={}),
    "cw_desched":  dict(scheduler=True,  descheduler=True,  env={}),
}


DESCHED_ENV = {"CHECK_INTERVAL": "5", "MAX_EVICTIONS_PER_CYCLE": "10", "COOLDOWN_SECONDS": "30", "MIN_GAIN": "10"}

DEPLOY = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: eco-demo
spec:
  replicas: {replicas}
  selector:
    matchLabels: {{app: eco-demo}}
  template:
    metadata:
      labels: {{app: eco-demo}}
      annotations:
        cw-scheduler/opt-in: "true"
    spec:
{scheduler_line}      containers:
      - name: c
        image: fake-image
        resources:
          requests: {{cpu: 500m, memory: 256Mi}}
"""


def script(name):
    for d in (HERE, os.getcwd()):
        path = os.path.join(d, name)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"{name} not found in {HERE} or {os.getcwd()}")


def sh(*cmd, input=None, check=True):
    try:
        return subprocess.run(cmd, input=input, text=True, capture_output=True, check=check)
    except FileNotFoundError:
        sys.exit(f"[HARNESS] '{cmd[0]}' not found in PATH")
    except subprocess.CalledProcessError as e:
        sys.exit(f"[HARNESS] command failed: {' '.join(cmd)}\n{e.stdout}\n{e.stderr}")


def wait_no_pods(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        out = sh("kubectl", "get", "pods", "-l", "app=eco-demo", "--no-headers", check=False).stdout.strip()
        if not out:
            return
        time.sleep(2)
    raise RuntimeError("old eco-demo pods did not terminate in time")


def stash_old_logs():
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for p in LOGS:
        if os.path.exists(p):
            os.rename(p, os.path.join(os.path.dirname(p), f"old_{stamp}_{os.path.basename(p)}"))


def collect_logs(out):
    for p in LOGS:
        if os.path.exists(p):
            shutil.move(p, os.path.join(out, os.path.basename(p)))


def spawn(script, env, logfile):
    return subprocess.Popen([sys.executable, "-u", script], env=env,
                            stdout=open(logfile, "w"), stderr=subprocess.STDOUT)


def run_one(mode, rep, args, scenario):
    cfg = MODES[mode]
    out = os.path.join(args.out, f"{mode}_{rep}")
    os.makedirs(out, exist_ok=True)
    print(f"\n=== {mode}  run {rep} ===", flush=True)

    sh("kubectl", "delete", "deployment", "eco-demo", "--ignore-not-found", "--wait=true", check=False)
    wait_no_pods()
    if os.path.exists(BASE):
        os.remove(BASE)
    sh(sys.executable, script("gen_cluster.py"))          
    stash_old_logs()

    manifest = DEPLOY.format(
        replicas=args.replicas,
        scheduler_line="      schedulerName: cwScheduler\n" if cfg["scheduler"] else "")

    procs = []
    env = dict(os.environ, **cfg["env"])
    try:
        if cfg["scheduler"]:
            procs.append(spawn(script("cwscheduler.py"), env, os.path.join(out, "scheduler.log")))
        if cfg["descheduler"]:
            procs.append(spawn(script("descheduler-controller.py"), dict(env, **DESCHED_ENV),
                               os.path.join(out, "descheduler.log")))
        time.sleep(3)

        sh("kubectl", "apply", "-f", "-", input=manifest)
        stop = threading.Event()
        err = []
        
        def _play():
            try:
                run_scenario(scenario, METRICS, BASE, args.speed, stop)
            except Exception as e:
                err.append(e)
                print(f"[HARNESS] scenario thread failed: {e}", flush=True)
                
        th = threading.Thread(target=_play, daemon=True)
        th.start()
        duration = scenario["duration"] / args.speed + args.tail
        summary = recorder.record(out, duration, interval=args.interval,
                                  selector="app=eco-demo", metrics_path=METRICS)
        stop.set()
        th.join(timeout=5)
        if err:
            raise RuntimeError(f"scenario failed in {mode} run {rep}: {err[0]}")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sh("kubectl", "delete", "deployment", "eco-demo", "--ignore-not-found", check=False)
        restore_base(METRICS, BASE)
        collect_logs(out)

    summary.update(mode=mode, rep=rep)
    print(f"    carbon {summary['carbon_units']}  water {summary['water_units']}  "
          f"evictions {summary['evictions']}  pending-pod-s {summary['pending_pod_seconds']}  "
          f"peak/node {summary['peak_pods_on_one_node']}", flush=True)
    return summary


def append_results(path, summary):
    cols = ["mode", "rep", "carbon_units", "water_units", "evictions", "pending_pod_seconds",
            "placed_pod_seconds", "peak_pods_on_one_node", "avg_nodes_used", "duration_s"]
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        w.writerow([summary[c] for c in cols])


def print_table(all_summaries):
    keys = ["carbon_units", "water_units", "evictions", "pending_pod_seconds", "peak_pods_on_one_node"]
    print("\n=== MEAN +/- STD PER MODE (std needs reps >= 2) ===")
    print(f"{'mode':<12}" + "".join(f"{k:>26}" for k in keys))
    for mode in MODES:
        rows = [s for s in all_summaries if s["mode"] == mode]
        if rows:
            cells = []
            for k in keys:
                vals = [r[k] for r in rows]
                sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
                cells.append(f"{statistics.mean(vals):.2f} +/- {sd:.2f}")
            print(f"{mode:<12}" + "".join(f"{c:>26}" for c in cells))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=list(MODES), choices=list(MODES))
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--replicas", type=int, default=40)
    ap.add_argument("--scenario", default="scenario.json")
    ap.add_argument("--speed", type=float, default=1.0, help="2 = play the scenario twice as fast")
    ap.add_argument("--tail", type=float, default=15, help="extra seconds recorded after the last scenario second")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--any-context", action="store_true", help="skip the kwok-* kubectl context check")
    args = ap.parse_args()

    print(f"[HARNESS] folder: {os.getcwd()}", flush=True)
    if not os.path.exists(METRICS):
        sys.exit(f"[HARNESS] {METRICS} not found. Run this from the folder that contains config_files/.")
    ctx = sh("kubectl", "config", "current-context", check=False).stdout.strip()
    print(f"[HARNESS] kubectl context: {ctx or '<none>'}", flush=True)
    if not args.any_context and not ctx.startswith("kwok-"):
        sys.exit("[HARNESS] not on a kwok-* context. Run: kubectl config use-context kwok-cw  (or pass --any-context)")
    nodes = sh("kubectl", "get", "nodes", "--no-headers", check=False).stdout.strip().splitlines()
    print(f"[HARNESS] cluster has {len(nodes)} nodes", flush=True)
    if not nodes:
        sys.exit("[HARNESS] no nodes. Run: python gen_cluster.py && kubectl apply -f nodes.yaml")

    candidates = [args.scenario,
              os.path.join("config_files", args.scenario),
              os.path.join(HERE, args.scenario)]
    scen_path = next((c for c in candidates if os.path.exists(c)), None)
    if scen_path is None:
        sys.exit("[HARNESS] scenario file not found. Looked in: " + ", ".join(candidates))
    print(f"[HARNESS] scenario: {scen_path}", flush=True)
    scenario = load_json(scen_path)
    os.makedirs(args.out, exist_ok=True)
    all_summaries = []
    for rep in range(1, args.reps + 1):
        for mode in args.modes:            # interleave modes so drift affects them equally
            s = run_one(mode, rep, args, scenario)
            append_results(os.path.join(args.out, "results.csv"), s)
            all_summaries.append(s)
    print_table(all_summaries)


if __name__ == "__main__":
    main()
