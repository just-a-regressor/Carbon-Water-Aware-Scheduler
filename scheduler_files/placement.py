#shared placement helpers are in this script
from kubernetes.utils import parse_quantity

#the nodes whose eco score is within this many points of the best candidate count as "tied". among these tied ones the least-loaded one wins
TIE_BAND = 2.0

def pod_requests(pod):
    cpu = mem = 0.0
    for c in pod.spec.containers:
        req = (c.resources.requests or {}) if c.resources else {}
        cpu += float(parse_quantity(req.get("cpu","0")))
        mem += float(parse_quantity(req.get("memory","0")))
    return cpu,mem

def cluster_state(v1):
    nodes = {}
    for n in v1.list_node().items:
        if n.spec.unschedulable:
            continue
        if any(t.effect in ("NoSchedule", "NoExecute") for t in (n.spec.taints or [])):
            continue
        if not any(c.type == "Ready" and c.status == "True" for c in (n.status.conditions or [])):
            continue
        a = n.status.allocatable or {}
        nodes[n.metadata.name] = {
            "cpu": float(parse_quantity(a.get("cpu","0"))),
            "mem":float(parse_quantity(a.get("memory","0"))),
            "pods_max": int(a.get("pods",110)),
            "cpu_used": 0.0,
            "mem_used": 0.0,
            "pods": 0,
        }
        
    for p in v1.list_pod_for_all_namespaces().items:
        name = p.spec.node_name
        if name not in nodes:
            continue
        if p.status.phase in ("Succeeded", "Failed"):
            continue
        cpu, mem = pod_requests(p)
        nodes[name]["cpu_used"] += cpu
        nodes[name]["mem_used"] += mem
        nodes[name]["pods"] += 1
    return nodes

def fits(st,req):
    cpu, mem = req
    return (
        st["cpu_used"] + cpu <= st["cpu"]
        and st["mem_used"] + mem <= st["mem"]
        and st["pods"] + 1 <= st["pods_max"]
    )

def load(st):
    return st["cpu_used"] / st["cpu"] if st["cpu"] else 1.0

def choose_node(results, state, req, exclude=()):
    
    """Conditions for picking a node
        1. The candidates that have an eco score
        2. the best candidate amongst them
        3. among nodes within TIE_BAND points of that score, take the least loaded (CPU fraction, then pod count, then name for a stable order).
        returns the node name or none if noting fits"""
    
    cands = {
        n: results[n]["score"]
        for n in state
        if n in results and n not in exclude and fits(state[n], req)
    }
    
    if not cands:
        return None
    top = max(cands.values())
    tied = [n for n, s in cands.items() if top -s <= TIE_BAND]
    return min(tied, key=lambda n: (load(state[n]), state[n]["pods"], n))
