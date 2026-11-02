import json
p = "config_files/metrics.json"
m = json.load(open(p))
for n in m:
    if n.startswith("clean-wet"):
        m[n]["carbon-intensity"] = 900     # was 50
json.dump(m, open(p, "w"), indent=2)
print("flipped")
