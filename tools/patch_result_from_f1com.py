#!/usr/bin/env python3
"""Re-sync one round's classification with formula1.com, keeping everything else.

  python3 tools/patch_result_from_f1com.py --round 6           # dry run
  python3 tools/patch_result_from_f1com.py --round 6 --apply

For when a result is amended after the fact and jolpica still serves the old one -
Monaco 2026, where the FIA International Court of Appeal reinstated Gasly's pit-lane
penalties and moved Hadjar onto the podium.

Only the finishing fields move (position, points, status, time) plus the races[] entry
they feed. Lap-by-lap data, safety-car counts, pit stops, grid and qualifying are left
untouched, so this does not throw away anything jolpica gave us. Standings are then
recomputed with update_round.recompute(), the same function the normal path uses.

NOTE: re-running `update_round.py --apply <r>` re-fetches from jolpica and will revert
this if jolpica has still not published the amendment.
"""
import json, os, sys, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")

def _load(name, path):
    s = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", path))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

ur = _load("ur", "update_round.py")
af = _load("af", "apply_from_f1com.py")

FIELDS = ("fin", "ptxt", "pts", "rpts", "status", "time")


def main():
    args = sys.argv[1:]
    if "--round" not in args:
        raise SystemExit(__doc__)
    rnd = int(args[args.index("--round") + 1])
    do = "--apply" in args
    f1 = json.load(open(F1P))
    fresh, fresh_race, cal = af.build(f1, rnd)

    changes = []
    for dr in f1["drivers"]:
        nd = fresh.get(dr["code"])
        if not nd:
            continue
        for r in dr["races"]:
            if r["r"] != rnd:
                continue
            # only touch drivers the amendment actually moved; leave everyone else alone
            if r.get("fin") == nd["fin"] and r.get("pts") == nd["pts"]:
                continue
            diff = {k: (r.get(k), nd[k]) for k in FIELDS if r.get(k) != nd[k]}
            if diff:
                changes.append((dr["code"], diff))
                if do:
                    for k in FIELDS:
                        r[k] = nd[k]

    if not changes:
        print("round %d already matches f1.com" % rnd); return
    print("round %d — %s : %d drivers differ from f1.com" % (rnd, cal["full"], len(changes)))
    for code, diff in sorted(changes, key=lambda c: fresh[c[0]]["fin"] or 99):
        bits = ", ".join("%s %s->%s" % (k, a, b) for k, (a, b) in diff.items())
        print("   %-4s %s" % (code, bits))
    if not do:
        print("\ndry run — pass --apply to write data/f1.json"); return

    # races[] entry: refresh the classification rows and the headline three, keep the rest
    for i, race in enumerate(f1["races"]):
        if race["r"] != rnd:
            continue
        race["cls"] = fresh_race["cls"]
        race["winner"] = fresh_race["winner"]
        if fresh_race.get("pole"): race["pole"] = fresh_race["pole"]
        if fresh_race.get("fl"): race["fl"] = fresh_race["fl"]
        # deliberately NOT copied: laps, safety, lat/long, and no nolaps flag
    ur.recompute(f1)
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nPATCHED round %d from f1.com — standings recomputed" % rnd)
    print("  drivers lead: %s %d" % (f1["drivers"][0]["code"], f1["drivers"][0]["points"]))


if __name__ == "__main__":
    main()
