import  json
import time, datetime
import csv
from kubernetes import client, config, watch

try:
	config.load_kube_config()
except Exception:
	config.load_incluster_config()

v1 = client.CoreV1Api()
SCHEDULER_NAME = "cwScheduler"

def load_environmental_db():
    with open("metrics.json","r") as f:
        return json.load(f)
        
def water_index(m):
    direct = m["wue_direct"] * m["scarcity_site"]
    indirect = m["pue"] * m["ewif"] * m["scarcity_grid"]
    return direct + indirect
    

def score_nodes(carbon_weight = 0.5, water_weight = 0.5):
    db = load_environmental_db()
    w = {n : water_index(m) for n, m in db.items()}
    
    max_c = max(m["carbon-intensity"] for m in db.values()) or 1
    max_w = max(w.values()) or 1
    
    
    best_score = float("-inf")
    winning_node = None
    
    for node_name, metrics in db.items():
        c_intensity = metrics["carbon-intensity"]
        w_scarcity = water_index(metrics)
        
        carbon_score = 100 * (1 - c_intensity / max_c)
        water_score = 100 *( 1 -  w_scarcity / max_w)
        
        total_score = (carbon_weight * carbon_score) + (water_weight * water_score)
        
        print(f"Node : {node_name} | Carbon Impact : {c_intensity:.3f} | Water Impact : {w_scarcity:.3f} | Total Eco Score : {total_score:.2f}")
        
        if total_score > best_score:
            best_score, winning_node = total_score, node_name
    
    print(f"Next Scheduled Node {winning_node} (Score : {best_score:.2f})")
    
    return winning_node
    
def log_scheduling(pod_name,node_name,carbon_intensity, water_scarcity, total_score):
    file_exists = False
    try:
        with open("simulation_log.cv","r") as f:
            file_exists = True
    except:
        file_exists = False
    
    with open("simulation_log.cv","a",newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
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
    print(resp.status, resp.data)
    print(f"Successfully bound Pod '{pod_name}' to the node '{node_name}'")
   

def main():
    print(f"CW-Scheduler Started looking for the schedulerTag {SCHEDULER_NAME}")
    w = watch.Watch()
    
    for event in w.stream(v1.list_pod_for_all_namespaces):
        pod = event["object"]
        
        if(pod.status.phase == "Pending" and pod.spec.scheduler_name == SCHEDULER_NAME and not pod.spec.node_name):
            print(f"[DETECTED] Pod Found For Scheduling: {pod.metadata.name}")
            
            
            winning_region = score_nodes(carbon_weight = 0.5, water_weight = 0.5)
            
            try:
                send_pod_to_node(pod.metadata.name, pod.metadata.namespace, winning_region)
                
                db = load_environmental_db()
                w = {n : water_index(m) for n, m in db.items()}
                max_c = max(m["carbon-intensity"] for m in db.values()) or 1
                max_w = max(w.values()) or 1
                
                winner_metrics = db[winning_region]
                carbon_intensity = winner_metrics["carbon-intensity"]
                water_scarcity = water_index(winner_metrics)
                carbon_score = 100 * (1 - carbon_intensity / max_c)
                water_score = 100 *( 1 -  water_scarcity / max_w)
                total_score = (0.5 * carbon_score) + (0.5 * water_score)
                
                log_scheduling(pod.metadata.name,winning_region,carbon_intensity,water_scarcity, total_score)
                
            except Exception as e:
                print(f"Binding failed: {e}")
                
if __name__ == "__main__":
    main()
    
