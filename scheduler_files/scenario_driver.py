import argparse, copy, json, os, threading, time

def load_json(path):
    with open(path) as f:
        return json.load(f)

def write_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def region_of(node_name):
    return node_name.rsplit("-",1)[0]

def validate(scenario, db):
    regions = {region_of(n) for n in db}
    for ev in scenario["events"]:
        if ev["region"] not in regions:
            raise ValueError(f"event at {ev['at']}s: unknown region '{ev['region']}' (known: {sorted(regions)})")
        if not ev.get("restore") and not ev.get("set"):
            raise ValueError(f"event at {ev['at']}s needs either 'set' or 'restore'")
        for n in db:
            if region_of(n) == ev["region"]:
                bad = set(ev.get("set", {})) - set(db[n])
                if bad:
                    raise ValueError(f"event at {ev['at']}s: unknown metric keys(s) {sorted(bad)}" f"(valid: {sorted(db[n])})")

def apply_event(live, base, ev):
    hit = [n for n in live if region_of(n) == ev["region"]]
    for n in hit:
        if ev.get("restore"):
            live[n] = copy.deepcopy(base[n])
        else:
            live[n].update(ev["set"])
    return hit

def run_scenario(scenario, metrics_path, base_path, speed=1.0, stop=None, log=print):
    stop = stop or threading.Event()
    base = load_json(metrics_path)
    validate(scenario, base)
    write_atomic(base_path, base)
    live = copy.deepcopy(base)
    
    t0 = time.time()
    for ev in sorted(scenario["events"], key=lambda e: e["at"]):
        wait = t0 +  ev["at"] / speed - time.time()
        if wait > 0 and stop.wait(wait):
            break
        hit = apply_event(live, base, ev)
        write_atomic(metrics_path, live)
        what = "back to baseline" if ev.get("restore") else ev["set"]
        log(f"[SCENARIO t={time.time() - t0:5.0f}s] {ev['region']} ({len(hit)} nodes) : {what}")
    return t0

def restore_base(metrics_path, base_path):
    if os.path.exists(base_path):
        write_atomic(metrics_path, load_json(base_path))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--metrics", default="config_files/metrics.json")
    ap.add_argument("--base", default="config_files/metrics_base.json")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args()
    
    scenario = load_json(args.scenario)
    validate(scenario, load_json(args.metrics))
    try:
        t0 = run_scenario(scenario, args.metrics, args.base, speed=args.speed)
        remaining = t0 + scenario.get("duration", 0) / args.speed - time.time()
        if remaining > 0:
            time.sleep(remaining)
    except KeyboardInterrupt:
        pass
    finally:
        restore_base(args.metrics, args.base)

if __name__ == "__main__":
    main()
    
