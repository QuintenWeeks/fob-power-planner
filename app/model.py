"""
FOB hybrid power design-and-dispatch model.

Implements the linearized model (U) of Scioletti, Newman, Goodman, Zolan & Leyffer (2017),
"Optimal design and dispatch of a system of diesel generators, photovoltaics and batteries for
remote locations", Optimization and Engineering 18:755-792 (draft read: OSTI 1393847).

What is taken from the paper (equation numbers refer to the paper):
  objective (1); load balance with overage (2a); spinning reserve (2b); symmetry (2c, 3d, 3e);
  generator bounds and linear fuel curve with a_f = 0 (3a-3c); PV (4a-4b); battery SOC (5c-5d),
  current bounds (5j-5n); exact binary x continuous linearization Y (24a-24e); McCormick
  under-estimator Z for SOC x current (25a-25h); battery power (20-21); lifecycle (6a, 6c, 23).

What this project adds (all marked ADD below):
  * load split into three priority tiers with an unserved-load penalty per tier (Tier 1 very high);
  * optional host-nation grid import with an availability pattern (existing FOB);
  * generators already owned (existing FOB) carry zero procurement cost;
  * representative-day horizon: each day is a block with SOC starting at b0 and ending >= b0,
    and each hour carries a weight that scales it to the FOB's operating period.

Approximation error is reported the same way as the paper's Section 4.4 (eqs. 26-29), plus a
re-simulation of the chosen design under the nonlinear battery equations (12-13, 6b).
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import coo_matrix


class Builder:
    def __init__(self):
        self.lb, self.ub, self.c, self.integ, self.names = [], [], [], [], []
        self.rows, self.cols, self.vals, self.rlo, self.rhi = [], [], [], [], []
        self.nrow = 0

    def var(self, name, lb=0.0, ub=np.inf, cost=0.0, integer=False):
        self.lb.append(lb); self.ub.append(ub); self.c.append(cost)
        self.integ.append(1 if integer else 0); self.names.append(name)
        return len(self.lb) - 1

    def con(self, terms, lo=-np.inf, hi=np.inf):
        for j, v in terms:
            if v != 0:
                self.rows.append(self.nrow); self.cols.append(j); self.vals.append(v)
        self.rlo.append(lo); self.rhi.append(hi); self.nrow += 1

    def solve(self, time_limit=60, gap=0.02):
        n = len(self.lb)
        A = coo_matrix((self.vals, (self.rows, self.cols)), shape=(self.nrow, n)).tocsr()
        res = milp(c=np.array(self.c), constraints=LinearConstraint(A, self.rlo, self.rhi),
                   integrality=np.array(self.integ), bounds=Bounds(self.lb, self.ub),
                   options={"time_limit": time_limit, "mip_rel_gap": gap, "disp": False})
        return res


def battery_params(b):
    """Paper Table 3 scaled per kWh (A123 Li-ion). cref [Ah] = 4.52 Ah per kWh nominal."""
    kwh = b["kwh"]
    cref = b.get("cref", 4.52 * kwh)
    p = dict(
        cref=cref,
        pbar=b.get("kw", kwh),                      # paper: pbar (W) equals kWh rating
        av=10.62, bv=214.69,                        # voltage slope / intercept [V]
        rint=b.get("rint", 0.84 / kwh),             # 0.0042 ohm at 200 kWh
        cminus=0.0401, cplus=3.0,
        asoc=0.801, dsoc=-0.801,
        eta_in=b.get("eta_in", 0.95), eta_out=b.get("eta_out", 0.95),
        life=b.get("life_cycles", 1647), smin=b.get("smin", 0.0), smax=b.get("smax", 1.0),
        b0=b.get("b0", 0.5),
    )
    p["iU_minus"] = cref / (p["cminus"] + 1.0)
    p["iU_plus"] = cref / p["cplus"]
    p["iavg"] = cref                                # tau * iavg = cref (Table 3 note)
    return p


def solve(inp: dict) -> dict:
    t0 = time.time()
    loads = np.array(inp["tier_loads"], dtype=float)        # 3 x T  [kW]
    T = loads.shape[1]
    w = np.array(inp.get("weights", [1.0] * T), dtype=float)  # hours represented by each period
    day_len = int(inp.get("block_len", 24))
    pv1 = np.array(inp["pv_per_kw"], dtype=float)            # kW per 1-kW panel
    fuel = float(inp["fuel_cost"])                           # $/gal (fully burdened)
    kbar = float(inp.get("overage", 0.3)); ks = float(inp.get("spin", 0.3))
    pen = inp.get("tier_penalty", [100000.0, 50.0, 5.0])     # $/kWh unserved
    eps = float(inp.get("cycle_cost", 1.0))
    gens = inp["generators"]; bats = inp["batteries"]; pv = inp["pv"]
    grid = inp.get("grid", {"enabled": False})
    allow_shed = inp.get("allow_shed", [False, True, True])

    B = Builder()
    # ---------------- design variables ----------------
    Wg = []  # (type index, twin index, var)
    for gi, g in enumerate(gens):
        prev = None
        for k in range(int(g["max_count"])):
            owned = k < int(g.get("owned", 0))
            v = B.var(f"W_{g['name']}_{k}", 0, 1, 0.0 if owned else g["cost"], True)
            if owned:
                B.lb[v] = 1.0                      # ADD: equipment already on the FOB
            if prev is not None:
                B.con([(v, 1), (prev, -1)], hi=0)  # (2c)
            Wg.append((gi, k, v)); prev = v
    Wb = []
    for bi, b in enumerate(bats):
        v = B.var(f"W_{b['name']}", 0, 1, b["cost"], True)
        if b.get("owned"):
            B.lb[v] = 1.0; B.c[v] = 0.0
        Wb.append(v)
    if Wb:
        B.con([(v, 1) for v in Wb], hi=1)          # one battery bank (paper Section 3.1)
    X = B.var("X_pv", 0, float(pv["max_panels"]), pv["cost"], True)
    if pv.get("owned", 0):
        B.lb[X] = float(pv["owned"])
    BP = [battery_params(b) for b in bats]

    # ---------------- lifecycle variables ----------------
    Lg = {}
    for gi, k, wv in Wg:
        g = gens[gi]
        L = B.var(f"L_{g['name']}_{k}", 0, np.inf, eps)
        B.con([(L, 1), (wv, -g.get("life_hours", 9000))], hi=0)   # (6c)
        Lg[(gi, k)] = L
    Lb = []
    for bi, wv in enumerate(Wb):
        L = B.var(f"L_bat{bi}", 0, np.inf, eps)
        B.con([(L, 1), (wv, -BP[bi]["life"])], hi=0)               # (6c)
        Lb.append(L)

    rec = {"G": {}, "Pg": {}, "F": [], "pv": [], "grid": [], "U": [[], [], []],
           "Ip": {}, "Im": {}, "S": {}, "Pp": {}, "Pm": {}, "Bp": {}, "Bm": {}}
    Lg_terms = {key: [] for key in Lg}
    Lb_terms = {bi: [] for bi in range(len(Wb))}

    for t in range(T):
        wt = w[t]
        first = (t % day_len == 0)
        # PV (4a)
        ppv = B.var(f"Ppv_{t}", 0, np.inf)
        B.con([(ppv, 1), (X, -pv1[t])], hi=0)
        rec["pv"].append(ppv)
        # grid ADD
        gr = None
        if grid.get("enabled"):
            cap = float(grid.get("max_kw", 0)) * float(grid.get("avail", [1] * T)[t])
            gr = B.var(f"Grid_{t}", 0, cap, wt * float(grid.get("price", 0.25)))
        rec["grid"].append(gr)
        # unserved load per tier ADD
        U = []
        for i in range(3):
            ub = loads[i, t] if allow_shed[i] or i > 0 else 0.0
            if i == 0 and not allow_shed[0]:
                ub = 0.0
            u = B.var(f"U{i}_{t}", 0, ub, wt * float(pen[i]))
            U.append(u); rec["U"][i].append(u)
        # fuel (3b) with a_f = 0
        F = B.var(f"F_{t}", 0, np.inf, wt * fuel)
        rec["F"].append(F)
        fuel_terms = [(F, 1)]
        bal = []      # (2a) terms
        spin = []     # (2b) terms
        prevG = {}; prevP = {}
        for gi, k, wv in Wg:
            g = gens[gi]
            G = B.var(f"G_{gi}_{k}_{t}", 0, 1, 0, True)
            P = B.var(f"Pg_{gi}_{k}_{t}", 0, g["kw"])
            pmin = g.get("min_frac", 0.3) * g["kw"]
            B.con([(P, 1), (G, -pmin)], lo=0)                       # (3a)
            B.con([(P, 1), (G, -g["kw"])], hi=0)
            B.con([(G, 1), (wv, -1)], hi=0)                         # (3c)
            if k > 0:
                B.con([(G, 1), (prevG[gi], -1)], hi=0)              # (3d)
                B.con([(P, 1), (prevP[gi], -1)], hi=0)              # (3e)
            prevG[gi] = G; prevP[gi] = P
            fuel_terms += [(P, -g["b"]), (G, -g["c"])]
            bal.append((P, g.get("eta", 1.0)))
            spin += [(G, g["kw"]), (P, -1)]
            Lg_terms[(gi, k)].append((G, wt))
            rec["G"][(gi, k, t)] = G; rec["Pg"][(gi, k, t)] = P
        B.con(fuel_terms, lo=0)                                     # F >= sum(b P + c G)

        for bi, wv in enumerate(Wb):
            p = BP[bi]
            Bp = B.var(f"Bp_{bi}_{t}", 0, 1, 0, True)
            Bm = B.var(f"Bm_{bi}_{t}", 0, 1, 0, True)
            Ip = B.var(f"Ip_{bi}_{t}", 0, p["iU_plus"])
            Im = B.var(f"Im_{bi}_{t}", 0, p["iU_minus"])
            S = B.var(f"S_{bi}_{t}", 0, 1)
            Pp = B.var(f"Pp_{bi}_{t}", 0, p["pbar"])
            Pm = B.var(f"Pm_{bi}_{t}", 0, p["pbar"])
            Yp = B.var(f"Yp_{bi}_{t}", 0, p["iU_plus"])
            Ym = B.var(f"Ym_{bi}_{t}", 0, p["iU_minus"])
            Zp = B.var(f"Zp_{bi}_{t}", 0, p["iU_plus"])
            Zm = B.var(f"Zm_{bi}_{t}", 0, p["iU_minus"])
            # previous SOC as a linear expression: S_{t-1}, or b0 * W at the start of a day block
            Sprev = [(wv, p["b0"])] if first else [(rec["S"][(bi, t - 1)], 1.0)]
            # (5c) / (8)
            B.con([(S, 1)] + [(j, -a) for j, a in Sprev] + [(Ip, -p["eta_in"] / p["cref"]), (Im, 1 / p["cref"])], lo=0, hi=0)
            B.con([(S, 1), (wv, -p["smax"])], hi=0)                  # (5d)
            B.con([(S, 1), (wv, -p["smin"])], lo=0)
            if (t + 1) % day_len == 0:                              # ADD: end of day SOC >= b0
                B.con([(S, 1), (wv, -p["b0"])], lo=0)
            # (5h)/(5i)
            B.con([(Pm, 1), (Bm, -p["pbar"])], hi=0)
            B.con([(Pp, 1), (Bp, -p["pbar"])], hi=0)
            # (5j)
            B.con([(Im, 1)] + [(j, -p["iU_minus"] * a) for j, a in Sprev], hi=0)
            # (5k)/(5l)
            B.con([(Im, 1), (Bm, -p["iU_minus"])], hi=0)
            B.con([(Ip, 1), (Bp, -p["iU_plus"])], hi=0)
            # (5m)
            B.con([(Bp, 1), (Bm, 1), (wv, -1)], hi=0)
            # exact Y (24a-24e)
            B.con([(Yp, 1), (Bp, -p["iU_plus"])], hi=0)
            B.con([(Ym, 1), (Bm, -p["iU_minus"])], hi=0)
            B.con([(Ym, 1)] + [(j, -p["iU_minus"] * a) for j, a in Sprev], hi=0)        # (24c)
            B.con([(Ip, 1), (Yp, -1), (Bp, p["iU_plus"])], hi=p["iU_plus"])
            B.con([(Ip, 1), (Yp, -1), (Bp, -p["iU_plus"])], lo=-p["iU_plus"])
            B.con([(Im, 1), (Ym, -1), (Bm, p["iU_minus"])], hi=p["iU_minus"])
            B.con([(Im, 1), (Ym, -1), (Bm, -p["iU_minus"])], lo=-p["iU_minus"])
            # McCormick Z (25a-25h), bounds S in [smin, smax], I in [0, iU]
            for Z, I, iU in ((Zp, Ip, p["iU_plus"]), (Zm, Im, p["iU_minus"])):
                sl, su = p["smin"], p["smax"]
                B.con([(Z, 1)] + [(j, -iU * a) for j, a in Sprev] + [(I, -su)], lo=-su * iU)   # Z >= iU S + su I - su iU
                B.con([(Z, 1), (I, -sl)] + [(j, 0 * a) for j, a in Sprev], lo=0)              # Z >= iL S + sl I - sl iL (iL=0)
                B.con([(Z, 1)] + [(j, -iU * a) for j, a in Sprev] + [(I, -sl)], hi=-sl * iU)  # Z <= iU S + sl I - sl iU
                B.con([(Z, 1), (I, -su)], hi=0)                                                 # Z <= iL S + su I - su iL
            # power (20)/(21): W -> kW
            B.con([(Pp, 1), (Zp, -p["av"] / 1000), (Yp, -(p["bv"] + p["iavg"] * p["rint"]) / 1000)], lo=0, hi=0)
            B.con([(Pm, 1), (Zm, -p["av"] / 1000), (Ym, -(p["bv"] - p["iavg"] * p["rint"]) / 1000)], lo=0, hi=0)
            bal += [(Pm, p["eta_out"]), (Pp, -1)]
            spin.append((S, p["eta_out"] * p["pbar"]))
            Lb_terms[bi] += [(Ip, wt * p["asoc"] / p["cref"]), (Zp, -wt * p["dsoc"] / p["cref"])]   # (23)
            for key, var in (("Ip", Ip), ("Im", Im), ("S", S), ("Pp", Pp), ("Pm", Pm), ("Bp", Bp), ("Bm", Bm)):
                rec[key][(bi, t)] = var
        # (2a) with tiers: supply >= (1 + kbar) * (demand - unserved)
        dem = loads[:, t].sum()
        terms = bal + [(ppv, 1)] + ([(gr, 1)] if gr is not None else []) + [(u, (1 + kbar)) for u in U]
        B.con(terms, lo=(1 + kbar) * dem)
        # (2b)
        B.con(spin + [(ppv, -ks)], lo=0)

    for key, terms in Lg_terms.items():
        B.con([(Lg[key], 1)] + [(j, -a) for j, a in terms], lo=0)             # (6a)
    for bi, terms in Lb_terms.items():
        B.con([(Lb[bi], 1)] + [(j, -a) for j, a in terms], lo=0)              # (23)

    fx = inp.get("fixed_design")
    if fx is not None:      # fix every design variable (used by the design enumeration)
        for gi, k, v in Wg:
            on = 1.0 if k < int(fx["gens"].get(gens[gi]["name"], 0)) else 0.0
            B.lb[v] = B.ub[v] = on
        for bi, v in enumerate(Wb):
            on = 1.0 if fx.get("battery") == bats[bi]["name"] else 0.0
            B.lb[v] = B.ub[v] = on
        if fx.get("pv_kw") is not None:
            B.lb[X] = B.ub[X] = float(fx["pv_kw"])
    res = B.solve(time_limit=float(inp.get("time_limit", 45)), gap=float(inp.get("gap", 0.02)))
    if res.x is None:
        return {"ok": False, "message": str(res.message), "seconds": round(time.time() - t0, 2),
                "n_vars": len(B.lb), "n_cons": B.nrow}
    x = res.x
    val = lambda j: float(x[j]) if j is not None else 0.0

    design = {"generators": [], "battery": None, "pv_kw": round(val(X))}
    for gi, g in enumerate(gens):
        n = sum(1 for (gj, k, v) in Wg if gj == gi and val(v) > 0.5)
        if n:
            design["generators"].append({"name": g["name"], "kw": g["kw"], "count": n,
                                         "owned": min(n, int(g.get("owned", 0)))})
    bsel = None
    for bi, v in enumerate(Wb):
        if val(v) > 0.5:
            bsel = bi; design["battery"] = {"name": bats[bi]["name"], "kwh": bats[bi]["kwh"]}

    disp = {"pv": [], "gen": [], "bat_out": [], "bat_in": [], "grid": [], "soc": [], "unserved": [[], [], []],
            "fuel_gal": [], "gen_units": [], "load": loads.sum(axis=0).round(3).tolist()}
    for t in range(T):
        disp["pv"].append(val(rec["pv"][t]))
        disp["grid"].append(val(rec["grid"][t]))
        disp["fuel_gal"].append(val(rec["F"][t]))
        gk = sum(val(rec["Pg"][(gi, k, t)]) for gi, k, _ in Wg)
        disp["gen"].append(gk)
        disp["gen_units"].append(sum(1 for gi, k, _ in Wg if val(rec["G"][(gi, k, t)]) > 0.5))
        for i in range(3):
            disp["unserved"][i].append(val(rec["U"][i][t]))
        if bsel is not None:
            p = BP[bsel]
            disp["bat_out"].append(val(rec["Pm"][(bsel, t)]) * p["eta_out"])
            disp["bat_in"].append(val(rec["Pp"][(bsel, t)]))
            disp["soc"].append(val(rec["S"][(bsel, t)]))
        else:
            disp["bat_out"].append(0.0); disp["bat_in"].append(0.0); disp["soc"].append(0.0)
    for k in disp:
        if k == "unserved":
            disp[k] = [[round(v, 3) for v in row] for row in disp[k]]
        elif isinstance(disp[k], list):
            disp[k] = [round(v, 4) for v in disp[k]]

    # ------------- costs -------------
    capex = sum(B.c[v] * val(v) for _, _, v in Wg) + sum(B.c[v] * val(v) for v in Wb) + pv["cost"] * (val(X) - pv.get("owned", 0))
    fuel_gal = float(np.dot(w, disp["fuel_gal"]))
    grid_kwh = float(np.dot(w, disp["grid"]))
    unserved_kwh = [float(np.dot(w, disp["unserved"][i])) for i in range(3)]
    costs = {"capex": round(capex), "fuel_gal": round(fuel_gal), "fuel_cost": round(fuel_gal * fuel),
             "grid_kwh": round(grid_kwh), "grid_cost": round(grid_kwh * float(grid.get("price", 0)) if grid.get("enabled") else 0),
             "unserved_kwh": [round(u, 1) for u in unserved_kwh], "objective": round(float(res.fun)),
             "load_kwh": round(float(np.dot(w, loads.sum(axis=0)))),
             "pv_kwh": round(float(np.dot(w, disp["pv"])))}
    costs["renewable_fraction"] = round(costs["pv_kwh"] / max(1.0, costs["load_kwh"]), 3)

    out = {"ok": True, "status": str(res.message), "mip_gap": float(getattr(res, "mip_gap", 0) or 0),
           "seconds": round(time.time() - t0, 2), "n_vars": len(B.lb), "n_cons": B.nrow,
           "n_int": int(sum(B.integ)), "design": design, "dispatch": disp, "costs": costs}

    # ------------- approximation error (paper Section 4.4) -------------
    if bsel is not None:
        p = BP[bsel]
        dplus, dminus, Lhat = [], [], 0.0
        for t in range(T):
            Sprev = p["b0"] if t % day_len == 0 else val(rec["S"][(bsel, t - 1)])
            Ip = val(rec["Ip"][(bsel, t)]); Im = val(rec["Im"][(bsel, t)])
            Bp = val(rec["Bp"][(bsel, t)]); Bm = val(rec["Bm"][(bsel, t)])
            Phat_p = (p["av"] * Sprev + (p["bv"] + p["iavg"] * p["rint"]) * Bp) * Ip / 1000   # (12)
            Phat_m = (p["av"] * Sprev + (p["bv"] - p["iavg"] * p["rint"]) * Bm) * Im / 1000   # (13)
            dplus.append(val(rec["Pp"][(bsel, t)]) - Phat_p)                                    # (27)
            dminus.append(val(rec["Pm"][(bsel, t)]) - Phat_m)                                   # (26)
            Lhat += w[t] * (Ip + Im) / (2 * p["cref"]) * (p["asoc"] - p["dsoc"] * Sprev)        # (6b)
        dem = loads.sum(axis=0)
        dhat = float(np.sum((np.array(dplus) - np.array(dminus)) * w) / max(1e-9, np.sum(dem * w)) * 100)  # (28)
        Lu = val(Lb[bsel])
        out["approx_error"] = {
            "max_overestimate_discharge_kw": round(max(dminus), 3),
            "max_underestimate_charge_kw": round(-min(dplus), 3),
            "net_energy_error_pct_of_demand": round(dhat, 4),
            "lifecycle_ratio_pct": round(Lhat / Lu * 100, 1) if Lu > 1e-9 else None,
            "lifecycles_model": round(Lu, 2), "lifecycles_nonlinear": round(Lhat, 2),
            "series_discharge_err": [round(v, 3) for v in dminus],
            "series_charge_err": [round(v, 3) for v in dplus],
        }
    out["nonlinear_check"] = simulate_nonlinear(inp, design, BP[bsel] if bsel is not None else None)
    return out


def pick_units(units, need):
    """H1-style unit commitment: the smallest single unit that covers `need`; otherwise keep adding
    the largest remaining units until covered."""
    if need <= 1e-9:
        return []
    asc = sorted(units, key=lambda g: g["kw"])
    for g in asc:
        if g["kw"] >= need:
            return [g]
    on, cap = [], 0.0
    for g in sorted(units, key=lambda g: -g["kw"]):
        on.append(g); cap += g["kw"]
        if cap >= need:
            break
    return on


def simulate_nonlinear(inp, design, p, series=False):
    """Re-run a design hour by hour with the paper's nonlinear battery physics (12-13, 6b) and a
    myopic rule like heuristic H1: PV first, then battery, then the smallest generator set that
    covers the remaining load plus spinning reserve. Generator output above the load (minimum
    loading) charges the battery. Unserved energy is shed from Tier 3, then 2, then 1."""
    loads = np.array(inp["tier_loads"], dtype=float); T = loads.shape[1]
    w = np.array(inp.get("weights", [1.0] * T)); day_len = int(inp.get("block_len", 24))
    pv_kw = design["pv_kw"]; pv1 = np.array(inp["pv_per_kw"])
    ks = float(inp.get("spin", 0.3)); kbar = float(inp.get("overage", 0.3))
    units = []
    for g in inp["generators"]:
        for d in design["generators"]:
            if d["name"] == g["name"]:
                units += [g] * d["count"]
    grid = inp.get("grid", {"enabled": False})
    S = p["b0"] if p else 0.0
    fuel = 0.0; curtailed = 0.0; shed = np.zeros(3); gen_hours = 0.0; Lhat = 0.0
    ser = {"pv": [], "gen": [], "bat_out": [], "bat_in": [], "grid": [], "soc": [], "shed": [[], [], []], "fuel_gal": [],
           "gen_units": []}

    def charge(P_avail):
        nonlocal S
        if not p or P_avail <= 0:
            return 0.0
        V = p["av"] * S + p["bv"] + p["iavg"] * p["rint"]
        I = max(0.0, min(P_avail * 1000 / V, p["iU_plus"], (p["smax"] - S) * p["cref"] / p["eta_in"]))
        S_old = S
        S += p["eta_in"] * I / p["cref"]
        return V * I / 1000, I, S_old

    best_b = min((g["b"] for g in units), default=0.07)
    for t in range(T):
        if p and t % day_len == 0:
            S = p["b0"]
        dem = loads[:, t].sum()
        pv_av = pv_kw * pv1[t]
        req = (1 + kbar) * dem                    # same overage rule as (2a)
        net = req - pv_av
        gr = 0.0
        if grid.get("enabled"):
            gr = min(max(net, 0), float(grid.get("max_kw", 0)) * float(grid.get("avail", [1] * T)[t]))
            net -= gr
        bat_out = bat_in = gen_out = 0.0; on = []; I_used = 0.0; S_prev = S; shed_t = [0.0, 0.0, 0.0]; fuel_hr = 0.0
        if net < 0:
            r = charge(-net)
            if r:
                bat_in, I_used, _ = r
            curtailed += (-net - bat_in) * w[t]
            pv_used = pv_av - (-net - bat_in)
        else:
            pv_used = pv_av
            if p:
                V = p["av"] * S + p["bv"] - p["iavg"] * p["rint"]
                Imax = max(0.0, min(p["iU_minus"] * S, (S - p["smin"]) * p["cref"]))
                Pmax = min(V * Imax / 1000 * p["eta_out"], p["pbar"])
                bat_out = min(net, Pmax)
                I_used = bat_out / p["eta_out"] * 1000 / V if V > 0 else 0.0
                S -= I_used / p["cref"]
            rest = net - bat_out
            need = rest + ks * pv_used if rest > 1e-9 else 0.0
            on = pick_units(units, need)
            cap = sum(g["kw"] for g in on)
            gen_out = min(rest, cap)
            pmin_total = sum(g.get("min_frac", 0.3) * g["kw"] for g in on)
            gen_run = max(gen_out, pmin_total)
            if gen_run > gen_out + 1e-9:          # minimum loading: surplus goes into the battery
                r = charge(gen_run - gen_out)
                if r:
                    bat_in, I2, _ = r; I_used += I2
            for g in on:
                share = gen_run * g["kw"] / cap
                fuel_t = (g["b"] * share + g["c"])
                fuel += fuel_t * w[t]; fuel_hr += fuel_t
            gen_hours += len(on) * w[t]
            short = max(0.0, rest - cap) / (1 + kbar)
            for i in (2, 1, 0):
                take = min(short, loads[i, t]); shed[i] += take * w[t]; short -= take; shed_t[i] = take
        if p and (t + 1) % day_len == 0 and S < p["b0"]:
            # the MILP must end each day at SOC >= b0; charge the deficit to generator fuel
            deficit_kwh = (p["b0"] - S) * p["cref"] * (p["av"] * p["b0"] + p["bv"]) / 1000 / p["eta_in"]
            fuel += deficit_kwh * best_b * w[t]
        if p:
            Lhat += w[t] * I_used / (2 * p["cref"]) * (p["asoc"] - p["dsoc"] * S_prev)
        if series:
            ser["pv"].append(round(pv_used, 3)); ser["gen"].append(round(gen_out, 3)); ser["grid"].append(round(gr, 3))
            ser["bat_out"].append(round(bat_out, 3)); ser["bat_in"].append(round(bat_in, 3)); ser["soc"].append(round(S, 4))
            ser["gen_units"].append(len(on)); ser["fuel_gal"].append(round(fuel_hr, 3))
            for i in range(3):
                ser["shed"][i].append(round(shed_t[i], 3))
    out = {"fuel_gal": round(fuel), "unserved_kwh": [round(v, 1) for v in shed], "curtailed_kwh": round(curtailed),
           "gen_hours": round(gen_hours), "battery_cycles": round(Lhat, 2)}
    if series:
        out["series"] = ser
    return out


def capex_of(inp, design):
    c = 0.0
    for d in design["generators"]:
        g = next(g for g in inp["generators"] if g["name"] == d["name"])
        c += g["cost"] * max(0, d["count"] - int(g.get("owned", 0)))
    if design.get("battery"):
        b = next(b for b in inp["batteries"] if b["name"] == design["battery"]["name"])
        c += 0 if b.get("owned") else b["cost"]
    c += inp["pv"]["cost"] * max(0, design["pv_kw"] - inp["pv"].get("owned", 0))
    return c


def evaluate(inp, design):
    """Fast cost of a design with the nonlinear simulation (used for live sliders and screening)."""
    p = None
    if design.get("battery"):
        b = next(b for b in inp["batteries"] if b["name"] == design["battery"]["name"])
        p = battery_params(b)
    sim = simulate_nonlinear(inp, design, p, series=True)
    pen = inp.get("tier_penalty", [100000.0, 50.0, 5.0])
    fuel_cost = sim["fuel_gal"] * float(inp["fuel_cost"])
    grid_kwh = float(np.dot(inp.get("weights", [1.0] * len(sim["series"]["grid"])), sim["series"]["grid"]))
    grid_cost = grid_kwh * float(inp.get("grid", {}).get("price", 0)) if inp.get("grid", {}).get("enabled") else 0.0
    pen_cost = sum(sim["unserved_kwh"][i] * float(pen[i]) for i in range(3))
    capex = capex_of(inp, design)
    sim["capex"] = round(capex); sim["fuel_cost"] = round(fuel_cost); sim["grid_kwh"] = round(grid_kwh)
    sim["grid_cost"] = round(grid_cost); sim["penalty_cost"] = round(pen_cost)
    sim["total"] = round(capex + fuel_cost + grid_cost + pen_cost + float(inp.get("cycle_cost", 1.0)) * (sim["gen_hours"] + sim["battery_cycles"]))
    return sim


def candidate_designs(inp, max_units=5):
    loads = np.array(inp["tier_loads"]); peak = float(loads.sum(axis=0).max()); peak_t1 = float(loads[0].max())
    kbar = float(inp.get("overage", 0.3))
    gens = inp["generators"]
    combos = []

    def rec(i, counts):
        if i == len(gens):
            n = sum(counts); cap = sum(c * g["kw"] for c, g in zip(counts, gens))
            if n <= max_units and 0.45 * (1 + kbar) * peak <= cap <= 1.7 * (1 + kbar) * peak + max(g["kw"] for g in gens):
                if inp.get("n_plus_1") and n:
                    biggest = max(g["kw"] for c, g in zip(counts, gens) if c)
                    if cap - biggest < (1 + kbar) * peak_t1:   # Tier 1 must survive losing the largest unit
                        return
                combos.append(list(counts))
            return
        lo = int(gens[i].get("owned", 0))
        for c in range(lo, int(gens[i]["max_count"]) + 1):
            rec(i + 1, counts + [c])
    rec(0, [])
    # Algorithm 2 (paper Section 3.1) adapted: consider the three battery sizes nearest 80% of peak
    near = sorted(inp["batteries"], key=lambda b: abs(b["kwh"] - 0.8 * peak))[:3]
    bats = [None] + [b["name"] for b in near]
    if any(b.get("owned") for b in inp["batteries"]):
        bats = [b["name"] for b in inp["batteries"] if b.get("owned")]
    maxp = int(inp["pv"]["max_panels"]); lo_pv = int(inp["pv"].get("owned", 0))
    pv_levels = sorted(set([lo_pv] + [max(lo_pv, int(round(maxp * f))) for f in (0.25, 0.5, 0.75, 1.0)]))
    designs = []
    for c in combos:
        for b in bats:
            for pvk in pv_levels:
                designs.append({"generators": [{"name": g["name"], "kw": g["kw"], "count": n} for g, n in zip(gens, c) if n],
                                "battery": ({"name": b, "kwh": next(x["kwh"] for x in inp["batteries"] if x["name"] == b)} if b else None),
                                "pv_kw": pvk})
    return designs


def optimize(inp, top_k=4):
    """Paper-style solution approach: heuristic H screens candidate designs with the nonlinear
    simulation, then model (U) is solved with each of the best generator/battery choices fixed
    and the PV size free. Returns the best (U) solution plus the screening leaderboard."""
    t0 = time.time()
    designs = candidate_designs(inp)
    scored = []
    for d in designs:
        e = evaluate(inp, d); e.pop("series", None)
        scored.append((e["total"], d, e))
    scored.sort(key=lambda r: r[0])
    seen, picks = set(), []
    keyf = lambda d: (tuple((g["name"], g["count"]) for g in d["generators"]), d["battery"]["name"] if d["battery"] else None)
    for tot, d, e in scored:
        if keyf(d) in seen:
            continue
        seen.add(keyf(d)); picks.append((tot, d, e))
        if len(picks) >= top_k - 1:
            break
    # always refine the best design that has a battery too: the myopic heuristic undervalues storage,
    # and the MILP (with look-ahead dispatch) is the fair test of it
    for tot, d, e in scored:
        if d["battery"] and keyf(d) not in seen:
            picks.append((tot, d, e)); seen.add(keyf(d)); break
    for tot, d, e in scored:
        if len(picks) >= top_k:
            break
        if keyf(d) not in seen:
            picks.append((tot, d, e)); seen.add(keyf(d))
    best = None; runs = []
    for tot, d, e in picks:
        sub = dict(inp)
        sub["fixed_design"] = {"gens": {g["name"]: g["count"] for g in d["generators"]},
                               "battery": d["battery"]["name"] if d["battery"] else None, "pv_kw": None}
        sub["time_limit"] = float(inp.get("time_limit_each", 20))
        r = solve(sub)
        runs.append({"design": d, "heuristic_total": tot, "milp_objective": r.get("costs", {}).get("objective"),
                     "ok": r.get("ok"), "seconds": r.get("seconds"), "gap": r.get("mip_gap")})
        if r.get("ok") and (best is None or r["costs"]["objective"] < best["costs"]["objective"]):
            best = r; best["heuristic_best"] = {"design": d, "eval": e}
    if best is None:
        return {"ok": False, "message": "No feasible design found. Raise generator counts or allow shedding.",
                "screened": len(designs)}
    best["screened"] = len(designs)
    best["leaderboard"] = [{"design": d, "total": tot, "fuel_gal": e["fuel_gal"], "capex": e["capex"],
                            "unserved_kwh": e["unserved_kwh"]} for tot, d, e in scored[:12]]
    best["refined"] = runs
    best["seconds_total"] = round(time.time() - t0, 2)
    return best


if __name__ == "__main__":
    import json, sys
    print(json.dumps(optimize(json.load(open(sys.argv[1]))), indent=1)[:3000])
