import json, os, time, csv, datetime
from kubernetes import client, config
from scoring import score_nodes,CARBON_WEIGHT,WATER_WEIGHT
from placement import cluster_state, choose_node, pod_requests

try:
    config.load_kube_config()
except Exception:
    config.load_incluster_config()

v1 = client.CoreV1Api()

SCHEDULER_NAME = "cwScheduler"
OPT_IN = "cw-scheduler/opt-in"
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL",10))
MIN_GAIN = float(os.getenv("MIN_GAIN", 10))
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", 60))
MAX_EVICTIONS_PER_CYCLE = int(os.getenv("MAX_EVICTIONS_PER_CYCLE", 3))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
 


def log_eviction(pod, ns, src, dst, gain):
    os.makedirs("log_files",exist_ok=True)
    path = "log_files/eviction_log.csv"
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Pod","Namespace","From","To","Gain","DryRun","TimeStamp"])
        w.writerow([pod, ns, src, dst, f"{gain:.2f}", DRY_RUN, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        
def controller_owner(pod):
    for o in pod.metadata.owner_references or []:
        if o.controller:
            return o
    return None

def evict_pod(name, namespace):
    body = client.V1Eviction(
    api_version="policy/v1", kind="Eviction",
    metadata=client.V1ObjectMeta(name=name, namespace=namespace)
    )
    v1.create_namespaced_pod_eviction(
        name=name, namespace=namespace, body=body, _preload_content=False
    )

def check_once():
    results = score_nodes(CARBON_WEIGHT, WATER_WEIGHT)
    scores = {n: r["score"] for n, r in results.items()}
    state = cluster_state(v1)
    if not state:
        print("[SKIP CYCLE] no ready nodes")
        return
        
    now = datetime.datetime.now(datetime.timezone.utc)
    evicted = 0
    
    pods = v1.list_pod_for_all_namespaces(field_selector=f"status.phase=Running,spec.schedulerName={SCHEDULER_NAME}")
    
    for pod in pods.items:
        if evicted >= MAX_EVICTIONS_PER_CYCLE:
            break
        name, ns = pod.metadata.name, pod.metadata.namespace
        if(pod.metadata.annotations or {}).get(OPT_IN) != "true":
            continue
            
        if pod.metadata.deletion_timestamp: #already terminating
            continue
            
        owner = controller_owner(pod)
        if owner is None or owner.kind == "DaemonSet":
            continue
        
        start = pod.status.start_time
        if start and (now - start).total_seconds() < COOLDOWN_SECONDS:
            continue
        
        current = pod.spec.node_name
        if current not in scores:
            print(f"[SKIP] {name}: {current} not in metrics.json")
            continue
        
        target = choose_node(results, state, pod_requests(pod), exclude={current})
        if target is None:
            continue
        
        gain = scores[target] - scores[current]
        if gain < MIN_GAIN:
            continue
    
        
        if DRY_RUN:
            print(f"[DRY-RUN] would evict {name}: {current} -> {target} (gain {gain:.2f})")
            log_eviction(name, ns, current, target, gain)
            continue
            
       
        try:
            evict_pod(name,ns)
            evicted+=1
            log_eviction(name,ns,current,target,gain)
            print(f"[EVICTED] {name}: {current} -> {target} (gain {gain:.2f})")
        except client.ApiException as e:
            if e.status == 429:
                print(f"[BLOCKED BY PDB] {name}, will retry")
            else:
                print(f"[EVICT FAILED] {name}: {e.status} {e.reason}")

def main():
    print("Descheduler Started" + (" (DRY RUN)" if DRY_RUN else "" ))
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"Check failed: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
    
