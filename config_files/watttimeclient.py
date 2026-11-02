"""
Set your credentials once per terminal:
    export WATTTIME_USER="your_username"
    export WATTTIME_PASSWORD="your_password"
    Then check what works for YOUR account before wiring anything else:
    python3 watttime_client.py --my-access            # regions + signals your plan includes
    python3 watttime_client.py --probe CAISO_NORTH    # raw forecast JSON + the parsed current value
    python3 watttime_client.py --locate 37.77 -122.42 # grid region for a lat/lon (may need a paid plan)"""
    

import argparse, json, os, time, requests

BASE = "https://api.watttime.org"
LBS_PER_MWH_TO_G_PER_KWH = 0.453592
TOKEN_LIFETIME = 25*60

class WattTimeError(Exception):
    pass

class WattTimeClient:
    def __init__(self, username=None, password=None, session=None, base=BASE):
        self.user = username or os.getenv("WATTTIME_USER")
        self.pw = password or os.getenv("WATTTIME_PASSWORD")
        if not (self.user and self.pw):
            raise WattTimeError("set WATTTIME_USER and WATTTIME_PASSWORD in your environment")
        self.http = session or requests.Session()
        self.base = base
        self._token = None
        self._token_time = 0.0
    
    def _login(self):
        r = self.http.get(f"{self.base}/login", auth=(self.user, self.pw), timeout=15)
        if r.status_code != 200:
            raise WattTimeError(f"login failed: HTTP {r.status_code} {r.text[:200]}")
        self._token = r.json()["token"]
        self._token_time = time.time()
    
    def _get(self, path, params=None, _retry=True):
        if not self._token or time.time() - self._token_time > TOKEN_LIFETIME:
            self._login()
        r = self.http.get(f"{self.base}{path}", headers={"Authorization": f"Bearer {self._token}"},
                          params=params or {}, timeout=20)
        if r.status_code == 401 and _retry:             # token expired early: log in again once
            self._token = None
            return self._get(path, params, _retry=False)
        if r.status_code == 429:
            raise WattTimeError("rate limited (HTTP 429): wait for the next clock minute")
        if r.status_code >= 400:
            raise WattTimeError(f"HTTP {r.status_code} from {path}: {r.text[:200]}")
        return r.json()
    
    def my_access(self):
        """Raw /v3/my-access JSON."""
        return self._get("/v3/my-access")
    
    def list_regions(self, signal_type="co2_moer"):
        """[(region_code, full_name)] that your plan can read for this signal."""
        out = []
        for sig in self.my_access().get("signal_types", []):
            if sig.get("signal_type") == signal_type:
                for reg in sig.get("regions", []):
                    out.append((reg.get("region"), reg.get("region_full_name")))
        return out
    
    def region_from_loc(self, lat, lon, signal_type="co2_moer"):
        j = self._get("/v3/region-from-loc",
                      {"latitude": lat, "longitude": lon, "signal_type": signal_type})
        return j.get("region"), j.get("region_full_name")
    
    def forecast_raw(self, region, signal_type="co2_moer"):
        return self._get("/v3/forecast", {"region": region, "signal_type": signal_type})
 
    def current_moer(self, region, signal_type="co2_moer"):
        """Return (value in lbs CO2/MWh, point_time) for the current 5-minute period."""
        j = self.forecast_raw(region, signal_type)
        data = j.get("data") or []
        if not data or "value" not in data[0]:
            raise WattTimeError(f"unexpected forecast response for {region}: {json.dumps(j)[:300]}")
        return float(data[0]["value"]), data[0].get("point_time")
 
 def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--my-access", action="store_true")
    g.add_argument("--probe", metavar="REGION")
    g.add_argument("--locate", nargs=2, type=float, metavar=("LAT", "LON"))
    args = ap.parse_args()
 
    wt = WattTimeClient()
    if args.my_access:
        regions = wt.list_regions()
        print(f"{len(regions)} co2_moer regions on your plan:")
        for code, name in regions:
            print(f"  {code:20s} {name}")
    elif args.probe:
        raw = wt.forecast_raw(args.probe)
        print(json.dumps(raw, indent=1)[:900], "\n...")
        lbs, t = wt.current_moer(args.probe)
        print(f"\ncurrent MOER for {args.probe}: {lbs:.1f} lbs CO2/MWh = "
              f"{lbs * LBS_PER_MWH_TO_G_PER_KWH:.1f} g CO2/kWh  (period {t})")
    else:
        print(wt.region_from_loc(*args.locate))
 
 
if __name__ == "__main__":
    main()
