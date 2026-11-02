"""

  python3 build_water_table.py --data aqueduct_provinces.csv --inspect   # show columns + sample rows
  python3 build_water_table.py --data aqueduct_provinces.csv --dry-run   # show what would change
  python3 build_water_table.py --data aqueduct_provinces.csv             # write sites.json

"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata

SITES = "config_files/sites.json"
COUNTRY_COLS = ("name_0", "country", "country_name", "name_country")
PROVINCE_COLS = ("name_1", "province", "province_name", "state", "name_province")
SCORE_COLS = ("bws_score", "score")
INDICATOR_COLS = ("indicator_name", "indicator")
WEIGHT_COLS = ("weight",)
BWS_NAMES = {"bws", "baseline water stress"}


def norm(s):
    """Lowercase, strip accents and extra spaces so 'São Paulo' == 'sao paulo'."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def read_rows(path, sheet=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit("reading .xlsx needs openpyxl (pip install openpyxl), or save the sheet as CSV")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        if sheet is None:       # the WRI workbook starts with a 'Read Me' sheet, so pick the province baseline one
            sheet = "province_baseline" if "province_baseline" in wb.sheetnames else wb.sheetnames[0]
        if sheet not in wb.sheetnames:
            raise SystemExit(f"sheet '{sheet}' not found. Sheets: {wb.sheetnames}")
        print(f"[WATER] reading sheet '{sheet}' of {os.path.basename(path)}")
        ws = wb[sheet]
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(it)]
        return [dict(zip(header, r)) for r in it]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def find_col(headers, candidates):
    lower = {h.lower().strip(): h for h in headers}
    for c in candidates:
        if c in lower:
            return lower[c]
    return None


class WaterTable:
    def __init__(self, rows):
        if not rows:
            raise SystemExit("the data file has no rows")
        headers = list(rows[0].keys())
        self.country = find_col(headers, COUNTRY_COLS)
        self.province = find_col(headers, PROVINCE_COLS)
        self.score = find_col(headers, SCORE_COLS)
        ind = find_col(headers, INDICATOR_COLS)
        wt = find_col(headers, WEIGHT_COLS)
        if not (self.country and self.score):
            raise SystemExit(f"could not find country/score columns in {headers}. Run with --inspect.")
        if ind:
            rows = [r for r in rows if norm(r.get(ind)) in BWS_NAMES] or rows
        if wt:
            tot = [r for r in rows if norm(r.get(wt)) in ("tot", "total")]
            if tot:
                rows = tot
            else:
                print(f"[WATER] warning: no 'Tot' rows in column '{wt}', using all rows", file=sys.stderr)
        self.rows = rows

    def lookup(self, country, province=None):
        """Return (score, matched_rows) or raise KeyError."""
        want_c, want_p = norm(country), norm(province)
        scores = []
        for r in self.rows:
            if norm(r.get(self.country)) != want_c:
                continue
            row_p = norm(r.get(self.province)) if self.province else ""
            if want_p and row_p != want_p:
                continue
            if not want_p and row_p:
                continue                       # country-level lookup: only rows without a province
            try:
                v = float(r.get(self.score))
            except (TypeError, ValueError):
                continue
            if 0 <= v <= 5:                    # Aqueduct uses -1 / -9999 for "no data"
                scores.append(v)
        if not scores:
            raise KeyError(f"{country}" + (f" / {province}" if province else "") + " not found (or no valid score)")
        return sum(scores) / len(scores), len(scores)


def write_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def resolve(table, loc):
    score, n = table.lookup(loc.get("country"), loc.get("province"))
    return round(score, 2), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Aqueduct 4.0 baseline water stress table (.csv or .xlsx)")
    ap.add_argument("--sites", default=SITES)
    ap.add_argument("--sheet", default=None,
                    help="sheet name for .xlsx files (default: 'province_baseline' if present, else the first sheet)")
    ap.add_argument("--inspect", action="store_true", help="print the detected columns and a few rows, then exit")
    ap.add_argument("--dry-run", action="store_true", help="show the changes without writing sites.json")
    args = ap.parse_args()

    rows = read_rows(args.data, args.sheet)
    if args.inspect:
        print(f"{len(rows)} rows. Columns: {list(rows[0].keys()) if rows else []}")
        for r in rows[:3]:
            print("  ", dict(list(r.items())[:10]))
        t = WaterTable(rows)
        print(f"detected -> country: {t.country}, province: {t.province}, score: {t.score}, rows kept: {len(t.rows)}")
        return

    table = WaterTable(rows)
    with open(args.sites) as f:
        data = json.load(f)

    failed = False
    print(f"{'region':14s} {'location':32s} {'old site':>8s} {'new site':>8s} {'old grid':>8s} {'new grid':>8s}")
    for name, s in data["regions"].items():
        site_loc = s.get("aqueduct")
        if not site_loc:
            print(f"{name:14s} {'(no aqueduct entry, unchanged)':32s}")
            continue
        grid_loc = s.get("aqueduct_grid") or site_loc
        try:
            site, n1 = resolve(table, site_loc)
            grid, n2 = resolve(table, grid_loc)
        except KeyError as e:
            print(f"{name:14s} ERROR: {e}", file=sys.stderr)
            failed = True
            continue
        label = f"{site_loc.get('province') or ''}, {site_loc['country']}".strip(", ")
        print(f"{name:14s} {label:32s} {s['scarcity_site']:>8} {site:>8} {s['scarcity_grid']:>8} {grid:>8}")
        if max(n1, n2) > 1:
            print(f"  note: averaged {max(n1, n2)} rows for {name}; check the file is province level, not sub-basin")
        s["scarcity_site"], s["scarcity_grid"] = site, grid

    if failed:
        raise SystemExit("some regions could not be resolved; sites.json was NOT written")
    if args.dry_run:
        print("dry run: sites.json not written")
    else:
        write_atomic(args.sites, data)
        print(f"wrote {args.sites}")


if __name__ == "__main__":
    main()
