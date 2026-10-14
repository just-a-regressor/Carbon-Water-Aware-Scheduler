import json
import time
from kubernetes import client, config

try:
    config.load_kube_config()
except Exception:
    config.load_incluster_config()

v1 = client.CoreV1Api()

SCHEDULER_NAME = "cwScheduler"
OPT_IN = "cw-scheduler/opt-in"
CHECK_INTERVAL = 10
MIN_GAIN = 10
COOLDOWN_SECONDS = 60

last_moved = {}

def load_environmental_db():
    with open("metrics.json","r") as f:
        return json.load(f)

def water_index(m):
    direct = m["wue_direct"] * m["scarcity_site"]
    indirect = m["pue"] * m["ewif"] * m["scarcity_grid"]
    return direct + indirect

def compute_scores(carbon_weight=0.5, water_weight=0.5):
    db = load_environmental_db()
    w = {n:water_index(m) for n,m in db.items()}
    
    max_c = max(m["carbon-intensity"] for m in db.values()) or 1
    max_w = max(w.values()) or 1
    
    scores = {}
    for node, m in db.items():
        c_score = 100 * (1 - m["carbon-intensity"] / max_c)
        w_score = 100 * (1 - w[node] / max_w)
        scores[node] = (carbon_weight * c_score) + (water_weight * w_score)
    return scores

def evict_pod(name, namespace):
    body = client.V1Eviction(
    metadata=client.V1ObjectMeta(name=name, namespace=namespace)
    )
    v1.create_namespaced_pod_eviction(
        name=name, namespace=namespace, body=body, _preload_content=False
    )

def check_once():
    pods = v1.list_pod_for_all_namespaces(field_selector="status.phase=Running")
    
    for pod in pods.items:
        name, ns = pod.metadata.name, pod.metadata.namespace
        ann = pod.metadata.annotations or {}
        
        if ann.get(OPT_IN) != "true":
            continue
        if pod.spec.scheduler_name != SCHEDULER_NAME:
            continue
        if pod.metadata.deletion_timestamp: #already terminating
            continue
        if not pod.metadata.owner_references: #bare pod
            print(f"[SKIP] {name}: no owner (ReplicaSet) would not be recreated")
            continue
        
        owner_uid = pod.metadata.owner_references[0].uid
        if time.time() - last_moved.get(owner_uid, 0) < COOLDOWN_SECONDS:
            continue
        
        current = pod.spec.node_name
        scores = compute_scores(0.5,0.5)
        
        if current not in scores:
            print(f"[SKIP] {name}: node not in metrics.json file")
        
        best = max(scores, key=scores.get)
        gain = scores[best] - scores[current]
        
        print(f"[CHECK BEFORE EVICTION] ({name} on {current}): best node {best} | gain {gain:.2f}")
        
        if current != best and gain >= MIN_GAIN:
            try:
                evict_pod(name,ns)
                last_moved[owner_uid] = time.time()
                print(f"[EVICTED] {name}: moving away from {current} to {best}")
            except client.ApiException as e:
                print(f"[EVICT FAILED] {name}: {e.status} {e.reason}")

def main():
    print("Descheduler Started")
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"Check failed: {e}")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
    
