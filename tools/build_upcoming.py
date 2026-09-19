#!/usr/bin/env python3
"""Schedule-only cards for the rounds still to come -> data/upcoming.json.

  python3 tools/build_upcoming.py            # dry run
  python3 tools/build_upcoming.py --apply

One entry per calendar round after the last one raced, in the same shape as live.json so
the card can reuse the weekend layout: circuit, lap count, distance, timezone and the
session times. Session times come from jolpica, which carries the whole calendar, and the
circuit figures from update_live.py's CIRCUIT_INFO so the two agree.

The round currently being run has live.json and is skipped by the app, not here - the
file is built once and stays correct as rounds fall off the front of it.
"""
import importlib.util, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "upcoming.json")
YEAR = 2026

_s = importlib.util.spec_from_file_location("ul", os.path.join(ROOT, "tools", "update_live.py"))
ul = importlib.util.module_from_spec(_s); _s.loader.exec_module(ul)


def jolpica(rnd):
    url = "https://api.jolpi.ca/ergast/f1/%d/%d.json" % (YEAR, rnd)
    try:
        d = json.load(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=45))
        races = d["MRData"]["RaceTable"]["Races"]
        return races[0] if races else None
    except Exception as e:
        print("   jolpica round %d failed: %s" % (rnd, e)); return None


def main():
    do = "--apply" in sys.argv
    f1 = json.load(open(os.path.join(ROOT, "data", "f1.json")))
    out = []
    for cal in f1["calendar"]:
        if cal["r"] <= f1["meta"]["round"]:
            continue
        jr = jolpica(cal["r"]); time.sleep(1)
        sched = []
        if jr:
            for jkey, skey, label in ul.SESSION_MAP:
                blk = jr.get(jkey) if jkey != "Race" else {"date": jr.get("date"), "time": jr.get("time")}
                if not blk or not blk.get("date"):
                    continue
                sched.append({"key": skey, "name": label,
                              "start": ("%sT%s" % (blk["date"], blk.get("time") or "00:00:00Z")).replace("ZZ", "Z")})
        # key on jolpica's circuitId, exactly as update_live.py does - guessing from the
        # circuit name misses the ones named after people (rodriguez, interlagos)
        cid = (jr or {}).get("Circuit", {}).get("circuitId")
        circ = ul.CIRCUIT_INFO.get(cid) or (f1.get("circuits") or {}).get(str(cal["r"])) or {}
        laps, length = circ.get("laps"), circ.get("length")
        dist = circ.get("distance") or (round(float(laps) * float(length), 3) if laps and length else None)
        out.append(dict(round=cal["r"], gp=cal["gp"], full=cal["full"], short=cal["short"],
                        flag=cal["flag"], country=cal["country"], circuit=cal["circuit"],
                        locality=cal["locality"], date=cal["date"],
                        laps=laps, length=length, distance=dist, circuitId=cid,
                        tz=ul.CIRCUIT_TZ.get(cal["locality"]), sessions=sched))
        print("  r%-3d %-14s %-30s %d sessions%s"
              % (cal["r"], cal["short"], cal["circuit"][:29], len(sched),
                 "" if circ else "   (no circuit figures)"))
    if not do:
        print("\ndry run - pass --apply to write data/upcoming.json"); return
    json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE data/upcoming.json - %d rounds, %.0f KB" % (len(out), os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
