""" keeps metrics upto date with real data
    python3 metrics_updater.py --once
    python3 metrics_updater.py --interval 300 
    dont run with run_experiment.py / scenario_driver.py
"""

import argparse
import datetime
import json
import os
import time

from scenario_driver import region_of, write_atomic
from watttime_client import LBS_PER_MWH_TO_G_PER_KWH, WattTimeClient

SITES = "config_files/sites.json"
METRICS = "config_files/metrics.json"
STATUS = "config_files/metrics_status.json"
NODE_LABEL = "cw/region"
REQUIRED = ("carbon_fallback", "scarcity_site", "pue", "wue_direct","ewif")

def load_sites(path):
    with open(path) as f:
        sites = json.load(f)["regions"]
    for name, s in sites.items():
        for k in REQUIRED:
            if s.get(k) is None:
                raise SystemExit(f"{path}: region '{name}' is missing '{k}'")
    return sites

def discover_nodes(metrics_path):
    try:
        from kubernetes import client, config
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()
        out = {}
        for n in client.CoreV1Api().list_node().items:
            region = (n.metadata.labels or {}).get(NODE_LABEL)
            if region:
                out[n.metadata.name] = region
            if out:
                return out
    except Exception as e:
        print(f"[UPDATER] could not list cluster nodes ({e}); using names from {metrics_path}")
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            return {n: region_n(n) for n in json.load(f)}
    raise SystemExit("no nodes found: apply nodes.yaml first (nodes need the cw/region label)")

def fetch_live(client, sites, cache, max_stale, now):
    by_code = {}
    for region, s in sites.items():
        if s.get("watttime_region"):
            by_code.setdefault(s["watttime_region"], []).append(region)
    live, status = {}, {}
    for code, regions in by_code.items():
        value, source = None, ""
        try:
            lbs, point_time = client.current_moer(code)
            value = lbs * LBS_PER_MWH_TO_G_PER_KWH
            cache[code] = (value, now, point_time)
            source = "live"
        except Exception as e:
            err = str(e)[:100]
            if code in cache and now - cache[code][1] <= max_stale:
                value = cache[code][0]
                source = f"stale ({int(now - cache[code][1])}s old; {err})"
            else:
                source = f"fallback ({err})"
        for region in regions:
            status[region] = {"watttime_region": code, "source": source, 
            "g_per_kwh":None if value is None else round(value, 2)}
            if value is not None:
                live[region] = value
    return live, status

def build_metrics(node_region, sites, live):
    metrics = {}
    for node, region in node_region.items():
        if region not in sites:
            raise SystemExit(f"node {node} has region '{region}' which is not is sites.json")
        s = sites[region]
        metrics[node] = {
            "carbon-intensity": round(live.get(region, s["carbon_fallback"]), 2),
            "wue-direct": s["wue_direct"],
            "scarcity_site": s["scarcity_site"],
            "pue": s["pue"],
            "ewif": s["ewif"],
            "scarcity_grid": s.get("scarcity_grid", s["scarcity_sites"]),
        }
    return metrics

def run_cycle(sites, node_region, client, cache, max_stale, metrics_path, status_path, now=None):
    now = time.time() if now is None else now
    live, status = fetch_live(client, sites, cache, max_stale, now) if client else ({}, {})
    metrics = build_metrics(node_region, sites, live)
    write_atomic(metrics_path, metrics)
    for region in sites:
        status.setdefault(region, {"watttime_region": sites[region].get("watttime_region"),
        "source": "fallback (no watttime_region)",
        "g_per_kwh": sites[region]["carbon_fallback"]})
    write_atomic(status_path, {"updated": datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    "regions": status})
    return metrics, status

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default=SITES)
    ap.add_argument("--metrics", default=METRICS)
    ap.add_argument("--status", default=STATUS)
    ap.add_argument("--interval", type=float, default=300)
    ap.add_argument("--max-stale", type=float, default=1800, help="seconds to keep using the last good value")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--offline", action="store_true", help="never call WattTime; write fallback values only")
    args = ap.parse_args()
    sites = load_sites(arg.sites)
    node_region = discover_nodes(args.metrics)
    client = None if args.offline else (WattTimeClient() if any(s.get("watttime_region") for s in sites.values()) else None)
    print(f"[UPDATER] {len(node_region)} nodes, {len(sites)} regions, "
    f"{sum(1 for s in sites.values() if s.get('watttime_region'))} with live carbon")
    cache = {}
    while True:
        try:
            _, status = run_cycle(sites, node_region, client, cache, args.max_stale, args.metrics, args.status)
            parts = [f"{r}={st['g_per_kwh']} ({st['source'].split(' ')[0]})" for r, st in sorted(status.items())]
            print(f"[UPDATER {datetime.datetime.now():%H:%M:%S}] " + " ".join(parts), flush=True)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[UPDATER] cycle failed: {e}", flush=True)
        if args.once:
            break
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
    
