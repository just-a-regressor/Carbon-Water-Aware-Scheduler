import json
import os
CARBON_WEIGHT = float(os.getenv("CARBON_WEIGHT",0.5))
WATER_WEIGHT = float(os.getenv("WATER_WEIGHT", 0.5))

def load_environmental_db():
    with open("config_files/metrics.json","r") as f:
        return json.load(f)
        
def water_index(m):
    direct = m["wue_direct"] * m["scarcity_site"]
    indirect = m["pue"] * m["ewif"] * m["scarcity_grid"]
    return direct + indirect
    
def score_nodes(carbon_weight = CARBON_WEIGHT, water_weight = WATER_WEIGHT):
    db = load_environmental_db()
    w = {n : water_index(m) for n, m in db.items()}
    
    max_c = max(m["carbon-intensity"] for m in db.values()) or 1
    max_w = max(w.values()) or 1
    
    results = {}
    for node_name, metrics in db.items():
        c_intensity = metrics["carbon-intensity"]
        w_scarcity = w[node_name]
        carbon_score = 100 * (1 - c_intensity / max_c)
        water_score = 100 *( 1 -  w_scarcity / max_w)
        results[node_name] = {
            "carbon": c_intensity,
            "water":w_scarcity,
            "score":carbon_weight * carbon_score + water_weight * water_score
        }
    return results
