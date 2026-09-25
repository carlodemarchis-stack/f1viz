#!/usr/bin/env python3
"""Every stewards' ruling of the 2026 season, straight from the FIA's official documents.

  python3 tools/fetch_penalties.py            # fetch + parse, print a summary per round
  python3 tools/fetch_penalties.py -v         # also print every ruling
  python3 tools/fetch_penalties.py --apply    # write data/penalties.json + grid reasons
  python3 tools/fetch_penalties.py --round 15 # refresh one round only (update_live.py does this)

Source: fia.com/documents (F1 2026 season). Each event lists its documents; the rulings are
titled "Infringement - Car N - ..." or "Decision - Car N - ..." (after a summons). Every one of
those PDFs has the same fields - No / Driver, Competitor, Time, Session, Fact, Infringement,
Decision, Reason - so the penalty is read from the Decision field, never inferred from a title.

"Deleted lap times" documents (track-limits lap deletions, a list per session) are not rulings
against one car and are skipped. PDFs are cached in ~/.cache/f1viz/fia (not the repo).
"""
import html, json, os, re, subprocess, sys

import fitz  # PyMuPDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F1P = os.path.join(ROOT, "data", "f1.json")
OUT = os.path.join(ROOT, "data", "penalties.json")
CACHE = os.path.expanduser("~/.cache/f1viz/fia")
FIA = "https://www.fia.com"
SEASON = FIA + "/documents/championships/fia-formula-one-world-championship-14/season/season-2026-2072"
DOC_RE = re.compile(r'<a href="([^"]+\.pdf)".*?<div class="title">(.*?)</div>.*?date-display-single">([^<]+)<', re.S)


def curl(url):
    return subprocess.run(["curl", "-sL", "--max-time", "60", "-A", "Mozilla/5.0", url],
                          capture_output=True, text=True).stdout


def docs_in(h):
    out = []
    for u, t, dt in DOC_RE.findall(h):
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(t))).strip()
        out.append({"url": u, "title": t, "pub": dt.strip()})
    return out


def events(want=None):
    """-> [(event name, [docs])] for every 2026 event: the open one inline, the rest via ajax.
    want(name) -> bool limits which events are fetched."""
    want = want or (lambda n: True)
    page = curl(SEASON)
    evs = []
    a = page.find('event-title active')
    if a >= 0:
        name = re.search(r'event-title active">([^<]+)<', page[a - 30:]).group(1).strip()
        b = page.find('decision-document-list/nojs/', a)
        if want(name):
            evs.append((name, docs_in(page[a:b if b > 0 else None])))
    for eid, name in re.findall(r'decision-document-list/nojs/(\d+)" class="event-title[^>]*>\s*([^<]+?)\s*</a>', page):
        if not want(name):
            continue
        raw = curl(FIA + "/decision-document-list/ajax/" + eid)
        h = "".join(c.get("data", "") for c in json.loads(raw) if c.get("command") == "insert")
        evs.append((name, docs_in(h)))
    return evs


def is_ruling(title):
    t = title.lower()
    if "deleted lap time" in t:
        return False
    return ("infringement" in t or re.search(r"\bdecision\b", t)) and "summons" not in t


def pdf_text(url):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, os.path.basename(url))
    if not os.path.exists(p) or os.path.getsize(p) < 1000:
        subprocess.run(["curl", "-sL", "--max-time", "60", "-A", "Mozilla/5.0", "-o", p, FIA + url])
    try:
        return "".join(pg.get_text() for pg in fitz.open(p)).replace("\xa0", " ")
    except Exception as e:
        print("   unreadable %s: %s" % (url, e)); return ""


# rulings later rescinded on a Right of Review: (round, doc) -> the document that overturned it
OVERTURNED = {
    (6, 73): "Rescinded on review (FIA doc 99): did not exceed 60 km/h, 5 s removed",
    (6, 75): "Rescinded on review (FIA doc 99): did not exceed 60 km/h, 5 s removed",
}

FIELDS = ["No / Driver", "Competitor", "Time", "Session", "Fact", "Infringement", "Decision", "Reason"]


def fields(txt):
    """Split the ruling body into its labelled fields (each label starts a line)."""
    body = txt[txt.find("No / Driver"):] if "No / Driver" in txt else txt[txt.find("Competitor"):]
    # a label starts its line and is not followed by ":" - "Competitor: Warning." inside the
    # Decision is the penalty's addressee, not the Competitor field
    pat = re.compile(r"^(%s)\b(?![ \t]*:)" % "|".join(re.escape(f) for f in FIELDS), re.M)
    marks = [(m.start(), m.end(), m.group(1)) for m in pat.finditer(body)]
    out = {}
    for i, (s, e, k) in enumerate(marks):
        if k in out:
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        out[k] = re.sub(r"\s+", " ", body[e:end]).strip()
    if "Reason" in out:                      # the appeal boilerplate follows the reason
        out["Reason"] = re.split(r"Competitors are reminded", out["Reason"])[0].strip()
    doc = re.search(r"^Document\s*\n\s*(\d+)", txt, re.M)
    out["doc"] = int(doc.group(1)) if doc else None
    return out


def kinds(dec):
    """Machine tags for the Decision text. Order: most severe first."""
    d = dec.lower()
    k, v = [], {}
    if re.search(r"\bno further action\b|\bno penalty (?:is )?applied", d): k.append("nfa")
    if "disqualif" in d: k.append("dsq")
    m = re.search(r"(\d+)\s*(?:-| )?second(?:s)? time penalty", d)
    if m: k.append("time"); v["sec"] = int(m.group(1))
    if "drive through" in d or "drive-through" in d: k.append("drive")
    if re.search(r"stop[ -/]?(?:and[ -])?go", d): k.append("stopgo")
    m = re.search(r"(\d+) penalty lap", d)
    if m: k.append("lap"); v["laps"] = int(m.group(1))
    m = re.search(r"drop of (\d+) grid position", d) or re.search(r"(\d+)[ -]place grid (?:penalty|drop)", d)
    if m: k.append("grid"); v["grid"] = int(m.group(1))
    if "back of the grid" in d or "rear of the grid" in d: k.append("back")
    if "pit lane" in d and "start" in d: k.append("pitlane")
    if "reprimand" in d: k.append("reprimand")
    if "warning" in d and "reprimand" not in d: k.append("warning")
    m = re.search(r"fined\s*(?:€|eur(?:o|os)?)\s*([\d][\d,.]*\d|\d)", d) or re.search(r"fine of\s*(?:€|eur(?:o|os)?)\s*([\d][\d,.]*\d|\d)", d)
    if m or re.search(r"\bfined?\b", d):
        k.append("fine")
        if m: v["eur"] = int(re.sub(r"\D", "", m.group(1)))
        s = re.search(r"(?:€\s*([\d][\d,.]*\d) of which is suspended)|(?:fine is suspended)|(?:suspended for a period)", d)
        if s: v["susp"] = int(re.sub(r"\D", "", s.group(1))) if s.group(1) else v.get("eur")
    m = re.search(r"(\d+)\s+penalty points?", d)
    if m: v["pp"] = int(m.group(1))
    m = re.search(r"total of (\d+)", d)
    if m: v["ppTotal"] = int(m.group(1))
    if re.search(r"lap time[s]? .*deleted|deletion of .*lap time", d): k.append("laptime")
    if "imposed after the" in d: v["post"] = True
    if k != ["nfa"] and "nfa" in k: k.remove("nfa")
    return k, v


def cal_round(name, cal):
    return next((c for c in cal if name.lower().startswith(c["gp"].lower())), None)


def collect(rounds=None, verbose=False):
    """-> {"<round>": [rulings]} for the given rounds (all when None)."""
    f1 = json.load(open(F1P))
    cal = f1["calendar"]
    bynum = {int(d["num"]): d for d in f1["drivers"]}
    want = None
    if rounds:
        want = lambda n: (cal_round(n, cal) or {}).get("r") in rounds
    result, missing = {}, []
    for name, docs in events(want):
        c = cal_round(name, cal)
        if not c:
            print("?? no calendar round for FIA event %r - skipped" % name); continue
        rnd = c["r"]
        rows, seen = [], set()
        for d in docs:
            if not is_ruling(d["title"]):
                continue
            txt = pdf_text(d["url"])
            f = fields(txt)
            if "Decision" not in f:
                missing.append((rnd, d["title"])); continue
            car = re.match(r"(\d+)\s*-\s*(.+)", f.get("No / Driver", ""))
            num = int(car.group(1)) if car else None
            if num is None:                    # a few rulings carry the car only in the title
                t = re.search(r"\bCar (\d+)\b", d["title"])
                num = int(t.group(1)) if t else None
            if num is None and re.search(r"SC2\s*-\s*SC1", d["title"]):
                continue                       # field-wide sweep of the SC2-SC1 check, no single car
            k, v = kinds(f["Decision"])
            if num is None and not k:
                continue                       # procedural (e.g. a Right of Review's admissibility step)
            key = (num, f.get("Session"), f.get("Fact"), f["Decision"])
            if key in seen:                    # the same ruling re-issued (file_0.pdf, "Corrected ...")
                continue
            seen.add(key)
            rows.append(dict({
                "doc": f["doc"], "title": re.sub(r"^(?:DOC|Doc) \d+ - ", "", d["title"]), "pub": d["pub"],
                "num": num, "code": (bynum.get(num) or {}).get("code"),
                "driver": car.group(2).strip() if car else None,
                "team": f.get("Competitor"), "session": f.get("Session"),
                "fact": f.get("Fact"), "breach": f.get("Infringement"),
                "decision": f["Decision"], "kinds": k,
                "url": FIA + d["url"],
            }, **v))
            ov = OVERTURNED.get((rnd, f["doc"]))
            if ov:
                rows[-1]["overturned"] = ov
        rows.sort(key=lambda r: (r["doc"] or 0))
        result[str(rnd)] = rows
        pen = [r for r in rows if r["kinds"] and r["kinds"] != ["nfa"]]
        print("r%-3d %-26s %3d docs  %3d rulings  %3d penalised/reprimanded  %3d NFA"
              % (rnd, name, len(docs), len(rows), len(pen), sum(1 for r in rows if "nfa" in r["kinds"])))
        if verbose:
            for r in rows:
                print("      #%-4s %-4s %-14s %-40s -> %s" % (r["doc"], r["code"] or r["num"] or "team",
                      (r["session"] or "")[:14], (r["fact"] or "")[:40], r["decision"][:110]))
    if missing and verbose:
        print("\n%d ruling-titled docs without a Decision field (referrals, 107%%, permission to start):" % len(missing))
        for rnd, t in missing: print("   r%d  %s" % (rnd, t))
    return result


# ---- starting-grid reasons ---------------------------------------------------------------
GRID_KINDS = {"grid", "pitlane", "back"}


def for_sprint(x):
    """Grid rulings name their target: "start the Sprint from the pit lane", or "next Sprint/Race"
    handed out before the Sprint. Everything else applies to the Grand Prix."""
    d = x["decision"].lower()
    return "the sprint" in d or ("sprint/race" in d and (x["session"] or "").startswith("Sprint"))


ACRONYMS = ["PU", "SC", "VSC", "RD", "CDS", "SCL", "SC2", "SC1", "SCL1", "SCL2", "ECU", "DRS", "FIA", "ISC"]


def why(x, bynum_code):
    """Short, sentence-case reason from the document title; car numbers become driver codes."""
    t = re.sub(r"^(?:Corrected )?(?:Infringement|Decision)\s*-\s*(?:Car \d+\s*-\s*)?", "", x["title"]).strip()
    tl = t.lower()
    if "pu element" in tl:
        return "PU change in parc fermé" if "parc f" in tl else "PU change"
    if "parc f" in tl:
        return "Parc fermé change"
    if "yellow" in tl:
        return "Yellow flag"
    if "pit lane speeding" in tl or "pitlane speeding" in tl:
        m = re.search(r"([\d.]+)\s*km/h", x.get("fact") or "")
        return "Pit-lane speeding" + (" · %s km/h" % m.group(1) if m else "")
    t = t[:1].upper() + t[1:].lower()
    t = re.sub(r"\bcars? (\d+)\b", lambda m: bynum_code.get(int(m.group(1))) or ("car " + m.group(1)), t)
    t = re.sub(r"\bimped\w*(?: of)? ([A-Z]{3})", lambda m: ("I" if m.group(0)[0] == "I" else "i") + "mpeding " + m.group(1), t, flags=re.I)
    for a in ACRONYMS:
        t = re.sub(r"\b%s\b" % a.lower(), a, t)
    return t.replace("race director's", "Race Director's").replace("race directors", "Race Director's")


def grid_reasons(rnd, P):
    """-> {code: [{"k", "n", "why", "ses", "url"}]} for the Grand Prix starting grid of round rnd."""
    f1 = json.load(open(F1P))
    bynum_code = {int(d["num"]): d["code"] for d in f1["drivers"]}
    out = {}
    for x in P.get(str(rnd), []):
        if x.get("overturned") or not x.get("code") or not (set(x["kinds"]) & GRID_KINDS) or for_sprint(x):
            continue
        k = "pitlane" if "pitlane" in x["kinds"] else ("grid" if "grid" in x["kinds"] else "back")
        out.setdefault(x["code"], []).append({"k": k, "n": x.get("grid"), "why": why(x, bynum_code),
                                              "ses": x["session"], "url": x["url"]})
    return out


def annotate(grid, reasons):
    """Attach reasons to starting-grid rows in place; -> number of rows annotated."""
    n = 0
    for g in grid or []:
        g.pop("pen", None)
        if reasons.get(g.get("code")):
            g["pen"] = reasons[g["code"]]; n += 1
    return n


def annotate_files(P, rounds=None):
    """Write the reasons into every archived weekend grid and the live grid."""
    import glob
    paths = sorted(glob.glob(os.path.join(ROOT, "data", "weekend", "r*.json"))) + [os.path.join(ROOT, "data", "live.json")]
    for path in paths:
        if not os.path.exists(path):
            continue
        w = json.load(open(path))
        rnd = w.get("round")
        if not w.get("grid") or (rounds and rnd not in rounds):
            continue
        n = annotate(w["grid"], grid_reasons(rnd, P))
        json.dump(w, open(path, "w"), ensure_ascii=False, indent=1)
        print("   grid reasons r%-2s %2d driver(s)  %s" % (rnd, n, os.path.relpath(path, ROOT)))


# ---- slim copy embedded in the page (build.py -> /*__STEWARDS__*/) ------------------------
SLIM = os.path.join(ROOT, "data", "stewards.json")
SES_ORDER = ["Thursday Press Conference", "Free Practice 1", "Practice 1", "Free Practice 2", "Practice 2",
             "Free Practice 3", "Sprint Qualifying", "Sprint", "Qualifying", "Reconnaissance Laps",
             "Grid Procedure", "Race"]
SES_AB = {"Free Practice 1": "FP1", "Practice 1": "FP1", "Free Practice 2": "FP2", "Practice 2": "FP2",
          "Free Practice 3": "FP3", "Sprint Qualifying": "Sprint Quali", "Qualifying": "Qualifying",
          "Reconnaissance Laps": "Recon laps", "Grid Procedure": "Grid", "Thursday Press Conference": "Media day"}


def tag(x):
    """-> (category, label) for the most severe sanction in a ruling."""
    k, v = x["kinds"], x
    if "dsq" in k: return "dsq", "Disqualified"
    if "drive" in k: return "race", "Drive-through"
    if "stopgo" in k:
        m = re.search(r"(\d+)\s*second stop", x["decision"].lower())
        return "race", ("%ds stop-go" % int(m.group(1))) if m else "Stop-go"
    if "lap" in k: return "race", "+%d lap" % v.get("laps", 1)
    if "time" in k: return "race", "+%ds" % v.get("sec", 0)
    if "pitlane" in k: return "grid", "Pit-lane start"
    if "grid" in k: return "grid", "−%d grid" % v.get("grid", 0)
    if "back" in k: return "grid", "Back of grid"
    if "laptime" in k: return "race", "Lap time deleted"
    if "reprimand" in k: return "rep", "Reprimand"
    if "fine" in k:
        eur = v.get("eur")
        return "fine", ("€{:,}".format(eur) if eur else "Fine")
    if "warning" in k: return "warn", "Warning"
    if "nfa" in k: return "nfa", "No action"
    return "nfa", "Noted"


def note(x):
    d, bits = x["decision"], []
    if "fine" in x["kinds"] and not tag(x)[0] == "fine" and x.get("eur"):
        bits.append("team fined €{:,}".format(x["eur"]))
    if x.get("susp"):
        bits.append("€{:,} suspended".format(x["susp"]))
    m = re.search(r"(\d+(?:st|nd|rd|th)) reprimand of the season", d)
    if m: bits.append(m.group(1) + " reprimand of the season")
    if x.get("pp"):
        bits.append("%d penalty point%s%s" % (x["pp"], "" if x["pp"] == 1 else "s",
                                              " (%d in 12 months)" % x["ppTotal"] if x.get("ppTotal") else ""))
    if x.get("post"): bits.append("applied after the session")
    if "subject to" in d.lower() and "classif" in d.lower(): bits.append("if classified")
    return " · ".join(bits)


def slim(P):
    f1 = json.load(open(F1P))
    bynum_code = {int(d["num"]): d["code"] for d in f1["drivers"]}
    out = {}
    for rnd, rows in P.items():
        lst = []
        for x in rows:
            cat, lbl = tag(x)
            who = x.get("code") or ((x.get("driver") or "").split()[-1].upper()[:3] if x.get("driver") else None) \
                or ("#%d" % x["num"] if x.get("num") else "Team")
            ses = x.get("session") or ""
            e = {"c": who, "s": SES_AB.get(ses, ses or "Other"), "o": SES_ORDER.index(ses) if ses in SES_ORDER else 99,
                 "k": cat, "t": lbl, "w": why(x, bynum_code), "u": x["url"].rsplit("/", 1)[-1]}
            if not x.get("code") and x.get("driver"): e["n"] = x["driver"]
            nt = note(x)
            if nt: e["x"] = nt
            if x.get("overturned"): e["v"] = x["overturned"]
            lst.append(e)
        sev = ["dsq", "race", "grid", "rep", "fine", "warn", "nfa"]
        lst.sort(key=lambda e: (e["o"], sev.index(e["k"]), e["c"]))
        out[rnd] = lst
    json.dump(out, open(SLIM, "w"), ensure_ascii=False, separators=(",", ":"))
    return out


def refresh(rounds, verbose=False):
    """Fetch the given rounds, merge them into data/penalties.json, annotate grids. -> P"""
    P = json.load(open(OUT))["rounds"] if os.path.exists(OUT) else {}
    P.update(collect(rounds, verbose))
    json.dump({"source": "fia.com/documents", "rounds": P}, open(OUT, "w"), ensure_ascii=False, indent=1)
    slim(P)
    annotate_files(P, rounds)
    return P


def main():
    a = sys.argv[1:]
    verbose = "-v" in a
    if "--round" in a:
        refresh([int(a[a.index("--round") + 1])], verbose)
    elif "--apply" in a:
        refresh(None, verbose)
    else:
        collect(None, verbose)
        print("\ndry run - pass --apply (all rounds) or --round N to write data/penalties.json")


if __name__ == "__main__":
    main()
