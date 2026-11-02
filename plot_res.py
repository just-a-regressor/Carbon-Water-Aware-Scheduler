import argparse, glob, json, os, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ORDER = ["default","carbon_only","cw","cw_desched"]
LABEL = {"default" :"Default\nK8s", "carbon_only":"Carbon\nonly","cw": "Carbon\n+ water",
"cw_desched": "Carbon + water\n+ descheduler"}
FLAT = {m: l.replace("\n", " ") for m, l in LABEL.items()}
COLOR = {"default": "#8C8C8C", "carbon_only": "#E69F00", "cw": "#0072B2", "cw_desched": "#009E73"}
# regions: blues = clean grid, greys = coal grid (light = water-rich, dark = water-stressed), orange = real data
REGION_COLOR = {"clean-wet": "#56B4E9", "clean-dry": "#0072B2", "coal-wet": "#BDBDBD", "coal-dry": "#525252", "real-california": "#E69F00"}
SHORT = {"carbon-intensity": "carbon", "wue_direct": "WUE", "scarcity_site": "site scarcity",
         "scarcity_grid": "grid scarcity"}

def style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color="#E3E3E3", linewidth=0.8)
    ax.set_axisbelow(True)

def bars(ax, df, col, title, ylabel):
    modes = [m for m in ORDER if m in set(df["mode"])]
    g = df.groupby("mode")[col]
    mean, std = g.mean().reindex(modes), g.std(ddof=1).reindex(modes).fillna(0)
    x = range(len(modes))
    ax.bar(x, mean, yerr=std if (std > 0).any() else None, capsize=4, width=0.62,
           color=[COLOR[m] for m in modes], edgecolor="none", error_kw=dict(ecolor="#333333", lw=1.2))
    for i, m in enumerate(modes):                      # individual reps as dots
        pts = df.loc[df["mode"] == m, col]
        ax.scatter([i] * len(pts), pts, s=14, color="white", edgecolor="#333333", linewidth=0.8, zorder=3)
    for i, m in enumerate(modes):                      # value labels, placed above whiskers and dots
        top = max(mean.iloc[i] + std.iloc[i], df.loc[df["mode"] == m, col].max())
        ax.annotate(f"{mean.iloc[i]:.1f}", (i, top), ha="center", va="bottom", fontsize=8,
                    xytext=(0, 6), textcoords="offset points")
    ax.margins(y=0.12)
    ax.set_xticks(list(x))
    ax.set_xticklabels([LABEL[m] for m in modes], fontsize=8)
    ax.set_title(title, fontsize=11, loc="left", fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=9)
    style(ax)
    
def save(fig, out, name, rect=None):
    fig.tight_layout(rect=rect) if rect else fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out, f"{name}.{ext}"), dpi=200)
    plt.close(fig)
    print(f"wrote {out}/{name}.png (+ .pdf)")

def load_timeseries(runs_dir):
    """{mode: {rep: DataFrame}} from runs/<mode>_<rep>/timeseries.csv"""
    out = {}
    for path in glob.glob(os.path.join(runs_dir, "*_*", "timeseries.csv")):
        mode, _, rep = os.path.basename(os.path.dirname(path)).rpartition("_")
        if mode in ORDER and rep.isdigit():
            out.setdefault(mode, {})[int(rep)] = pd.read_csv(path)
    return out

def load_events(path, speed):
    """[(seconds into the run, number, description)] from scenario.json"""
    if not path or not os.path.exists(path):
        return []
    events = sorted(json.load(open(path))["events"], key=lambda e: e["at"])
    out = []
    for i, ev in enumerate(events, 1):
        if ev.get("restore"):
            text = f"{ev['region']} back to normal"
        else:
            text = f"{ev['region']}: " + ", ".join(f"{SHORT.get(k, k)} = {v}" for k, v in ev["set"].items())
        out.append((ev["at"] / speed, i, text))
    return out

def mark_events(ax, events):
    for t, i, _ in events:
        ax.axvline(t, color="#555555", ls="--", lw=0.8, zorder=1)
        ax.annotate(str(i), (t, 1.0), xycoords=("data", "axes fraction"), ha="center", va="bottom", fontsize=8, fontweight="bold", color="#333333", xytext=(0, 2), textcoords="offset points")

def event_note(events):
    return " ".join(f"({i}) t={t:0f}s {text}" for t, i, text in events)

def mean_curve(dfs, col, step):
    tmax = min(d["t"].max() for d in dfs)
    grid = np.arange(0, tmax + 1e-9, step)
    ys = np.array([np.interp(grid, d["t"], d[col]) for d in dfs])
    return grid, ys.mean(axis=0), (ys.std(axis=0, ddof=1) if len(ys) > 1 else np.zeros_like(grid))
    
def fig_placement(ts, events, rep, out):
    modes = [m for m in ORDER if m in ts]
    n = len(modes)
    fig, axes = plt.subplots(n, 1, figsize=(10, 2.0 * n + 1.3), sharex = True, sharey = True, squeeze = False)
    axes = axes[:, 0]
    ymax, handles = 0, {}
    for ax, m in zip(axes, modes):
        reps = ts[m]
        df = reps[rep] if rep in reps else reps[sorted(reps)[0]]
        cols = [c for c in df.columns if c.startswith("pods_")]
        regions = [c[5:] for c in cols]
        ys = [df[c] for c in cols]
        polys = ax.stackplot(df["t"], ys, colors=[REGION_COLOR.get(r, '#999999') for r in regions], edgecolor="white", linewidth=0.4)
        handles = dict(zip(regions, polys))
        ymax = max(ymax, df[cols].sum(axis=1).max())
        mark_events(ax,events)
        ax.set_title(FLAT[m], fontsize=10,loc="left", fontweight="bold", pad=12)
        ax.set_ylabel("pods", fontsize=9)
    for ax in axes:
        ax.set_ylim(0, ymax * 1.05)
    axes[-1].set_xlabel("time (s)", fontsize=9)
    fig.legend(handles.values(), handles.keys(), loc="lower center", ncol=len(handles), frameon=False, fontsize=8,
               bbox_to_anchor=(0.5, 0.045))
    fig.text(0.01, 0.01, f"Pods per region over time (rep {rep} where available)" +
             (".  Events: " + event_note(events) if events else ""), fontsize=7, color="#555555", wrap=True)
    save(fig, out, "fig4_placement", rect=(0, 0.1, 1, 1))
 
 
def fig_rates(ts, events, step, out):
    modes = [m for m in ORDER if m in ts]
    n_reps = min(len(ts[m]) for m in modes)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharex=True)
    for ax, col, title, yl in ((axes[0], "carbon_rate", "Carbon rate", "relative carbon units / hour"),
                               (axes[1], "water_rate", "Water rate", "relative water units / hour")):
        for m in modes:
            grid, mean, sd = mean_curve(list(ts[m].values()), col, step)
            ax.plot(grid, mean, color=COLOR[m], lw=1.8, label=FLAT[m])
            if (sd > 0).any():
                ax.fill_between(grid, mean - sd, mean + sd, color=COLOR[m], alpha=0.15, linewidth=0)
        mark_events(ax, events)
        ax.set_title(title, fontsize=11, loc="left", fontweight="bold", pad=12)
        ax.set_ylabel(yl, fontsize=9)
        ax.set_xlabel("time (s)", fontsize=9)
        ax.grid(axis="x", visible=False)
        style(ax)
    h, l = axes[0].get_legend_handles_labels()          # legend below the plots so it never covers a line
    fig.legend(h, l, loc="lower center", ncol=len(l), frameon=False, fontsize=8, bbox_to_anchor=(0.5, 0.045))
    note = f"Mean over {n_reps} rep(s)" + ("; band = std" if n_reps > 1 else "") + \
           (".  Events: " + event_note(events) if events else "")
    fig.text(0.01, 0.01, note, fontsize=7, color="#555555", wrap=True)
    save(fig, out, "fig5_rates", rect=(0, 0.1, 1, 1))
 
 
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs", help="folder written by run_experiment.py")
    ap.add_argument("--scenario", default=None, help="scenario.json (default: scenario.json or config_files/scenario.json)")
    ap.add_argument("--speed", type=float, default=1.0, help="the --speed you gave run_experiment.py")
    ap.add_argument("--rep", type=int, default=1, help="which rep to show in the placement figure")
    ap.add_argument("--step", type=float, default=2.0, help="time grid (s) for averaging reps")
    ap.add_argument("--out", default="figures")
    args = ap.parse_args()
 
    df = pd.read_csv(os.path.join(args.runs, "results.csv"))
    os.makedirs(args.out, exist_ok=True)
    n_reps = df.groupby("mode")["rep"].nunique().min()
    note = f"n = {n_reps} rep(s) per mode" + ("; whiskers = std" if n_reps > 1 else "; no spread with one rep")
 
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    bars(axes[0], df, "carbon_units", "Carbon", "carbon, relative units (lower is better)")
    bars(axes[1], df, "water_units", "Water", "water, relative units (lower is better)")
    fig.text(0.01, 0.01, note, fontsize=7, color="#555555")
    save(fig, args.out, "fig1_carbon_water")
 
    if "default" in set(df["mode"]):
        base = df[df["mode"] == "default"][["carbon_units", "water_units"]].mean()
        mean = df.groupby("mode")[["carbon_units", "water_units"]].mean()
        pct = (mean / base - 1) * 100
        modes = [m for m in ORDER if m in pct.index and m != "default"]
        fig, ax = plt.subplots(figsize=(7, 4))
        w = 0.36
        for j, (col, hatch) in enumerate([("carbon_units", None), ("water_units", "//")]):
            xs = [i + (j - 0.5) * w for i in range(len(modes))]
            vals = [pct.loc[m, col] for m in modes]
            ax.bar(xs, vals, width=w, color=[COLOR[m] for m in modes], hatch=hatch, edgecolor="white")
            for x, v in zip(xs, vals):
                ax.annotate(f"{v:+.0f}%", (x, v), ha="center", va="top" if v < 0 else "bottom",
                            fontsize=8, xytext=(0, -3 if v < 0 else 3), textcoords="offset points")
        ax.axhline(0, color="#333333", lw=0.8)
        ax.margins(y=0.18)                              # room for the % labels at both ends
        ax.set_xticks(range(len(modes)))
        ax.set_xticklabels([LABEL[m] for m in modes], fontsize=8)
        ax.set_ylabel("change vs default scheduler (%)", fontsize=9)
        ax.set_title("Carbon and water relative to the default scheduler", fontsize=11, loc="left", fontweight="bold")
        handles = [plt.Rectangle((0, 0), 1, 1, fc="#BBBBBB", ec="white", label="Carbon (solid)"),
                   plt.Rectangle((0, 0), 1, 1, fc="#BBBBBB", ec="white", hatch="//", label="Water (hatched)")]
        ax.legend(handles=handles, fontsize=8, frameon=False, loc="best")
        style(ax)
        fig.text(0.01, 0.01, note, fontsize=7, color="#555555")
        save(fig, args.out, "fig2_reduction")
 
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    bars(axes[0], df, "evictions", "Evictions", "pods evicted")
    bars(axes[1], df, "pending_pod_seconds", "Scheduling delay", "pending pod-seconds")
    bars(axes[2], df, "peak_pods_on_one_node", "Packing", "peak pods on one node")
    fig.text(0.01, 0.01, note, fontsize=7, color="#555555")
    save(fig, args.out, "fig3_operational")
 
    ts = load_timeseries(args.runs)
    if not ts:
        print("no runs/<mode>_<rep>/timeseries.csv found: skipping fig4 and fig5")
        return
    scen = args.scenario or next((p for p in ("scenario.json", "config_files/scenario.json") if os.path.exists(p)), None)
    events = load_events(scen, args.speed)
    if not events:
        print("scenario.json not found: timeline figures will have no event markers")
    fig_placement(ts, events, args.rep, args.out)
    fig_rates(ts, events, args.step, args.out)
 
 
if __name__ == "__main__":
    main()


