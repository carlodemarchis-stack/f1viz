#!/usr/bin/env python3
"""Find F1's official race-highlights video for a round and store its id.

  python3 tools/find_highlights.py --validate           # re-find every stored round, must all match
  python3 tools/find_highlights.py --round 13           # dry run
  python3 tools/find_highlights.py --round 13 --apply   # writes f1.json['highlights']['13']

Searches YouTube (no API key: the results page carries ytInitialData) and keeps only
videos posted by the FORMULA 1 channel whose title is exactly "Race Highlights | <year>
<GP>". That last part matters - a race weekend also produces "Qualifying Highlights",
"Sprint Highlights", "FP3 Highlights" and F2/F3 feature and sprint races, all with very
similar titles and all ranking alongside the one we want.

The pick is then confirmed against the round's own name (calendar `gp` is the adjective
YouTube uses - Italian, Dutch, Canadian) so a race whose highlights are not up yet
cannot silently take a neighbouring round's video. A candidate that cannot be confirmed
is reported as UNVERIFIED and never written without being read first.
"""
import json, os, re, sys, time, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
YEAR = 2026
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
CHANNEL = "FORMULA 1"


def get(url):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return urllib.request.urlopen(r, timeout=30).read().decode("utf-8", "replace")


def search(q):
    """YouTube results page -> [(videoId, channel, title)]"""
    s = get("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", s, re.S)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if v:
                title = "".join(x.get("text", "") for x in v.get("title", {}).get("runs", []))
                owner = "".join(x.get("text", "") for x in v.get("ownerText", {}).get("runs", []))
                if v.get("videoId"):
                    out.append((v["videoId"], owner, title))
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(json.loads(m.group(1)))
    return out


def find(cal):
    """-> (videoId, title, verified) or (None, None, False)"""
    want = re.compile(r"^Race Highlights\s*\|\s*%d\b" % YEAR, re.I)
    tokens = [t for t in (cal.get("gp"), cal.get("locality"), cal.get("short")) if t]
    seen, best = set(), None
    for q in ("Race Highlights %d %s Grand Prix" % (YEAR, cal["gp"]),
              "F1 Race Highlights %d %s" % (YEAR, cal["locality"]),
              "Race Highlights %d %s Grand Prix" % (YEAR, cal["short"])):
        for vid, owner, title in search(q):
            if vid in seen or owner.strip().upper() != CHANNEL or not want.match(title):
                continue
            seen.add(vid)
            # confirm it is THIS round: the GP adjective / venue must appear in the title
            score = sum(w for t, w in zip(tokens, (3, 2, 1)) if t.lower() in title.lower())
            if best is None or score > best[2]:
                best = (vid, title, score)
        if best and best[2] > 0:
            break
        time.sleep(1)
    if not best:
        return None, None, False
    return best[0], best[1], best[2] > 0


def main():
    args = sys.argv[1:]
    f1 = json.load(open(F1P))
    cals = {c["r"]: c for c in f1["calendar"]}
    hl = f1.setdefault("highlights", {})

    if "--validate" in args:
        ok = bad = 0
        print("re-finding %d stored rounds" % len(hl))
        for k in sorted(hl, key=int):
            vid, title, ver = find(cals[int(k)])
            good = vid == hl[k]
            ok, bad = ok + good, bad + (not good)
            print("   r%-3s %-8s %-11s %s" % (k, "PASS" if good else "FAIL",
                                              vid or "-", title or "(not found)"))
            if not good:
                print("        stored: %s" % hl[k])
            time.sleep(1)
        print("\n%d PASS, %d FAIL" % (ok, bad))
        raise SystemExit(1 if bad else 0)

    if "--round" not in args:
        raise SystemExit(__doc__)
    rnd = int(args[args.index("--round") + 1]); do = "--apply" in args
    cal = cals[rnd]
    vid, title, ver = find(cal)
    print("round %d — %s" % (rnd, cal["full"]))
    if not vid:
        print("  no official race highlights found yet (F1 often posts a day or two later)")
        return
    print("  %s  %s" % (vid, title))
    print("  https://youtu.be/%s" % vid)
    if not ver:
        print("  UNVERIFIED — the title does not name this round; read it before applying")
    if hl.get(str(rnd)) == vid:
        print("  already stored"); return
    if not do:
        print("\ndry run — pass --apply to write data/f1.json"); return
    if not ver:
        raise SystemExit("refusing to store an unverified match")
    hl[str(rnd)] = vid
    json.dump(f1, open(F1P, "w"), ensure_ascii=False, separators=(",", ":"))
    print("\nWROTE f1.json highlights[%d] = %s — rebuild index.html" % (rnd, vid))


if __name__ == "__main__":
    main()
