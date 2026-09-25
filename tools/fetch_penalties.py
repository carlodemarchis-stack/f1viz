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
    (6, 73): "Doc 99: rescinded on Right of Review - Car 10 did not exceed 60 km/h; 5 s removed",
    (6, 75): "Doc 99: rescinded on Right of Review - Car 10 did not exceed 60 km/h; 5 s removed",
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


def why(x, bynum_code):
    t = re.sub(r"^(?:Corrected )?(?:Infringement|Decision)\s*-\s*Car \d+\s*-\s*", "", x["title"]).strip()
    tl = t.lower()
    if "pu element" in tl:
        return "PU change in parc fermé" if "parc f" in tl else "PU change"
    if "parc f" in tl:
        return "Parc fermé change"
    if "yellow" in tl:
        return "Yellow flag"
    m = re.search(r"imped\w*(?: of)? car (\d+)", tl)
    if m:
        return "Impeding " + (bynum_code.get(int(m.group(1))) or "car " + m.group(1))
    return t[:1].upper() + t[1:]


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


def refresh(rounds, verbose=False):
    """Fetch the given rounds, merge them into data/penalties.json, annotate grids. -> P"""
    P = json.load(open(OUT))["rounds"] if os.path.exists(OUT) else {}
    P.update(collect(rounds, verbose))
    json.dump({"source": "fia.com/documents", "rounds": P}, open(OUT, "w"), ensure_ascii=False, indent=1)
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
