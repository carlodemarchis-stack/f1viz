#!/usr/bin/env python3
"""Build data/extras.json — per-race tyre stints, overtakes and weather from OpenF1.

  python3 tools/fetch_extras.py              # every round, dry run
  python3 tools/fetch_extras.py --apply
  python3 tools/fetch_extras.py --round 14 --apply    # just the new race

Deliberately a file of its own rather than fields on f1.json: update_round.py --validate
proves it can still reproduce f1.json exactly, and adding data it does not generate would
break that check. Same reason recaps.json and radio.json sit outside it.

Overtakes are position changes, so a standing start logs one for most of the field. Lap 1
is excluded, and so is a restart lap after a red flag - both re-sort the grid rather than
being raced for. A safety-car restart is NOT excluded: the field is bunched but the passes
are real. Both counts are kept, so the UI can say which it is showing.
"""
import bisect, datetime, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "extras.json")
YEAR = 2026


def api(ep, **params):
    url = "https://api.openf1.org/v1/%s?%s" % (ep, "&".join("%s=%s" % kv for kv in params.items()))
    for a in range(5):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(r, timeout=90))
        except Exception as e:
            if a == 4:
                print("   OpenF1 %s failed: %s" % (ep, e)); return None
            time.sleep(6 * (a + 1))          # it rate-limits hard; back off rather than hammer


def parse(ts):
    return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def grid_starts(rc):
    """Laps whose racing began from a standing grid: lap 1, plus post-red-flag restarts.

    'OVERTAKE ENABLED' marks every restart including safety-car ones, so it cannot be used
    alone - Shanghai's SC restart on lap 14 is racing. Pairing it with a nearby
    'STANDING START' keeps only the grid re-sorts."""
    oe = sorted({m.get("lap_number") for m in rc
                 if "OVERTAKE ENABLED" in str(m.get("message", "")).upper() and m.get("lap_number")})
    ss = sorted({m.get("lap_number") for m in rc
                 if "STANDING START" in str(m.get("message", "")).upper() and m.get("lap_number")})
    out = {1}
    for L in oe:
        if L > 1 and any(s <= L <= s + 3 for s in ss):
            out.add(L)
    return sorted(out)


def build_round(rnd, cal, num2code, sk):
    laps = api("laps", session_key=sk);  time.sleep(4)
    st = api("stints", session_key=sk);  time.sleep(4)
    ov = api("overtakes", session_key=sk); time.sleep(4)
    rc = api("race_control", session_key=sk); time.sleep(4)
    wx = api("weather", session_key=sk); time.sleep(4)
    if laps is None or st is None or ov is None or rc is None:
        return None, None, None

    start = {}
    for l in laps:
        if l.get("date_start"):
            d, n = parse(l["date_start"]), l["lap_number"]
            if n not in start or d < start[n]:
                start[n] = d
    nLaps = max(start) if start else 0
    bounds = [start[k] for k in sorted(start)]
    lap_of = lambda t: max(1, min(nLaps, bisect.bisect_right(bounds, parse(t))))

    stints = {}
    for s in sorted(st, key=lambda x: (x.get("driver_number") or 0, x.get("stint_number") or 0)):
        c = num2code.get(s.get("driver_number"))
        if not c or not s.get("compound"):
            continue
        stints.setdefault(c, []).append(
            [s["compound"][0].upper(), s["lap_start"], s["lap_end"], s.get("tyre_age_at_start") or 0])

    skip = grid_starts(rc)
    # Two measures, because the feed logs every position swap: Lawson "passed" Hulkenberg
    # six times at Suzuka, so the event count rewards being stuck in a DRS train. `by`
    # counts events, `dist` counts how many different cars a driver got past - steadier,
    # and closer to what a published overtake figure means.
    by, raw, seen = {}, {}, {}
    for o in ov:
        c = num2code.get(o.get("overtaking_driver_number"))
        if not c:
            continue
        raw[c] = raw.get(c, 0) + 1
        if lap_of(o["date"]) not in skip:
            by[c] = by.get(c, 0) + 1
            seen.setdefault(c, set()).add(num2code.get(o.get("overtaken_driver_number")))
    dist = {c: len(v) for c, v in seen.items()}
    overtakes = dict(total=sum(by.values()), raw=len(ov), skip=skip,
                     by=dict(sorted(by.items(), key=lambda x: -x[1])),
                     dist=dict(sorted(dist.items(), key=lambda x: -x[1])))

    # weather arrives every ~30s; average it onto laps so it shares the strategy chart's axis
    weather = None
    if wx:
        acc = {}
        for w in wx:
            if not w.get("date"):
                continue
            L = lap_of(w["date"])
            a = acc.setdefault(L, {"t": [], "a": [], "r": 0, "h": []})
            if w.get("track_temperature") is not None: a["t"].append(w["track_temperature"])
            if w.get("air_temperature") is not None:   a["a"].append(w["air_temperature"])
            if w.get("humidity") is not None:          a["h"].append(w["humidity"])
            if w.get("rainfall"):                      a["r"] = 1
        avg = lambda v: round(sum(v) / len(v)) if v else None
        weather = dict(
            t=[avg(acc.get(L, {}).get("t", [])) for L in range(1, nLaps + 1)],
            a=[avg(acc.get(L, {}).get("a", [])) for L in range(1, nLaps + 1)],
            h=[avg(acc.get(L, {}).get("h", [])) for L in range(1, nLaps + 1)],
            r=[acc.get(L, {}).get("r", 0) for L in range(1, nLaps + 1)])
    return stints, overtakes, weather


def main():
    args = sys.argv[1:]
    do = "--apply" in args
    only = int(args[args.index("--round") + 1]) if "--round" in args else None
    f1 = json.load(open(os.path.join(ROOT, "data", "f1.json")))
    num2code = {int(d["num"]): d["code"] for d in f1["drivers"] if d.get("num")}
    cals = {c["r"]: c for c in f1["calendar"]}

    out = json.load(open(OUT)) if os.path.exists(OUT) else {"meta": {}, "stints": {}, "overtakes": {}, "weather": {}}
    ses = api("sessions", year=YEAR, session_name="Race") or []
    by_date = {str(s.get("date_start", ""))[:10]: s for s in ses}
    time.sleep(3)

    rounds = [only] if only else [r["r"] for r in f1["races"]]
    for rnd in rounds:
        cal = cals[rnd]
        s = by_date.get(cal["date"])
        if not s:
            print("  r%-3d %-16s no OpenF1 session" % (rnd, cal["locality"])); continue
        stints, ov, wx = build_round(rnd, cal, num2code, s["session_key"])
        if stints is None:
            print("  r%-3d %-16s fetch failed - left alone" % (rnd, cal["locality"])); continue
        top = next(iter(ov["by"].items()), ("-", 0))
        topd = next(iter(ov["dist"].items()), ("-", 0))
        print("  r%-3d %-16s %2d stints | events %3d of %3d raw (skip %s) | most events %s %d | most cars passed %s %d"
              % (rnd, cal["locality"], len(stints), ov["total"], ov["raw"], ov["skip"],
                 top[0], top[1], topd[0], topd[1]))
        out["stints"][str(rnd)] = stints
        out["overtakes"][str(rnd)] = ov
        if wx:
            out.setdefault("weather", {})[str(rnd)] = wx
            rl = sum(wx["r"]); tt = [x for x in wx["t"] if x is not None]
            print("        weather: track %d-%d°C%s" % (min(tt), max(tt),
                  (", rain on %d lap%s" % (rl, "" if rl == 1 else "s")) if rl else ", dry"))

    out["meta"] = dict(season=YEAR, kind="per-race tyre stints, overtake summaries and per-lap weather (OpenF1)",
                       throughRound=max(int(k) for k in out["stints"]) if out["stints"] else 0,
                       note="overtake totals exclude lap 1 and post-red-flag standing restarts")
    if not do:
        print("\ndry run - pass --apply to write data/extras.json"); return
    json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE data/extras.json (%.0f KB) - rebuild index.html" % (os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
