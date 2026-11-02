""" python3 gen_cluster.py
python3 gen_cluster.py --sites other.json
"""
import json, argparse, os

NODES_PER_REGION = 5
REQUIRED = ("carbon_fallback", "scarcity_site", "scarcity_grid", "pue", "wue_direct", "ewif")

NODE = """apiVersion: v1
kind: Node
metadata:
  name: {name}
  annotations:
    node.alpha.kubernetes.io/ttl: "0"
    kwok.x-k8s.io/node: fake
  labels:
    kubernetes.io/hostname: {name}
    kubernetes.io/os: linux
    kubernetes.io/arch: amd64
    type: kwok
    cw/region: {region}
status:
  allocatable: {{cpu: "4", memory: 256Gi, pods: "110"}}
  capacity: {{cpu: "4", memory: 256Gi, pods: "110"}}
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="config_files/sites.json")
    args = ap.parse_args()
    
    with open(args.sites) as f:
        regions = json.load(f)["regions"]
    
    docs, metrics = [], {}
    for region, p in regions.items():
        for k in REQUIRED:
            if p.get(k) is None:
                raise SystemExit(f"{args.sites}: region '{region}' is missing '{k}'")
        for i in range(p.get("nodes", NODES_PER_REGION)):
            name = f"{region}-{i}"
            docs.append(NODE.format(name=name, region=region))
            metrics[name] = {
                "carbon-intensity": p["carbon_fallback"],
                "wue_direct": p["wue_direct"], "scarcity_site":p["scarcity_site"],
                "pue": p["pue"], "ewif": p["ewif"], "scarcity_grid": p["scarcity_grid"],
            }
    with open("nodes.yaml", "w") as f:
        f.write("---\n".join(docs))
    os.makedirs("config_files", exist_ok=True)
    with open("config_files/metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"{len(metrics)} nodes writen from {args.sites} ({len(regions)} regions)")

if __name__ == "__main__":
    main()
