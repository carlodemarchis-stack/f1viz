#!/usr/bin/env python3
"""Apply a race from formula1.com when jolpica has not published it yet.

  python3 tools/apply_from_f1com.py --round 13          # dry run: print what it would write
  python3 tools/apply_from_f1com.py --round 13 --apply

f1.com carries the classification, fastest laps and the starting grid, but NOT
lap-by-lap timing. The round is therefore written with `nolaps: true`, and the card
disables Lap evolution and The Wall for it. Re-running the normal
`update_round.py --apply <r>` once jolpica catches up overwrites this with the full
round, laps file included, and clears the flag - so this is a stopgap, not a fork.

Standings are recomputed with update_round.recompute(), the same function the jolpica
path uses, so the championship maths is identical either way.
"""
import json, os, re, sys, urllib.request, importlib.util
import html as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
YEAR = 2026

_s = importlib.util.spec_from_file_location("ur", os.path.join(ROOT, "tools", "update_round.py"))
ur = importlib.util.module_from_spec(_s); _s.loader.exec_module(ur)
_s2 = importlib.util.spec_from_file_location("ul", os.path.join(ROOT, "tools", "update_live.py"))
ul = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(ul)


def table(mid, slug, path):
    url = "https://www.formula1.com/en/results/%d/races/%s/%s/%s" % (YEAR, mid, slug, path)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        s = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r"<table[^>]*>(.*?)</table>", s, re.S)
    if not m:
        return None
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        c = [re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", x))).strip()
             for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if c:
            out.append(c)
    return out


def code_of(cell):
    m = re.search(r"([A-Z]{3})\s*$", cell)
    return m.group(1) if m else cell[:3].upper()


def build(f1, rnd):
    cal = next(c for c in f1["calendar"] if c["r"] == rnd)
    geo = next(g for g in f1["geo"] if g["r"] == rnd)
    dstatic = {d["code"]: d for d in f1["drivers"]}
    teamById = {c["teamId"]: c for c in f1["constructors"]}
    teamid = {c["name"]: c["teamId"] for c in f1["constructors"]}

    cands = []
    for v in (cal.get("short"), cal.get("country"), cal.get("locality")):
        sl = ul.f1com_slug(v or "")
        if sl and sl not in cands:
            cands.append(sl)
    slug = mid = None
    for sl in cands:
        got = ul.f1com_meeting_id(sl)
        if got:
            slug, mid = sl, got; break
    if not mid:
        raise SystemExit("no f1.com meeting id for round %d" % rnd)

    rr = table(mid, slug, "race-result")
    if not rr or len(rr) < 2:
        raise SystemExit("f1.com has no race result for round %d yet" % rnd)
    flt = table(mid, slug, "fastest-laps") or []
    grid_t = table(mid, slug, "starting-grid") or []

    grid = {}
    for c in grid_t[1:]:
        if len(c) > 3 and c[0].isdigit():
            grid[code_of(c[2])] = int(c[0])
    fl_rank, fl_time = {}, {}
    for i, c in enumerate(flt[1:]):
        if len(c) > 6:
            cd = code_of(c[2]); fl_rank[cd] = int(c[0]) if c[0].isdigit() else i + 1; fl_time[cd] = c[6]
    # qualifying order comes from the weekend payload we already captured
    quali = {}
    for src in (os.path.join(ROOT, "data", "live.json"), os.path.join(ROOT, "data", "weekend", "r%d.json" % rnd)):
        if os.path.exists(src):
            try:
                w = json.load(open(src))
                if w.get("round") == rnd:
                    for s in w.get("sessions", []):
                        if s["key"] in ("quali", "sq"):
                            for r in s["results"]:
                                quali[r["code"]] = r["pos"]
            except Exception:
                pass

    dentries, cls = {}, []
    for c in rr[1:]:
        if len(c) < 7:
            continue
        pos, num, drv, team, laps, tr, pts = c[0], c[1], c[2], c[3], c[4], c[5], c[6]
        code = code_of(drv)
        clsd = pos.isdigit()
        trs = tr.strip()
        timeish = bool(re.match(r"^\+?\d", trs))     # "1:51:15.281" / "+3.857s" / "+1 lap"
        lapped = "lap" in trs.lower()
        # a driver can be classified yet retired - f1.com prints DNF in the time column
        if not clsd:
            status, timev = "Retired", None
        elif not timeish:
            status, timev = "Retired", ""
        elif lapped:
            status, timev = "Lapped", trs.rstrip("s")
        else:
            status, timev = "Finished", trs.rstrip("s")   # stored without the trailing 's'
        tid = ul.match_team(team, teamid) or (dstatic.get(code, {}) or {}).get("teamId")
        rpts = int(pts) if pts.strip().isdigit() else 0
        dentries[code] = dict(
            r=rnd, gp=cal["gp"], flag=cal["flag"], country=cal["country"], circuit=cal["circuit"],
            locality=cal["locality"], date=cal["date"], grid=grid.get(code, 0),
            fin=(int(pos) if clsd else None), ptxt=(pos if clsd else "R"),
            pts=rpts, status=status, laps=int(laps) if laps.isdigit() else 0,
            fl=(fl_rank.get(code) == 1), flrank=fl_rank.get(code), flTime=fl_time.get(code),
            quali=quali.get(code), time=timev, rpts=rpts, spr=None,
            pits=None,                      # f1.com's pit table is incomplete; unknown beats a wrong count
            tid=tid)
        d = dstatic.get(code, {})
        tm = teamById.get(tid, {})
        cls.append(dict(pos=pos, code=code, family=d.get("family", code), teamId=tid,
                        color=tm.get("color", d.get("color", "#888")),
                        result=(trs.rstrip("s") if timeish else status), pts=rpts, grid=grid.get(code, 0)))

    win = rr[1]; wc = code_of(win[2])
    pc = next((c for c, p in quali.items() if p == 1), None) or wc
    flc = next((c for c, r in fl_rank.items() if r == 1), None)
    race_entry = dict(
        r=rnd, short=cal["short"], full=cal["full"], flag=cal["flag"], country=cal["country"],
        locality=cal["locality"], circuit=cal["circuit"], date=cal["date"], lat=geo["lat"], long=geo["long"],
        laps=int(win[4]),
        winner=dict(code=wc, family=dstatic[wc]["family"], color=dstatic[wc]["color"], teamId=dstatic[wc]["teamId"]),
        pole=dict(code=pc, family=dstatic.get(pc, {}).get("family", pc)),
        fl=(dict(code=flc, family=dstatic.get(flc, {}).get("family", flc), time=fl_time.get(flc)) if flc else None),
        cls=cls,
        safety=dict(sc=0, vsc=0, red=0, unknown=True),   # f1.com does not publish this
        nolaps=True)                                     # no lap-by-lap -> card disables those tabs
    return dentries, race_entry, cal


def main():
    args = sys.argv[1:]
    if "--round" not in args:
        raise SystemExit(__doc__)
    rnd = int(args[args.index("--round") + 1])
    do = "--apply" in args
    f1 = json.load(open(F1P))
    dentries, race_entry, cal = build(f1, rnd)

    print("round %d — %s" % (rnd, cal["full"]))
    print("  classified %d | winner %s | pole %s | fastest %s"
          % (len(dentries), race_entry["winner"]["family"], race_entry["pole"]["family"],
             (race_entry["fl"] or {}).get("family", "—")))
    print("  top 5: " + ", ".join("%s %s(%s)" % (c["pos"], c["code"], c["pts"]) for c in race_entry["cls"][:5]))
    if not do:
        print("\ndry run — pass --apply to write data/f1.json"); return

    for dr in f1["drivers"]:
        dr["races"] = [r for r in dr["races"] if r["r"] != rnd]
        if dr["code"] in dentries:
            dr["races"].append(dentries[dr["code"]])
        dr["races"].sort(key=lambda r: r["r"])
    f1["races"] = [r for r in f1["races"] if r["r"] != rnd] + [race_entry]
    f1["races"].sort(key=lambda r: r["r"])
    f1["rounds"] = [r for r in f1["rounds"] if r["r"] != rnd] + [
        {"r": rnd, "country": cal["country"], "flag": cal["flag"], "gp": cal["gp"],
         "circuit": cal["circuit"], "locality": cal["locality"], "date": cal["date"]}]
    f1["rounds"].sort(key=lambda r: r["r"])
    f1["raceLaps"][str(rnd)] = race_entry["laps"]
    f1["meta"]["round"] = max(r["r"] for r in f1["races"])
    lastcal = next(c for c in f1["calendar"] if c["r"] == f1["meta"]["round"])
    f1["meta"]["roundName"] = lastcal["gp"]; f1["meta"]["roundCountry"] = lastcal["country"]
    ur.recompute(f1)                                   # same standings maths as the jolpica path
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nAPPLIED round %d from f1.com (no lap data)" % rnd)
    print("  drivers lead: %s %d | rebuild index.html and cross-check on formula1.com"
          % (f1["drivers"][0]["code"], f1["drivers"][0]["points"]))


if __name__ == "__main__":
    main()
