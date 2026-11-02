"""Units: carbon_units / water_units are RELATIVE (requested-CPU x intensity x hours).
They are for comparing runs against each other, not real grams or litres.
Pods are weighted by their requested CPU (1.0 if they request none)."""

import argparse,csv, json, os, time
from scoring import water_index
from scenario_driver import region_of

def cpu_cores(q):
    q = str(q)
    return float(q[:-1]) / 1000.0 if q.endswith("m") else float(q)

def pod_weight(pod):
    total = 0.0
    for c in pod.spec.containers:
        req = (c.resources.requests or {}) if c.resources else {}
        if "cpu" in req:
            total += cpu_cores(req["cpu"])
    return total or 1.0

def total_rows(path):
    if not os.path.exists(path):
        return 0
    with open(path, newline="") as f:
        return max(0, sum(1 for _ in csv.reader(f)) - 1)

def count_evictions(path, skip_rows):
    if not os.path.exists(path):
        return 0
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return sum(1 for r in rows[skip_rows:] if r.get("DryRun", "False") != "True")

def k8s_pods_fn(selector):
    from kubernetes import client, config
    try:
        config.load_kube_config()
    except Exception:
        config.load_incluster_config()
    v1 = client.CoreV1Api()
    return lambda: v1.list_pod_for_all_namespaces(label_selector=selector).items

def record(out_dir, duration, interval=2.0, pods_fn=None, selector="",
        metrics_path="config_files/metrics.json",
        evict_log="log_files/eviction_log.csv"):
    pods_fn = pods_fn or k8s_pods_fn(selector)
    os.makedirs(out_dir, exist_ok=True)
    start_rows = total_rows(evict_log)
    
    t0 = last = time.time()
    carbon = water = pending_s = placed_s = 0.0
    peak_on_node = samples = nodes_used_sum = 0
    writer = None
    regions = []
    
    with open(os.path.join(out_dir, "timeseries.csv"), "w", newline="") as f:
        while True:
            now = time.time()
            if now - t0 >= duration:
                break
            try:
                with open(metrics_path) as mf:
                    db = json.load(mf)
                pods = pods_fn()
            except Exception as e:
                print(f"[RECORDER] sample skipped: {e}")
                time.sleep(interval)
                continue
            
            if writer is None:
                regions = sorted({region_of(n) for n in db})
                writer = csv.writer(f)
                writer.writerow(["t","placed","pending","nodes_used","max_pods_on_node",
                "carbon_rate", "water_rate"] + [f"pods_{r}" for r in regions])
            
            dt = now - last
            last = now
            per_node, per_region = {}, {r: 0 for r in regions}
            pending = placed = 0
            c_rate = w_rate = 0.0
            for p in pods:
                if p.status.phase in ("Succeeded", "Failed"):
                    continue
                node = p.spec.node_name
                if not node:
                    pending+=1
                if node not in db:
                    continue
                placed+=1
                wt = pod_weight(p)
                per_node[node] = per_node.get(node, 0) + 1
                per_region[region_of(node)] += 1
                c_rate += wt * db[node]["carbon-intensity"]
                w_rate += wt * water_index(db[node])
            
            carbon += c_rate * dt / 3600.0
            water += w_rate * dt / 3600.0
            pending_s += pending * dt
            placed_s += placed * dt
            max_on_node = max(per_node.values(), default=0)
            peak_on_node = max(peak_on_node, max_on_node)
            nodes_used_sum += len(per_node)
            samples += 1
            
            writer.writerow([f"{now -t0:.1f}", placed, pending, len(per_node), max_on_node,
                f"{c_rate:.2f}", f"{w_rate:.2f}"] + [per_region[r] for r in regions])
            f.flush()
            time.sleep(max(0.0, interval - (time.time() - now)))
            
        summary = {
            "duration_s": round(time.time() - t0, 1),
            "samples": samples,
            "carbon_units": round(carbon, 3),
            "water_units": round(water, 3),
            "pending_pod_seconds": round(pending_s, 1),
            "placed_pod_seconds": round(placed_s, 1),
            "evictions": count_evictions(evict_log, start_rows),
            "peak_pods_on_one_node": peak_on_node,
            "avg_nodes_used": round(nodes_used_sum / samples, 2) if samples else 0,
        }
        with open(os.path.join(out_dir, "summary.json"), "w") as sf:
            json.dump(summary, sf, indent=2)
        return summary

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--selector", default="app=eco-demo")
    ap.add_argument("--metrics", default="config_files/metrics.json")
    args = ap.parse_args()
    print(json.dumps(record(args.out_dir, args.duration, args.interval,
    selector=args.selector, metrics_path=args.metrics), indent=2))

if __name__ == "__main__":
    main()
