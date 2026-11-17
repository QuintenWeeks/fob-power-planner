"""
Local server for the FOB Power Planner.
Run:  python server.py     then open http://127.0.0.1:8481
Endpoints
  GET  /api/climate?lat=..&lon=..   NASA POWER hourly sun + temperature, cut into seasonal days (cached)
  POST /api/evaluate               fast nonlinear simulation of one design (live sliders)
  POST /api/optimize               heuristic screen + model (U) on the best designs
  POST /api/solve_full             model (U) with every design decision free (monolithic, time-limited)
Only coordinates are sent to NASA. Everything else stays on this laptop.
"""
import json
import math
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model  # noqa: E402

DATA = os.path.join(HERE, "data")
PORT = int(os.environ.get("FOB_PORT", "8481"))
YEAR = 2023


def season_of(month, lat):
    s = {12: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 3, 10: 3, 11: 3}[month]
    if lat < 0:
        s = (s + 2) % 4
    return s   # 0 winter, 1 spring, 2 summer, 3 fall (local hemisphere)


def fetch_climate(lat, lon):
    lat = round(float(lat), 2); lon = round(float(lon), 2)
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"climate_{lat}_{lon}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    url = ("https://power.larc.nasa.gov/api/temporal/hourly/point?parameters=ALLSKY_SFC_SW_DWN,T2M"
           f"&community=RE&longitude={lon}&latitude={lat}&start={YEAR}0101&end={YEAR}1231&format=JSON&time-standard=LST")
    req = urllib.request.Request(url, headers={"User-Agent": "fob-power-planner/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = json.load(r)
    par = raw["properties"]["parameter"]
    ghi = par["ALLSKY_SFC_SW_DWN"]; t2m = par["T2M"]
    days = {}
    for key, g in ghi.items():
        d, h = key[:8], int(key[8:10])
        days.setdefault(d, [[0.0] * 24, [0.0] * 24])
        days[d][0][h] = max(0.0, g) if g > -900 else 0.0
        tv = t2m.get(key, 20)
        days[d][1][h] = tv if tv > -900 else 20.0
    seasons = [[] for _ in range(4)]
    for d in sorted(days):
        seasons[season_of(int(d[4:6]), lat)].append({"date": d, "ghi": days[d][0], "temp": days[d][1]})
    # order each season so day 1 is the median-sun day, then alternate around it (typical first)
    out_seasons = []
    for s in seasons:
        s.sort(key=lambda x: sum(x["ghi"]))
        mid = len(s) // 2
        order = [mid]
        for i in range(1, len(s)):
            for j in (mid + i, mid - i):
                if 0 <= j < len(s) and len(order) < 14:
                    order.append(j)
        out_seasons.append([s[j] for j in order[:14]])
    res = {"lat": lat, "lon": lon, "year": YEAR, "source": "NASA POWER hourly (ALLSKY_SFC_SW_DWN, T2M), LST",
           "seasons": out_seasons,
           "annual_ghi_kwh_m2": round(sum(sum(v[0]) for v in days.values()) / 1000, 1)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f)
    return res


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.path = "/index.html"
            return super().do_GET()
        if u.path == "/api/ping":
            return self._json({"ok": True})
        if u.path == "/api/climate":
            q = parse_qs(u.query)
            try:
                return self._json({"ok": True, "climate": fetch_climate(q["lat"][0], q["lon"][0])})
            except Exception as e:  # offline or bad point
                return self._json({"ok": False, "message": f"Climate fetch failed: {e}"}, 200)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/evaluate":
                return self._json({"ok": True, "result": model.evaluate(body["inp"], body["design"])})
            if u.path == "/api/optimize":
                return self._json(model.optimize(body["inp"]))
            if u.path == "/api/solve_full":
                inp = dict(body["inp"]); inp["time_limit"] = float(body.get("time_limit", 30))
                return self._json(model.solve(inp))
        except Exception as e:
            import traceback
            return self._json({"ok": False, "message": str(e), "trace": traceback.format_exc()[-1500:]}, 500)
        self._json({"ok": False, "message": "unknown endpoint"}, 404)


if __name__ == "__main__":
    HOST = os.environ.get("FOB_HOST", "127.0.0.1")   # Codespaces sets 0.0.0.0 so the port can be forwarded
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"FOB Power Planner on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    srv.serve_forever()
