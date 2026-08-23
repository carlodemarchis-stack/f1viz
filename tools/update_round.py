#!/usr/bin/env python3
"""Safe, repeatable Grand Prix update for F1viz.

Rebuilds a round from jolpica-f1 (results/quali/sprint/laps/pitstops) + OpenF1 (safety),
in the EXACT f1.json schema, then recomputes every driver & constructor standing/KPI.

  python3 tools/update_round.py --validate 11    # dry-run: reproduce round 11, diff vs committed (must PASS)
  python3 tools/update_round.py --apply 12        # add/refresh round 12 -> writes data/f1.json + data/laps/r12.json

ALWAYS run --validate on an existing round first (proves the pipeline still matches),
and cross-check the new round's winner/podium/points against formula1.com before publishing.
"""
import json, os, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P  = os.path.join(ROOT, "data", "f1.json")
YEAR = 2026
# circuit length (km) for tracks not yet in f1.json['circuits']; official race distance = length*laps
# OpenF1 race_control is unreliable for exact safety counts; verified overrides win when present.
# OpenF1 race_control is flaky; provide verified {safety counts, per-lap flags} per round.
SAFETY_OVERRIDE = {12: {"safety": {"sc": 1, "vsc": 0, "red": 1}, "flags": {"1": "S", "2": "S", "3": "S"}}}  # Dutch: Verstappen L1 crash -> red flag + SC
CIRCUIT_LEN = {"zandvoort": 4.259, "monza": 5.793, "madring": 5.474, "baku": 6.003,
               "sepang": 5.543, "marina_bay": 4.940, "americas": 5.513, "rodriguez": 4.304,
               "interlagos": 4.309, "vegas": 6.201, "losail": 5.419, "yas_marina": 5.281}

def curl_json(url):
    out = subprocess.run(["curl", "-s", url], capture_output=True, text=True).stdout
    try: return json.loads(out)
    except Exception: return None

def jolp(rnd, ep):
    d = curl_json(f"https://api.jolpi.ca/ergast/f1/{YEAR}/{rnd}/{ep}.json?limit=2000")
    if not d: return None
    races = d["MRData"]["RaceTable"]["Races"]
    return races[0] if races else None

def fetch_all_laps(rnd):
    """jolpica /laps caps at 100 timing-rows per page -> paginate and merge into full Laps."""
    merged, off, total = {}, 0, None
    while True:
        d = curl_json(f"https://api.jolpi.ca/ergast/f1/{YEAR}/{rnd}/laps.json?limit=100&offset={off}")
        if not d: break
        md = d["MRData"]; total = int(md["total"]); races = md["RaceTable"]["Races"]
        if not races: break
        for L in races[0].get("Laps", []):
            merged.setdefault(L["number"], []).extend(L["Timings"])
        off += 100
        if off >= total: break
    return [{"number": n, "Timings": merged[n]} for n in sorted(merged, key=int)]

def openf1_safety(rnd, cal):
    """Return ({sc,vsc,red}, {lap:flag}) from OpenF1 race control; best-effort."""
    mtgs = curl_json(f"https://api.openf1.org/v1/meetings?year={YEAR}") or []
    m = [x for x in mtgs if cal["country"] in (x.get("country_name") or "") or cal["gp"] in (x.get("meeting_name") or "")]
    if not m: return {"sc": 0, "vsc": 0, "red": 0}, {}
    ss = curl_json(f"https://api.openf1.org/v1/sessions?meeting_key={m[0]['meeting_key']}") or []
    race = [s for s in ss if s.get("session_name") == "Race"]
    if not race: return {"sc": 0, "vsc": 0, "red": 0}, {}
    rc = curl_json(f"https://api.openf1.org/v1/race_control?session_key={race[0]['session_key']}") or []
    sc = vsc = red = 0; flags = {}
    for msg in rc:
        u = (msg.get("message") or "").upper(); lap = msg.get("lap_number")
        if "RED FLAG" in u: red += 1; flags[str(lap)] = "R"
        elif "VIRTUAL SAFETY CAR" in u and "DEPLOYED" in u: vsc += 1
        elif ("SAFETY CAR" in u and ("DEPLOYED" in u or "LIGHTS ON" in u)):
            sc += 1
            if lap: flags[str(lap)] = "S"
    return {"sc": sc, "vsc": vsc, "red": red}, flags

# ---------- build one round in f1.json schema ----------
NAT_FLAG = {"Japanese": "🇯🇵", "Thai": "🇹🇭", "Danish": "🇩🇰", "Chinese": "🇨🇳",
            "American": "🇺🇸", "Argentinian": "🇦🇷", "German": "🇩🇪", "French": "🇫🇷"}

def build_round(f1, rnd):
    dstatic = {dr["code"]: dr for dr in f1["drivers"]}
    teamById = {c["teamId"]: c for c in f1["constructors"]}
    new_drivers = []
    cal = next(c for c in f1["calendar"] if c["r"] == rnd)
    geo = next(g for g in f1["geo"] if g["r"] == rnd)

    def ensure_driver(res):
        """Create a static driver object for a code not yet in the grid (mid-season debut)."""
        d = res["Driver"]; code = d["code"]
        if code in dstatic: return
        tid = res["Constructor"]["constructorId"]; tm = teamById.get(tid, {})
        yob = int(d.get("dateOfBirth", "2000-01-01")[:4])
        nd = {"pos": 0, "code": code, "num": d.get("permanentNumber", ""),
              "given": d["givenName"], "family": d["familyName"], "nat": d["nationality"],
              "flag": NAT_FLAG.get(d["nationality"], "🏁"), "team": tm.get("name", ""),
              "teamId": tid, "color": tm.get("color", "#888"), "color2": tm.get("color2", "#111"),
              "slug": d["driverId"], "points": 0, "wins": 0, "podiums": 0, "poles": 0, "fl": 0,
              "best": None, "dnf": 0, "pfin": 0, "starts": 0, "gap": 0, "prog": [], "teammate": {},
              "races": [], "age": YEAR - yob, "sprintRaces": 0, "sprintWins": 0, "sprintPoles": 0}
        dstatic[code] = nd; new_drivers.append(nd)

    resR  = jolp(rnd, "results")
    quali = jolp(rnd, "qualifying")
    Q = {q["Driver"]["code"]: int(q["position"]) for q in quali["QualifyingResults"]}
    sprintR = jolp(rnd, "sprint")
    is_sprint = bool(sprintR and "SprintResults" in sprintR)
    SP = {}
    if is_sprint:
        for s in sprintR["SprintResults"]:
            SP[s["Driver"]["code"]] = int(s["points"])
    ps = jolp(rnd, "pitstops")
    pits = {}
    for p in (ps.get("PitStops", []) if ps else []):
        pits[p["driverId"]] = pits.get(p["driverId"], 0) + 1
    did2code = {r["Driver"]["driverId"]: r["Driver"]["code"] for r in resR["Results"]}

    # --- per-driver race entry ---
    dentries = {}
    for r in resR["Results"]:
        ensure_driver(r)
        code = r["Driver"]["code"]; did = r["Driver"]["driverId"]; pt = r["positionText"]; clsd = pt.isdigit()
        fl = r.get("FastestLap", {}) or {}; T = r.get("Time", {}) or {}
        rpts = int(r["points"]); spr = SP.get(code) if is_sprint else None
        tid = r["Constructor"]["constructorId"]
        dentries[code] = dict(
            r=rnd, gp=cal["gp"], flag=cal["flag"], country=cal["country"], circuit=cal["circuit"],
            locality=cal["locality"], date=cal["date"], grid=int(r["grid"]),
            fin=(int(pt) if clsd else None), ptxt=pt, pts=rpts + (spr or 0), status=r["status"],
            laps=int(r["laps"]), fl=(fl.get("rank") == "1"),
            flrank=int(fl["rank"]) if fl.get("rank") else None, flTime=(fl.get("Time", {}) or {}).get("time"),
            quali=Q.get(code), time=(T.get("time", "") if clsd else None), rpts=rpts, spr=spr,
            pits=pits.get(did, 0), tid=tid)

    # --- races[] entry ---
    def cls_row(r):
        code = r["Driver"]["code"]; d = dstatic[code]; T = r.get("Time", {}) or {}
        tid = r["Constructor"]["constructorId"]; tm = teamById.get(tid, {})
        return dict(pos=r["positionText"], code=code, family=d["family"], teamId=tid,
                    color=tm.get("color", d["color"]), result=(T.get("time") if T.get("time") else r["status"]),
                    pts=int(r["points"]), grid=int(r["grid"]))
    winner = resR["Results"][0]; pole = quali["QualifyingResults"][0]
    fld = next((x for x in resR["Results"] if (x.get("FastestLap", {}) or {}).get("rank") == "1"), None)
    wc = winner["Driver"]["code"]; pc = pole["Driver"]["code"]
    safety, lapflags = openf1_safety(rnd, cal)
    _ov = SAFETY_OVERRIDE.get(rnd)               # verified manual override (OpenF1 is flaky)
    if _ov:
        safety = _ov.get("safety", safety); lapflags = _ov.get("flags", lapflags)
    race_entry = dict(
        r=rnd, short=cal["short"], full=cal["full"], flag=cal["flag"], country=cal["country"],
        locality=cal["locality"], circuit=cal["circuit"], date=cal["date"], lat=geo["lat"], long=geo["long"],
        laps=int(winner["laps"]),
        winner=dict(code=wc, family=dstatic[wc]["family"], color=dstatic[wc]["color"], teamId=dstatic[wc]["teamId"]),
        pole=dict(code=pc, family=dstatic[pc]["family"]),
        fl=(dict(code=fld["Driver"]["code"], family=dstatic[fld["Driver"]["code"]]["family"],
                 time=fld["FastestLap"]["Time"]["time"]) if fld else None),
        cls=[cls_row(r) for r in resR["Results"]], safety=safety)

    # --- laps file ---
    order, times = [], []
    for L in fetch_all_laps(rnd):
        tim = {}
        for t in L["Timings"]:
            c = did2code.get(t["driverId"])
            if c: tim[c] = (int(t["position"]), t["time"])
        codes = [c for c, _ in sorted(tim.items(), key=lambda kv: kv[1][0])]
        order.append(codes)
        times.append({c: v[1] for c, v in tim.items()})
    grid = [did2code[r["Driver"]["driverId"]] for r in sorted(resR["Results"], key=lambda r: int(r["grid"]) or 99)
            if int(r["grid"]) > 0]
    pit, pitTime = {}, {}
    for p in (ps.get("PitStops", []) if ps else []):
        c = did2code.get(p["driverId"]);
        if not c: continue
        pit.setdefault(c, []).append(int(p["lap"])); pitTime.setdefault(c, {})[str(p["lap"])] = p.get("duration", "")
    laps_obj = dict(race=cal["full"], round=rnd, nLaps=int(winner["laps"]), grid=grid,
                    order=order, times=times, pit=pit, pitTime=pitTime, flags=lapflags)

    circ = dict(length=f"{CIRCUIT_LEN.get(_circ_slug(cal), 0):.3f}".rstrip("0").rstrip(".") if _circ_slug(cal) in CIRCUIT_LEN else None,
                laps=str(int(winner["laps"])),
                distance=(f"{CIRCUIT_LEN[_circ_slug(cal)]*int(winner['laps']):.2f}" if _circ_slug(cal) in CIRCUIT_LEN else None))
    return dentries, race_entry, laps_obj, is_sprint, circ, new_drivers

def _circ_slug(cal):
    return cal["circuit"].lower().split()[-1] if cal.get("circuit") else ""

# ---------- recompute all standings / KPIs ----------
def countback(rs):
    """F1 tiebreak vector: -wins, -P2s, -P3s, ... (more of a better finish wins)."""
    from collections import Counter
    c = Counter(r["fin"] for r in rs if r.get("fin"))
    return tuple(-c.get(p, 0) for p in range(1, 23))

def recompute(f1):
    for d in f1["drivers"]:
        for r in d["races"]: r.setdefault("tid", d["teamId"])   # per-race team (mid-season moves keep their own tid)
    byTeam = {}
    for dr in f1["drivers"]:
        rs = sorted(dr["races"], key=lambda r: r["r"])
        prog, acc = [], 0
        for r in rs: acc += r["pts"]; prog.append(acc)
        dr["points"] = sum(r["pts"] for r in rs)
        dr["wins"] = sum(1 for r in rs if r.get("fin") == 1)
        dr["podiums"] = sum(1 for r in rs if r.get("fin") in (1, 2, 3))
        dr["poles"] = sum(1 for r in rs if r.get("quali") == 1)
        dr["fl"] = sum(1 for r in rs if r.get("fl"))
        dr["dnf"] = sum(1 for r in rs if r.get("fin") is None)
        dr["starts"] = len(rs)
        dr["best"] = min([r["fin"] for r in rs if r.get("fin")], default=None)
        dr["pfin"] = sum(1 for r in rs if r.get("fin") and r["fin"] <= 10)
        dr["prog"] = prog
        dr["sprintRaces"] = sum(1 for r in rs if r.get("spr") is not None)
        dr["sprintWins"] = sum(1 for r in rs if r.get("spr") == 8)
        byTeam.setdefault(dr["teamId"], []).append(dr)
    # driver standings order (points, then countback)
    order = sorted(f1["drivers"], key=lambda d: (-d["points"], countback(d["races"])))
    lead = order[0]["points"] if order else 0
    for i, d in enumerate(order):
        d["pos"] = i + 1; d["gap"] = lead - d["points"]
    f1["drivers"] = order
    # teammate
    for d in f1["drivers"]:
        mate = next((x for x in byTeam[d["teamId"]] if x["code"] != d["code"]), None)
        if mate: d["teammate"] = {"code": mate["code"], "family": mate["family"], "points": mate["points"]}
    # constructors -- group each driver-race by its per-race team (tid), so mid-season moves attribute correctly
    rounds = sorted({r["r"] for d in f1["drivers"] for r in d["races"]})
    allraces = [r for d in f1["drivers"] for r in d["races"]]
    def team_races(tid): return [r for r in allraces if r.get("tid") == tid]
    for c in f1["constructors"]:
        rs = team_races(c["teamId"])
        c["points"] = sum(r["pts"] for r in rs)
        c["wins"] = sum(1 for r in rs if r.get("fin") == 1)
        c["podiums"] = sum(1 for r in rs if r.get("fin") in (1, 2, 3))
        c["poles"] = sum(1 for r in rs if r.get("quali") == 1)
        c["fl"] = sum(1 for r in rs if r.get("fl"))
        c["dnf"] = sum(1 for r in rs if r.get("fin") is None)
        c["best"] = "P" + str(min([r["fin"] for r in rs if r.get("fin")], default=99))
        c["prog"] = [sum(r["pts"] for r in rs if r["r"] <= i) for i in rounds]
    corder = sorted(f1["constructors"], key=lambda c: (-c["points"],
                    tuple(-sum(1 for r in team_races(c["teamId"]) if r.get("fin") == p) for p in range(1, 23))))
    for i, c in enumerate(corder): c["pos"] = i + 1
    f1["constructors"] = corder

# ---------- validate ----------
def validate(rnd):
    f1 = json.load(open(F1P))
    committed_d = {dr["code"]: next((r for r in dr["races"] if r["r"] == rnd), None) for dr in f1["drivers"]}
    committed_race = next((r for r in f1["races"] if r["r"] == rnd), None)
    dentries, race_entry, laps_obj, is_sprint, circ, new_drivers = build_round(f1, rnd)
    if new_drivers: print("  new drivers this round:", [n["code"] for n in new_drivers])
    IGN_D = {"pits", "tid"}  # pits: jolpica vs OpenF1 source diff; tid: new field added by this pipeline
    dmis = 0
    for code, built in dentries.items():
        c = committed_d.get(code)
        if not c: print("  extra driver", code); dmis += 1; continue
        diffs = {k: (built[k], c.get(k)) for k in built if k not in IGN_D and built[k] != c.get(k)}
        if diffs: print(f"  DRIVER {code}: {diffs}"); dmis += 1
    rmis = 0
    for k in ("winner", "pole", "fl", "laps"):   # 'safety' is best-effort (OpenF1 flaky) -> not gated
        if race_entry[k] != committed_race.get(k): print(f"  RACE {k}: built={race_entry[k]} committed={committed_race.get(k)}"); rmis += 1
    # cls compare (ignore pts source for sprint? compare pos/code/result)
    for a, b in zip(race_entry["cls"], committed_race["cls"]):
        if {k: a[k] for k in ("pos", "code", "result", "grid")} != {k: b[k] for k in ("pos", "code", "result", "grid")}:
            print(f"  CLS {a['code']}: built={a} committed={b}"); rmis += 1; break
    print(f"round {rnd} validation -> drivers: {'PASS' if dmis==0 else str(dmis)+' DIFF'} | race: {'PASS' if rmis==0 else str(rmis)+' DIFF'}")

# ---------- apply ----------
def apply(rnd):
    f1 = json.load(open(F1P))
    cal = next(c for c in f1["calendar"] if c["r"] == rnd)
    dentries, race_entry, laps_obj, is_sprint, circ, new_drivers = build_round(f1, rnd)
    existing = {d["code"] for d in f1["drivers"]}
    for nd in new_drivers:
        if nd["code"] not in existing:
            f1["drivers"].append(nd); print("  added new driver:", nd["code"], nd["family"], "->", nd["team"])
    # drivers: replace/append this round's entry
    for dr in f1["drivers"]:
        dr["races"] = [r for r in dr["races"] if r["r"] != rnd]
        if dr["code"] in dentries: dr["races"].append(dentries[dr["code"]])
        dr["races"].sort(key=lambda r: r["r"])
    # races[]
    f1["races"] = [r for r in f1["races"] if r["r"] != rnd] + [race_entry]
    f1["races"].sort(key=lambda r: r["r"])
    # rounds[]
    f1["rounds"] = [r for r in f1["rounds"] if r["r"] != rnd] + [
        {"r": rnd, "country": cal["country"], "flag": cal["flag"], "gp": cal["gp"],
         "circuit": cal["circuit"], "locality": cal["locality"], "date": cal["date"]}]
    f1["rounds"].sort(key=lambda r: r["r"])
    # raceLaps / circuits / sprintRounds / meta
    f1["raceLaps"][str(rnd)] = race_entry["laps"]
    if circ["length"]: f1["circuits"][str(rnd)] = circ
    if is_sprint and rnd not in f1["meta"]["sprintRounds"]:
        f1["meta"]["sprintRounds"] = sorted(f1["meta"]["sprintRounds"] + [rnd])
    f1["meta"]["round"] = max(r["r"] for r in f1["races"])
    lastcal = next(c for c in f1["calendar"] if c["r"] == f1["meta"]["round"])
    f1["meta"]["roundName"] = lastcal["gp"]; f1["meta"]["roundCountry"] = lastcal["country"]
    recompute(f1)
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(laps_obj, open(os.path.join(ROOT, "data", "laps", f"r{rnd}.json"), "w"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"APPLIED round {rnd}: {race_entry['winner']['family']} won | drivers now lead by {f1['drivers'][0]['code']} {f1['drivers'][0]['points']} | sprint={is_sprint}")
    print(f"  wrote data/f1.json + data/laps/r{rnd}.json ; remember to rebuild index.html and verify on formula1.com")

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("--validate", "--apply"):
        print(__doc__); sys.exit(1)
    (validate if sys.argv[1] == "--validate" else apply)(int(sys.argv[2]))
