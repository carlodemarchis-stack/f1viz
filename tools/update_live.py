#!/usr/bin/env python3
"""In-progress race weekend for F1viz -> data/live.json

Builds the "weekend in progress" payload for the NEXT round (the first calendar round
after meta.round), using the SAME sources as the race pipeline:
  - jolpica-f1  : session schedule (FP1/FP2/FP3/Sprint/Quali/Race dates+times) + circuit
  - OpenF1      : session keys, driver metadata (incl. FP-only rookies) and session_result

  python3 tools/update_live.py            # refresh the in-progress round
  python3 tools/update_live.py --round 13 # force a specific round
  python3 tools/update_live.py --clear    # drop live.json (race done -> use update_round.py)

Re-run it after each session (FP1, FP2, FP3, Quali), then rebuild index.html and push.
Once the race has run, use update_round.py --apply <r> instead; live.json is ignored
automatically as soon as meta.round reaches that round (and --clear empties it).
"""
import json, os, sys, subprocess, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
LIVEP = os.path.join(ROOT, "data", "live.json")
YEAR = 2026

# jolpica schedule key -> (our session key, label). Order defines display order.
SESSION_MAP = [
    ("FirstPractice",  "fp1",    "Practice 1"),
    ("SecondPractice", "fp2",    "Practice 2"),
    ("ThirdPractice",  "fp3",    "Practice 3"),
    ("SprintQualifying", "sq",   "Sprint Qualifying"),
    ("Sprint",         "sprint", "Sprint"),
    ("Qualifying",     "quali",  "Qualifying"),
    ("Race",           "race",   "Race"),
]
# our session key -> OpenF1 session_name
OPENF1_NAME = {"fp1": "Practice 1", "fp2": "Practice 2", "fp3": "Practice 3",
               "sq": "Sprint Qualifying", "sprint": "Sprint", "quali": "Qualifying", "race": "Race"}

# f1.json['circuits'] only covers rounds already run, so upcoming tracks need a fallback
# (official laps + length; distance = laps * length). Values cross-checked on formula1.com.
CIRCUIT_INFO = {
    "monza":       {"laps": 53, "length": 5.793, "distance": 306.720},
    "madring":     {"laps": 57, "length": 5.474},
    "baku":        {"laps": 51, "length": 6.003},
    "sepang":      {"laps": 56, "length": 5.543},
    "marina_bay":  {"laps": 62, "length": 4.940},
    "americas":    {"laps": 56, "length": 5.513},
    "rodriguez":   {"laps": 71, "length": 4.304},
    "interlagos":  {"laps": 71, "length": 4.309},
    "vegas":       {"laps": 50, "length": 6.201},
    "losail":      {"laps": 57, "length": 5.419},
    "yas_marina":  {"laps": 58, "length": 5.281},
}


def curl_json(url, timeout="30"):
    out = subprocess.run(["curl", "-s", "--max-time", timeout, url], capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except Exception:
        return None


LAST_ERR = None


def curl_list(url, tries=3):
    """OpenF1 endpoints return a list. Returns None (not []) when the API is unavailable, so a
    failed fetch is never mistaken for 'this session has no results' and used to wipe good data.
    NOTE: OpenF1 answers 401 for EVERYTHING (past sessions included) while a session is live -
    run this after the session has ended, or use an API key."""
    global LAST_ERR
    import time
    for i in range(tries):
        v = curl_json(url)
        if isinstance(v, list):
            return v
        if isinstance(v, dict) and v.get("detail"):
            LAST_ERR = v["detail"]
        time.sleep(1.5 * (i + 1))
    return None


# ---- formula1.com fallback -------------------------------------------------
# OpenF1 is 401-locked while a session is live, and jolpica has no practice data at all,
# so official f1.com session pages are the fallback for FP/Quali classifications.
F1COM_PATH = {"fp1": "practice/1", "fp2": "practice/2", "fp3": "practice/3",
              "sq": "sprint-qualifying", "sprint": "sprint-results",
              "quali": "qualifying", "race": "race-result"}
SLUG_FIX = {"UAE": "united-arab-emirates", "USA": "united-states", "United States": "united-states",
            "Great Britain": "great-britain", "Saudi Arabia": "saudi-arabia", "Abu Dhabi": "abu-dhabi",
            "Las Vegas": "las-vegas", "Mexico": "mexico", "Azerbaijan": "azerbaijan"}


def curl_text(url):
    r = subprocess.run(["curl", "-s", "--max-time", "30", "-A", "Mozilla/5.0", url],
                       capture_output=True, text=True)
    return r.stdout or ""


def f1com_slug(country):
    return SLUG_FIX.get(country, (country or "").lower().replace(" ", "-"))


def f1com_meeting_id(slug):
    """Discover the numeric race id f1.com uses in its results URLs."""
    import re
    m = re.search(r"/en/results/%d/races/(\d+)/" % YEAR, curl_text("https://www.formula1.com/en/racing/%d/%s" % (YEAR, slug)))
    return m.group(1) if m else None


def f1com_table(url):
    """Return the results table as a list of cell-lists (row 0 = header), or None."""
    import re, html as H
    txt = curl_text(url)
    m = re.search(r"<table[^>]*>(.*?)</table>", txt, re.S)
    if not m:
        return None
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S):
        cells = [re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if cells:
            out.append(cells)
    return out or None


def parse_clock(t):
    """'1:22.219' or '+0.226s' or '22.219' -> seconds (float), else None."""
    if not t:
        return None
    t = t.strip().lstrip("+").rstrip("s")
    try:
        if ":" in t:
            m, s = t.split(":")
            return int(m) * 60 + float(s)
        return float(t)
    except ValueError:
        return None


def fmt_lap(sec):
    """83.008 -> '1:23.008'  (None -> None)"""
    if sec is None:
        return None
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return None
    m, s = divmod(sec, 60)
    return ("%d:%06.3f" % (int(m), s)) if m >= 1 else ("%.3f" % s)


def best_duration(d):
    """Practice gives a scalar; qualifying gives [Q1,Q2,Q3] -> take the best set time."""
    if isinstance(d, list):
        vals = [x for x in d if isinstance(x, (int, float))]
        return min(vals) if vals else None
    return d if isinstance(d, (int, float)) else None


def quali_segments(d):
    """Qualifying [Q1,Q2,Q3] -> formatted per-segment times (None where not set)."""
    if not isinstance(d, list):
        return None
    return [fmt_lap(x) if isinstance(x, (int, float)) else None for x in (list(d) + [None, None, None])[:3]]


def team_colour(name, teamcol):
    """Match an f1.com team label ('Red Bull Racing') to a constructor colour ('Red Bull')."""
    n = (name or "").lower()
    for cname, col in teamcol.items():
        c = cname.lower()
        if n == c or n.startswith(c) or c.startswith(n) or c in n or n in c:
            return col
    return "#888"


def f1com_results(mid, slug, skey, bynum, teamcol):
    """Build our result rows from an f1.com session page. Non-leaders are published as a gap,
    so absolute times are reconstructed as leader + gap (exact: f1.com gaps are to 3dp)."""
    import re
    tbl = f1com_table("https://www.formula1.com/en/results/%d/races/%s/%s/%s" % (YEAR, mid, slug, F1COM_PATH[skey]))
    if not tbl or len(tbl) < 2:
        return None
    head = [h.lower() for h in tbl[0]]
    col = lambda *names: next((i for i, h in enumerate(head) if any(n in h for n in names)), None)
    ci = {"pos": col("pos"), "num": col("no."), "drv": col("driver"), "team": col("team"),
          "time": col("time", "gap"), "laps": col("laps"),
          "q1": col("q1"), "q2": col("q2"), "q3": col("q3")}
    out, lead = [], None
    for row in tbl[1:]:
        get = lambda k: row[ci[k]] if ci[k] is not None and ci[k] < len(row) else ""
        try:
            pos = int(get("pos"))
        except ValueError:
            continue                                            # NC / DQ rows carry a non-numeric position
        try:
            num = int(get("num"))
        except ValueError:
            num = None
        code_m = re.search(r"([A-Z]{3})\s*$", get("drv"))
        code = code_m.group(1) if code_m else (get("drv") or "")[:3].upper()
        fd = bynum.get(num)
        # qualifying: best of Q1/Q2/Q3; practice: the single Time/Gap column
        segs = [parse_clock(get(q)) for q in ("q1", "q2", "q3")] if ci["q1"] is not None else []
        if segs and any(segs):
            best = min(x for x in segs if x)
        else:
            v = parse_clock(get("time"))
            if v is None:
                best = None
            elif pos == 1 or lead is None:
                best = v
            else:
                best = lead + v if get("time").strip().startswith("+") else v
        if pos == 1 and best:
            lead = best
        out.append({
            "pos": pos, "code": code, "num": num,
            "name": (fd or {}).get("family") or get("drv").split()[-2].title() if len((get("drv") or "").split()) > 1 else code,
            "team": (fd or {}).get("team") or get("team"),
            "col": (fd or {}).get("color") or team_colour(get("team"), teamcol),
            "time": fmt_lap(best),
            "gap": None if pos == 1 else ("+%.3f" % (best - lead) if best and lead else None),
            "laps": int(get("laps")) if get("laps").isdigit() else None,
            "seg": [fmt_lap(x) if x else None for x in segs] if segs else None,
            "out": False,
            "rookie": fd is None,
        })
    out.sort(key=lambda x: x["pos"])
    return out or None


def main():
    args = sys.argv[1:]
    f1 = json.load(open(F1P))

    if "--clear" in args:
        json.dump({}, open(LIVEP, "w"))
        print("cleared data/live.json")
        return

    done = int(f1["meta"]["round"])
    if "--round" in args:
        rnd = int(args[args.index("--round") + 1])
    else:
        nxt = [c for c in f1["calendar"] if int(c["r"]) > done]
        if not nxt:
            print("season complete - nothing in progress")
            return
        rnd = int(nxt[0]["r"])
    if rnd <= done:
        print("round %d already complete (meta.round=%d) - use update_round.py; run --clear" % (rnd, done))
        return

    cal = next((c for c in f1["calendar"] if int(c["r"]) == rnd), {})

    # ---- schedule + circuit from jolpica ----
    jd = curl_json("https://api.jolpi.ca/ergast/f1/%d/%d.json" % (YEAR, rnd))
    races = (((jd or {}).get("MRData") or {}).get("RaceTable") or {}).get("Races") or []
    if not races:
        print("jolpica has no round %d yet" % rnd)
        return
    jr = races[0]

    schedule = []
    for jkey, skey, label in SESSION_MAP:
        blk = jr.get(jkey) if jkey != "Race" else {"date": jr.get("date"), "time": jr.get("time")}
        if not blk or not blk.get("date"):
            continue
        start = "%sT%s" % (blk["date"], (blk.get("time") or "00:00:00Z"))
        schedule.append({"key": skey, "name": label, "start": start.replace("ZZ", "Z")})

    # ---- OpenF1 sessions for this weekend (match on the race date's meeting) ----
    sessions = curl_list("https://api.openf1.org/v1/sessions?year=%d" % YEAR)
    openf1_down = sessions is None
    if openf1_down:
        sessions = []
        print("OpenF1 unavailable - falling back to formula1.com")
        if LAST_ERR: print("  reason: %s" % LAST_ERR)
    days = {s["start"][:10] for s in schedule}
    wk = [s for s in sessions if (s.get("date_start") or "")[:10] in days]
    by_name = {}
    for s in wk:
        by_name[s.get("session_name")] = s

    circ = (f1.get("circuits") or {}).get(str(rnd)) or {}
    if not circ.get("laps"):                                   # upcoming track: fall back to the known layout
        cid = jr.get("Circuit", {}).get("circuitId")
        info = CIRCUIT_INFO.get(cid)
        if info:
            circ = {"laps": info["laps"], "length": "%.3f" % info["length"],
                    "distance": "%.3f" % info.get("distance", info["laps"] * info["length"])}
    live = {
        "meta": {"season": YEAR, "round": rnd,
                 "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "round": rnd,
        "gp": jr.get("raceName") or cal.get("gp"),
        "full": cal.get("full") or jr.get("raceName"),
        "short": cal.get("short") or (jr.get("Circuit", {}).get("Location", {}) or {}).get("country"),
        "flag": cal.get("flag", ""),
        "country": cal.get("country") or (jr.get("Circuit", {}).get("Location", {}) or {}).get("country"),
        "circuit": jr.get("Circuit", {}).get("circuitName") or cal.get("circuit"),
        "locality": (jr.get("Circuit", {}).get("Location", {}) or {}).get("locality") or cal.get("locality"),
        "date": jr.get("date"),
        "laps": circ.get("laps"),
        "length": circ.get("length"),
        "distance": circ.get("distance"),
        "sessions": [],
    }

    # driver fallbacks from f1.json (regulars); OpenF1 fills in FP-only rookies
    bynum = {int(d["num"]): d for d in f1["drivers"] if d.get("num")}
    teamcol = {c["name"]: c["color"] for c in f1["constructors"]}

    # anything already captured for this round is kept if a fetch fails (never overwrite good data)
    prev = {}
    if os.path.exists(LIVEP):
        try:
            old = json.load(open(LIVEP))
            if old.get("round") == rnd:
                prev = {s["key"]: s.get("results") or [] for s in old.get("sessions", [])}
        except Exception:
            pass
    kept = []

    slug = f1com_slug(live["country"])
    mid = None
    src = {}

    def via_f1com(skey):
        """f1.com fallback - only for sessions whose start time has passed."""
        nonlocal mid
        if mid is None:
            mid = f1com_meeting_id(slug) or ""
        if not mid:
            return None
        return f1com_results(mid, slug, skey, bynum, teamcol)

    for s in schedule:
        of1 = by_name.get(OPENF1_NAME.get(s["key"], ""))
        row = {"key": s["key"], "name": s["name"], "start": s["start"],
               "end": (of1 or {}).get("date_end"), "results": []}
        started = s["start"] <= datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not of1:
            got = via_f1com(s["key"]) if (started and s["key"] in F1COM_PATH) else None
            if got:
                row["results"] = got; src[s["key"]] = "f1.com"
            else:
                row["results"] = prev.get(s["key"], [])
                if row["results"]: kept.append(s["key"])
            live["sessions"].append(row)
            continue
        sk = of1["session_key"]
        res = curl_list("https://api.openf1.org/v1/session_result?session_key=%s" % sk)
        drvl = curl_list("https://api.openf1.org/v1/drivers?session_key=%s" % sk)
        if res is None or drvl is None:                      # API down -> f1.com, else keep what we had
            got = via_f1com(s["key"]) if s["key"] in F1COM_PATH else None
            if got:
                row["results"] = got; src[s["key"]] = "f1.com"
            else:
                row["results"] = prev.get(s["key"], [])
                if row["results"]: kept.append(s["key"])
            live["sessions"].append(row)
            continue
        drv = {x["driver_number"]: x for x in drvl}
        out = []
        for r in res:
            n = r.get("driver_number")
            pos = r.get("position")
            od = drv.get(n, {})
            fd = bynum.get(n)
            code = (fd or {}).get("code") or od.get("name_acronym") or str(n)
            col = (fd or {}).get("color") or ("#" + od["team_colour"] if od.get("team_colour") else "#888")
            best = best_duration(r.get("duration"))
            gap = r.get("gap_to_leader")
            out.append({
                "pos": pos,
                "code": code,
                "num": n,
                "name": (fd or {}).get("family") or (od.get("full_name") or "").split(" ")[-1].title(),
                "team": (fd or {}).get("team") or od.get("team_name") or "",
                "col": col,
                "time": fmt_lap(best),
                "gap": (None if not gap else ("+%.3f" % float(gap))) if not isinstance(gap, str) else gap,
                "laps": r.get("number_of_laps"),
                "seg": quali_segments(r.get("duration")),
                "out": bool(r.get("dnf") or r.get("dns") or r.get("dsq")),
                "rookie": fd is None,
            })
        out = [x for x in out if x["pos"] is not None]
        out.sort(key=lambda x: x["pos"])
        row["results"] = out
        if out: src[s["key"]] = "openf1"
        live["sessions"].append(row)

    json.dump(live, open(LIVEP, "w"), ensure_ascii=False, indent=1)
    filled = [s["key"] for s in live["sessions"] if s["results"]]
    print("wrote data/live.json - R%d %s | sessions: %s | with results: %s"
          % (rnd, live["gp"], ", ".join(s["key"] for s in live["sessions"]), ", ".join(filled) or "none"))
    if src:
        print("  sources: %s" % ", ".join("%s=%s" % kv for kv in src.items()))
    if kept:
        print("  NOTE: kept previously captured results for %s (fetch failed)" % ", ".join(kept))


if __name__ == "__main__":
    main()
