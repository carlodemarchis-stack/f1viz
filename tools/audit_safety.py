#!/usr/bin/env python3
"""Re-derive every round's safety-car / VSC / red-flag counts from OpenF1 race control.

  python3 tools/audit_safety.py             # audit all rounds, print the evidence
  python3 tools/audit_safety.py --apply     # write the corrected counts + lap flags

Needed because openf1_safety() in update_round.py carried three faults until 2026-09-07:
"RED FLAG" matched CHEQUERED FLAG (a phantom red flag on every last lap), VSC was only
matched as "VIRTUAL SAFETY CAR" when OpenF1 writes "VSC DEPLOYED" (so every VSC counted
as zero), and SAFETY CAR DEPLOYED + LIGHTS ON were counted as two deployments.

Every count is printed with the race-control lines it came from, so the numbers can be
checked by eye rather than taken on trust. Sessions are matched by date, not by country
name, because the calendar's country field does not always match OpenF1's ("UK").
"""
import json, os, re, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
YEAR = 2026


def api(ep, **params):
    url = "https://api.openf1.org/v1/%s?%s" % (ep, "&".join("%s=%s" % kv for kv in params.items()))
    for a in range(5):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(r, timeout=60))
        except Exception as e:
            if a == 4:
                print("   OpenF1 %s failed: %s" % (ep, e)); return None
            time.sleep(3 * (a + 1))


def counts(rc):
    """-> (sc, vsc, red), {lap: flag}, [evidence lines]   — same rules as update_round.openf1_safety"""
    sc = vsc = red = 0
    flags, ev, seen = {}, [], set()
    for m in sorted(rc, key=lambda x: str(x.get("date"))):
        msg = str(m.get("message") or "").strip()
        u = msg.upper()
        lap = m.get("lap_number")
        key = (str(m.get("date")), u)
        if key in seen:                      # OpenF1 duplicates messages; count a deployment once
            continue
        seen.add(key)
        # anchored: CHEQUERED FLAG must not match, and neither must the stewards' lines about a
        # "RED FLAG INFRINGEMENT", which is a penalty for a driver, not a stoppage (Monaco r6)
        if re.match(r"RED FLAG\b", u):
            red += 1
            if lap: flags[str(lap)] = "S"
            ev.append("L%-3s RED  %s" % (lap, msg))
        elif ("VSC" in u or "VIRTUAL SAFETY CAR" in u) and "DEPLOYED" in u:
            vsc += 1
            if lap: flags[str(lap)] = "V"
            ev.append("L%-3s VSC  %s" % (lap, msg))
        elif "SAFETY CAR" in u and ("DEPLOYED" in u or "LIGHTS ON" in u):
            if "DEPLOYED" in u:
                sc += 1
                ev.append("L%-3s SC   %s" % (lap, msg))
            if lap: flags[str(lap)] = "S"
    return (sc, vsc, red), flags, ev


def main():
    do = "--apply" in sys.argv
    verbose = "-v" in sys.argv
    f1 = json.load(open(F1P))
    cals = {c["r"]: c for c in f1["calendar"]}

    ses = api("sessions", year=YEAR, session_name="Race") or []
    by_date = {str(s.get("date_start", ""))[:10]: s for s in ses}
    print("OpenF1 race sessions for %d: %d\n" % (YEAR, len(by_date)))

    changed = []
    for race in sorted(f1["races"], key=lambda r: r["r"]):
        rnd = race["r"]; cal = cals[rnd]
        s = by_date.get(cal["date"])
        if not s:
            print("r%-3d %-26s NO OpenF1 session for %s — left alone" % (rnd, cal["full"], cal["date"]))
            continue
        rc = api("race_control", session_key=s["session_key"])
        time.sleep(1)
        if rc is None:
            print("r%-3d %-26s race_control unavailable — left alone" % (rnd, cal["full"]))
            continue
        (sc, vsc, red), flags, ev = counts(rc)
        cur = race.get("safety") or {}
        old = (cur.get("sc", 0), cur.get("vsc", 0), cur.get("red", 0))
        new = (sc, vsc, red)
        mark = "same" if old == new else "**DIFF**"
        print("r%-3d %-26s stored %d/%d/%d -> derived %d/%d/%d  %s%s"
              % (rnd, cal["full"], old[0], old[1], old[2], sc, vsc, red, mark,
                 "  (unknown)" if cur.get("unknown") else ""))
        if ev and (verbose or old != new):
            for line in ev:
                print("        %s" % line[:96])
        if old == new:
            continue                      # leave a round that already agrees completely alone
        changed.append((rnd, old, new, flags))
        if do:
            race["safety"] = {"sc": sc, "vsc": vsc, "red": red}
            lp = os.path.join(ROOT, "data", "laps", "r%d.json" % rnd)
            if os.path.exists(lp) and flags:
                lo = json.load(open(lp))
                if lo.get("flags") != flags:
                    print("        flags %s -> %s" % (lo.get("flags"), flags))
                    lo["flags"] = flags
                    json.dump(lo, open(lp, "w"), ensure_ascii=False, separators=(",", ":"))

    print("\n%d round(s) differ" % len(changed))
    for rnd, old, new, _ in changed:
        print("   r%-3d %d SC/%d VSC/%d red  ->  %d SC/%d VSC/%d red" % (rnd, *old, *new))
    if not do:
        print("\ndry run — pass --apply to write f1.json + laps flags"); return
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE data/f1.json (+ lap flag files) — rebuild index.html")


if __name__ == "__main__":
    main()
