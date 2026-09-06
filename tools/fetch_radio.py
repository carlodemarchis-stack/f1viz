#!/usr/bin/env python3
"""Pull a round's team radio from OpenF1 into data/radio.json + audio-radio/.

  python3 tools/fetch_radio.py --round 13           # dry run: list what it would take
  python3 tools/fetch_radio.py --round 13 --apply

Each clip is mapped to a lap by bisecting the leader's lap start times, the mp3 is
downloaded next to the others, and the entry is added under the driver's code. Captions
are a separate pass: tools/gen_radio_captions.py small.en (resumable, skips clips that
already have one).

Re-running is safe: clips already present for the round are left alone and their
captions preserved, so this can be run again if OpenF1 publishes more later.
"""
import json, os, re, sys, time, bisect, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RJ = os.path.join(ROOT, "data", "radio.json")
AUD = os.path.join(ROOT, "audio-radio")
YEAR = 2026


def api(ep, **params):
    url = "https://api.openf1.org/v1/%s?%s" % (ep, "&".join("%s=%s" % kv for kv in params.items()))
    for a in range(5):
        try:
            r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(r, timeout=60))
        except Exception as e:
            if a == 4:
                raise SystemExit("OpenF1 %s failed: %s" % (ep, e))
            time.sleep(3 * (a + 1))


def parse(ts):
    return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def main():
    args = sys.argv[1:]
    if "--round" not in args:
        raise SystemExit(__doc__)
    rnd = int(args[args.index("--round") + 1]); do = "--apply" in args
    f1 = json.load(open(os.path.join(ROOT, "data", "f1.json")))
    cal = next(c for c in f1["calendar"] if c["r"] == rnd)
    num2code = {int(d["num"]): d["code"] for d in f1["drivers"] if d.get("num")}

    ses = api("sessions", year=YEAR, country_name=cal["country"].replace(" ", "%20"))
    rs = [s for s in ses if s.get("session_name") == "Race" and str(s.get("date_start", ""))[:10] == cal["date"]]
    if not rs:
        raise SystemExit("no OpenF1 race session for round %d" % rnd)
    sk = rs[0]["session_key"]; time.sleep(1)

    laps = api("laps", session_key=sk); time.sleep(1)
    clips = api("team_radio", session_key=sk)

    # leader's crossing time per lap, so a clip's timestamp can be bisected onto a lap
    start = {}
    for l in laps:
        if not l.get("date_start"):
            continue
        d = parse(l["date_start"])
        if l["lap_number"] not in start or d < start[l["lap_number"]]:
            start[l["lap_number"]] = d
    nLaps = max(start) if start else 0
    bounds = [start[k] for k in sorted(start)]

    rj = json.load(open(RJ))
    prev = rj["radio"].get(str(rnd), {})
    have = {c["f"] for lst in prev.values() for c in lst}          # keep existing clips + their captions

    out, added, skipped = {}, 0, 0
    for c in sorted(clips, key=lambda x: str(x.get("date"))):
        url = c.get("recording_url")
        code = num2code.get(c.get("driver_number"))
        if not url or not code:
            continue
        fn = url.rsplit("/", 1)[-1]
        if fn in have:
            skipped += 1; continue
        t = parse(c["date"])
        lap = max(1, min(nLaps, bisect.bisect_right(bounds, t)))   # clamp: pre-race chatter -> lap 1
        out.setdefault(code, []).append(
            {"lap": lap, "t": t.strftime("%H:%M:%S"), "url": url, "f": fn})
        added += 1

    print("round %d — %s" % (rnd, cal["full"]))
    print("  %d clips from OpenF1 | %d new, %d already stored | %d laps" % (len(clips), added, skipped, nLaps))
    for code in sorted(out, key=lambda c: -len(out[c])):
        laps_ = ", ".join("L%d" % x["lap"] for x in out[code])
        print("   %-4s %2d  %s" % (code, len(out[code]), laps_[:70]))
    if not do:
        print("\ndry run — pass --apply to download the mp3s and write radio.json"); return

    os.makedirs(AUD, exist_ok=True)
    got = fail = 0
    for code, lst in out.items():
        for c in lst:
            dst = os.path.join(AUD, c["f"])
            if os.path.exists(dst):
                got += 1; continue
            try:
                r = urllib.request.Request(c["url"], headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(r, timeout=60).read()
                open(dst, "wb").write(data); got += 1
            except Exception as e:
                print("   download failed %s: %s" % (c["f"], e)); fail += 1
            time.sleep(0.2)

    merged = {k: list(v) for k, v in prev.items()}
    for code, lst in out.items():
        merged.setdefault(code, []).extend(lst)
        merged[code].sort(key=lambda x: x["t"])
    rj["radio"][str(rnd)] = merged
    rj["meta"]["throughRound"] = max(int(k) for k in rj["radio"])
    rj["meta"]["total"] = sum(len(v) for r in rj["radio"].values() for v in r.values())
    rj["meta"]["count"] = len([f for f in os.listdir(AUD) if f.endswith(".mp3")])
    json.dump(rj, open(RJ, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE radio.json — round %d now has %d clips across %d drivers (%d mp3s downloaded, %d failed)"
          % (rnd, sum(len(v) for v in merged.values()), len(merged), got, fail))
    print("  next: python3 tools/gen_radio_captions.py small.en   (fills 'cap' per clip)")


if __name__ == "__main__":
    main()
