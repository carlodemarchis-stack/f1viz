#!/usr/bin/env python3
"""Weekend archive: practice, qualifying and the starting grid for rounds already run.

  python3 tools/build_weekends.py            # every completed round that is missing
  python3 tools/build_weekends.py --round 6  # just one
  python3 tools/build_weekends.py --force    # refetch rounds already on disk

Writes data/weekend/r<N>.json (same shape as data/live.json) plus a manifest at
data/weekends.json listing the rounds available. The app fetches these on demand -
inlining 13+ weekends would roughly triple index.html for data most visits never open.

formula1.com is the source: it is the only one carrying practice results and the
starting grid, and unlike OpenF1 it is never locked. Session times come from jolpica.
Shares its keying/parsing with update_live.py so the live weekend and the archive
produce identical structures.
"""
import json, os, sys, time, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "weekend")
MANIFEST = os.path.join(ROOT, "data", "weekends.json")
YEAR = 2026

_spec = importlib.util.spec_from_file_location("ul", os.path.join(ROOT, "tools", "update_live.py"))
ul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ul)


def build(rnd, f1):
    cal = next((c for c in f1["calendar"] if int(c["r"]) == rnd), None)
    if not cal:
        return None
    # one country can host several races (Miami / United States / Las Vegas) and f1.com's slug is
    # not always the country ("UK" -> great-britain), so try the round's own names in turn
    cands, seen = [], set()
    for v in (cal.get("short"), cal.get("country"), cal.get("locality"), cal.get("gp")):
        sl = ul.f1com_slug(v or "")
        if sl and sl not in seen:
            seen.add(sl); cands.append(sl)
    slug = mid = None
    for sl in cands:
        got = ul.f1com_meeting_id(sl)
        if got:
            slug, mid = sl, got; break
    if not mid:
        print("  R%-2d no f1.com meeting id (tried %s)" % (rnd, ", ".join(cands))); return None

    bynum = {int(d["num"]): d for d in f1["drivers"] if d.get("num")}
    teamcol = {c["name"]: c["color"] for c in f1["constructors"]}
    teamid = {c["name"]: c["teamId"] for c in f1["constructors"]}
    tid2name = {c["teamId"]: c["name"] for c in f1["constructors"]}
    tid2col = {c["teamId"]: c["color"] for c in f1["constructors"]}

    # session times + circuit from jolpica
    jd = ul.curl_json("https://api.jolpi.ca/ergast/f1/%d/%d.json" % (YEAR, rnd))
    jr = (((jd or {}).get("MRData") or {}).get("RaceTable") or {}).get("Races") or [{}]
    jr = jr[0] if jr else {}
    schedule = []
    for jkey, skey, label in ul.SESSION_MAP:
        blk = jr.get(jkey) if jkey != "Race" else {"date": jr.get("date"), "time": jr.get("time")}
        if blk and blk.get("date"):
            schedule.append({"key": skey, "name": label,
                             "start": "%sT%s" % (blk["date"], blk.get("time") or "00:00:00Z")})
    if not schedule:      # jolpica missing the round -> at least give the card the race day
        schedule = [{"key": "race", "name": "Race", "start": "%sT00:00:00Z" % cal.get("date", "")}]

    circ = (f1.get("circuits") or {}).get(str(rnd)) or {}
    if not circ.get("laps"):
        info = ul.CIRCUIT_INFO.get(jr.get("Circuit", {}).get("circuitId"))
        if info:
            circ = {"laps": info["laps"], "length": "%.3f" % info["length"],
                    "distance": "%.3f" % info.get("distance", info["laps"] * info["length"])}

    wk = {"meta": {"season": YEAR, "round": rnd, "source": "f1.com"},
          "round": rnd, "gp": jr.get("raceName") or cal.get("gp"), "full": cal.get("full"),
          "short": cal.get("short"), "flag": cal.get("flag", ""), "country": cal.get("country"),
          "circuit": cal.get("circuit"), "locality": cal.get("locality"), "date": cal.get("date"),
          "laps": circ.get("laps"), "length": circ.get("length"), "distance": circ.get("distance"),
          "sessions": []}

    got = []
    for sc in schedule:
        row = {"key": sc["key"], "name": sc["name"], "start": sc["start"], "end": None, "results": []}
        if sc["key"] in ul.F1COM_PATH and sc["key"] != "race":
            res = ul.f1com_results(mid, slug, sc["key"], bynum, teamcol, teamid, tid2name, tid2col)
            if res:
                row["results"] = res; got.append("%s:%d" % (sc["key"], len(res)))
            time.sleep(0.25)
        wk["sessions"].append(row)

    grid = ul.f1com_results(mid, slug, "grid", bynum, teamcol, teamid, tid2name, tid2col)
    if grid:
        qpos = {r["code"]: r["pos"] for s in wk["sessions"] if s["key"] in ("quali", "sq") for r in s["results"]}
        for r in grid:
            r["qpos"] = qpos.get(r["code"])
            r["delta"] = (r["qpos"] - r["pos"]) if r["qpos"] else None
        wk["grid"] = grid
        got.append("grid:%d" % len(grid))
    print("  R%-2d %-16s %s" % (rnd, cal.get("short", ""), ", ".join(got) or "nothing found"))
    return wk if got else None


def main():
    args = sys.argv[1:]
    f1 = json.load(open(os.path.join(ROOT, "data", "f1.json")))
    done = int(f1["meta"]["round"])
    force = "--force" in args
    if "--round" in args:
        rounds = [int(args[args.index("--round") + 1])]
    else:
        rounds = list(range(1, done + 1))
    os.makedirs(OUT, exist_ok=True)
    for rnd in rounds:
        dst = os.path.join(OUT, "r%d.json" % rnd)
        if os.path.exists(dst) and not force:
            print("  R%-2d already built (--force to refetch)" % rnd); continue
        wk = build(rnd, f1)
        if wk:
            json.dump(wk, open(dst, "w"), ensure_ascii=False, indent=1)
    have = sorted(int(f[1:-5]) for f in os.listdir(OUT) if f.startswith("r") and f.endswith(".json"))
    json.dump(have, open(MANIFEST, "w"))
    print("weekends available: %s" % have)


if __name__ == "__main__":
    main()
