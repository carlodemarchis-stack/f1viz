#!/usr/bin/env python3
"""Build a round's lap-by-lap file from OpenF1 when jolpica has not published it.

  python3 tools/laps_from_openf1.py --round 13            # dry run
  python3 tools/laps_from_openf1.py --round 13 --apply

Writes data/laps/r<N>.json in the same shape update_round.py produces, so Lap evolution
and The Wall work unchanged, and updates the round in f1.json: clears `nolaps`, fills in
each driver's pit count, and sets the safety-car / VSC / red-flag counts from race control.

OpenF1 gives lap times directly but not the running order, so the order per lap is
derived: for each lap, take the leader's crossing time and resolve every driver's latest
position event at or before it.

Running `update_round.py --apply <r>` later replaces all of this from jolpica.
"""
import json, os, re, sys, time, urllib.request, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
YEAR = 2026


def api(ep, **params):
    q = "&".join("%s=%s" % kv for kv in params.items())
    url = "https://api.openf1.org/v1/%s?%s" % (ep, q)
    for attempt in range(5):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(r, timeout=60))
        except Exception as e:
            if attempt == 4:
                raise SystemExit("OpenF1 %s failed: %s" % (ep, e))
            time.sleep(3 * (attempt + 1))          # it rate-limits; back off rather than hammer


def clock(sec):
    """83.504 -> '1:23.504'   (and 1846.2 -> '30:46.200' for a red-flag stoppage)"""
    if sec is None:
        return None
    m, s = divmod(float(sec), 60)
    return ("%d:%06.3f" % (int(m), s)) if m >= 1 else ("%.3f" % s)


def parse(ts):
    return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def main():
    args = sys.argv[1:]
    if "--round" not in args:
        raise SystemExit(__doc__)
    rnd = int(args[args.index("--round") + 1]); do = "--apply" in args
    f1 = json.load(open(F1P))
    cal = next(c for c in f1["calendar"] if c["r"] == rnd)
    race = next(r for r in f1["races"] if r["r"] == rnd)
    num2code = {int(d["num"]): d["code"] for d in f1["drivers"] if d.get("num")}

    ses = api("sessions", year=YEAR, country_name=cal["country"].replace(" ", "%20"))
    rs = [s for s in ses if s.get("session_name") == "Race" and str(s.get("date_start", ""))[:10] == cal["date"]]
    if not rs:
        raise SystemExit("no OpenF1 race session for round %d" % rnd)
    sk = rs[0]["session_key"]; time.sleep(1)

    laps = api("laps", session_key=sk); time.sleep(1)
    pos = api("position", session_key=sk); time.sleep(1)
    pits = api("pit", session_key=sk); time.sleep(1)
    rc = api("race_control", session_key=sk)

    nLaps = max(l["lap_number"] for l in laps)
    # lap times per lap
    times = [{} for _ in range(nLaps)]
    start_of = {}                                   # (lap) -> earliest date_start, to find the leader's crossing
    for l in laps:
        c = num2code.get(l["driver_number"])
        if not c:
            continue
        ln = l["lap_number"]
        if l.get("lap_duration"):
            times[ln - 1][c] = clock(l["lap_duration"])
        if l.get("date_start"):
            d = parse(l["date_start"])
            if ln not in start_of or d < start_of[ln]:
                start_of[ln] = d

    # running order: resolve each driver's latest position at the leader's lap crossing
    evs = sorted([(parse(p["date"]), p["driver_number"], p["position"]) for p in pos if p.get("position")],
                 key=lambda x: x[0])
    order, cur, i = [], {}, 0
    for ln in range(1, nLaps + 1):
        boundary = start_of.get(ln + 1) or (start_of.get(ln) or evs[-1][0]) + datetime.timedelta(minutes=5)
        while i < len(evs) and evs[i][0] <= boundary:
            cur[evs[i][1]] = evs[i][2]; i += 1
        row = sorted(((p, n) for n, p in cur.items() if num2code.get(n)), key=lambda x: x[0])
        order.append([num2code[n] for _, n in row])

    grid = [c["code"] for c in sorted(race["cls"], key=lambda c: c["grid"] or 99) if c["grid"]]
    pit, pitTime = {}, {}
    for p in sorted(pits, key=lambda x: (x["driver_number"], x.get("lap_number") or 0)):
        c = num2code.get(p["driver_number"])
        if not c:
            continue
        ln = p.get("lap_number")
        dur = p.get("lane_duration") if p.get("lane_duration") is not None else p.get("pit_duration")
        pit.setdefault(c, []).append(ln)
        pitTime.setdefault(c, {})[str(ln)] = clock(dur)

    # safety car / VSC / red flag from race control
    flags, sc, vsc, red = {}, 0, 0, 0
    for x in sorted(rc, key=lambda x: str(x.get("date"))):
        msg = str(x.get("message", "")).upper(); ln = x.get("lap_number")
        if "SAFETY CAR DEPLOYED" in msg: sc += 1; flags[str(ln)] = "S"
        elif "VSC DEPLOYED" in msg: vsc += 1; flags[str(ln)] = "V"
        elif re.search(r"\bRED FLAG\b", msg): red += 1   # \b so CHEQUERED FLAG does not match
    if red:                                        # hold the flag through the stoppage lap
        for x in rc:
            if re.search(r"\bRED FLAG\b", str(x.get("message", "")).upper()) and x.get("lap_number"):
                flags[str(x["lap_number"])] = "S"

    obj = dict(race=cal["full"], round=rnd, nLaps=nLaps, grid=grid,
               order=order, times=times, pit=pit, pitTime=pitTime, flags=flags)

    filled = sum(1 for t in times if t)
    print("round %d — %s" % (rnd, cal["full"]))
    print("  %d laps | %d with times | order rows %d | %d drivers pitted (%d stops)"
          % (nLaps, filled, len(order), len(pit), sum(len(v) for v in pit.values())))
    print("  safety: %d SC, %d VSC, %d red  | flags %s" % (sc, vsc, red, flags))
    print("  lap 1 order: %s ..." % ", ".join(order[0][:5]))
    print("  final order: %s ..." % ", ".join(order[-1][:5]))
    if not do:
        print("\ndry run — pass --apply to write the laps file and update f1.json"); return

    json.dump(obj, open(os.path.join(ROOT, "data", "laps", "r%d.json" % rnd), "w"),
              ensure_ascii=False, separators=(",", ":"))
    race.pop("nolaps", None)
    race["safety"] = dict(sc=sc, vsc=vsc, red=red)
    for dr in f1["drivers"]:
        for r in dr["races"]:
            if r["r"] == rnd:
                r["pits"] = len(pit.get(dr["code"], []))
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE data/laps/r%d.json and updated f1.json (nolaps cleared, pits + safety filled)" % rnd)


if __name__ == "__main__":
    main()
