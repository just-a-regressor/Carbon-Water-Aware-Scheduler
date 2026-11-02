import  json
import time, datetime
import csv, os
from scoring import score_nodes,CARBON_WEIGHT,WATER_WEIGHT
from placement import cluster_state, choose_node, pod_requests
from kubernetes import client, config, watch

try:
	config.load_kube_config()
except Exception:
	config.load_incluster_config()

v1 = client.CoreV1Api()
SCHEDULER_NAME = "cwScheduler"
LOG_PATH = "log_files/simulation_log.csv"

VERBOSE = os.getenv("VERBOSE","false").lower() == "true"

def log_scheduling(pod_name,node_name,carbon_intensity, water_scarcity, total_score):
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH,"a",newline="") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["Pod Name","Node Name","Carbon Intensity","Water Scarcity","Total Score","TimeStamp"])
        time_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([pod_name,node_name,carbon_intensity, water_scarcity, f"{total_score:.2f}",time_stamp])

        
def send_pod_to_node(pod_name, namespace_name, node_name):
    node_target = client.V1ObjectReference(
        api_version="v1",
        kind="Node",
        name=node_name
    )
    
    target_binding = client.V1Binding(
        metadata=client.V1ObjectMeta(name=pod_name),
        target=node_target
    )
    
    resp = v1.create_namespaced_pod_binding(name=pod_name, namespace=namespace_name, body=target_binding,
    _preload_content=False)
    #print(resp.status, resp.data)
    
def schedule_one(pod):
    name, ns = pod.metadata.name, pod.metadata.namespace
    try:
        results = score_nodes(carbon_weight=CARBON_WEIGHT, water_weight=WATER_WEIGHT)
        state = cluster_state(v1)
        winner = choose_node(results, state, pod_requests(pod))
        
        if VERBOSE:
            for node, s in results.items():
                print(f'  {node}: carbon {s["carbon"]:.2f} | water {s["water"]:.2f} | eco {s["score"]:.2f}')
        if winner is None:
            print(f"[PENDING] {name}: no ready node with free capacity, searching again")
            return
        
        r = results[winner]
        send_pod_to_node(name, ns, winner)
        log_scheduling(name, winner, r["carbon"], r["water"], r["score"])
        print(f'[BOUND] {name} -> {winner} (eco {r["score"]:.2f}, node load {state[winner]["pods"] + 1} pods)')
    except Exception as e:
        print(f"Scheduling failed for {name}: {e}")

def main():
    print(f"CW-Scheduler started, looking for the schedulerTag {SCHEDULER_NAME}")
    while True:
        try:
            wa = watch.Watch()
            for event in wa.stream(
                v1.list_pod_for_all_namespaces,
                field_selector=f"spec.schedulerName={SCHEDULER_NAME},status.phase=Pending",
                timeout_seconds=30,
            ):
                pod = event["object"]
                if event["type"] == "DELETED" or pod.spec.node_name:
                    continue
                schedule_one(pod)
        except Exception as e:
            print(f"Watch error: {e}, restarting watch")
        time.sleep(1)
                
if __name__ == "__main__":
    main()
    
