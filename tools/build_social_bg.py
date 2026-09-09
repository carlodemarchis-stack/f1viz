#!/usr/bin/env python3
"""Build the story-card backgrounds the AGWAS capture extension points `agwas:bg` at.

  python3 tools/build_social_bg.py            # dry run: what it would fetch/compose
  python3 tools/build_social_bg.py --apply

Two kinds, both written to img/social/ and served from our own domain (the receiver
fetches the URL server-side, so it has to be absolute, public and stable). Every round
gets a file, falling back to the generic image where f1.com has no card, so the page can
name the URL without having to know which rounds exist - and a bg that 404s would raise
in the receiver rather than degrade.

  r<N>.jpg       the race's own photo from formula1.com's schedule cards
  car-<tid>.jpg  the team's car, rotated upright on a 9:16 canvas

The car needs composing rather than linking because the cutouts are 900x198: the
receiver cover-crops to 9:16, so a bare strip lands on a meaningless sliver of
bodywork. Rotated and fitted onto a 1080x1920 ground, the whole car reads.
"""
import io, json, os, subprocess, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "img", "social")
YEAR = 2026
CDN = ("https://media.formula1.com/image/upload/c_lfill,w_1296/q_auto/"
       "v1740000001/fom-website/static-assets/%d/races/card/%s.webp")
STORY = (1080, 1920)

# f1.com's card slug is not always its page slug (Abu Dhabi's page is united-arab-emirates)
SLUG_FIX = {"UAE": "abu-dhabi", "USA": "united-states", "UK": "great-britain",
            "Great Britain": "great-britain", "Las Vegas": "las-vegas"}


def get(url):
    r = subprocess.run(["curl", "-s", "--max-time", "40", "-A", "Mozilla/5.0", url],
                       capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout[:4] not in (b"", b"<htm") else None


def slug_candidates(cal):
    # most specific first: three US rounds share country "USA", and taking that first gave
    # Miami and Las Vegas the generic united-states photo meant for Austin
    seen, out = set(), []
    for v in (cal.get("short"), cal.get("locality"), cal.get("gp"), cal.get("country")):
        for s in (SLUG_FIX.get(v), (v or "").lower().replace(" ", "-")):
            if s and s not in seen:
                seen.add(s); out.append(s)
    return out


def fetch_race(cal):
    """-> (slug, bytes) for the first candidate the CDN actually has"""
    for s in slug_candidates(cal):
        b = get(CDN % (YEAR, s))
        if b and len(b) > 5000:
            try:
                Image.open(io.BytesIO(b)).verify(); return s, b
            except Exception:
                pass
    return None, None


def car_canvas(src, colour):
    """Rotate the car upright and fit it on a 9:16 ground tinted with the team colour."""
    car = Image.open(src).convert("RGBA").rotate(90, expand=True, resample=Image.BICUBIC)
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    # a dark wash of the team colour, not the colour itself: the receiver fades it further
    canvas = Image.new("RGB", STORY, (int(r * .22) + 8, int(g * .22) + 9, int(b * .22) + 12))
    scale = min(STORY[0] * .78 / car.width, STORY[1] * .82 / car.height)
    car = car.resize((max(1, round(car.width * scale)), max(1, round(car.height * scale))), Image.LANCZOS)
    canvas.paste(car, ((STORY[0] - car.width) // 2, (STORY[1] - car.height) // 2), car)
    return canvas


def main():
    do = "--apply" in sys.argv
    f1 = json.load(open(os.path.join(ROOT, "data", "f1.json")))
    if do:
        os.makedirs(OUT, exist_ok=True)

    print("RACES — photo per round from formula1.com")
    ok = miss = 0
    used = {}
    for cal in f1["calendar"]:
        dst = os.path.join(OUT, "r%d.jpg" % cal["r"])
        slug, data = fetch_race(cal)
        if not data:
            print("   r%-3d %-16s no card on f1.com (tried %s) -> generic"
                  % (cal["r"], cal["locality"], ", ".join(slug_candidates(cal))))
            miss += 1
            if do:
                Image.open(os.path.join(ROOT, "img", "og.png")).convert("RGB").save(
                    dst, quality=88, optimize=True)
            continue
        ok += 1
        dupe = " <- ALSO r%d" % used[slug] if slug in used else ""
        used.setdefault(slug, cal["r"])
        print("   r%-3d %-16s %-22s %6.0f KB%s"
              % (cal["r"], cal["locality"], slug, len(data) / 1024, dupe))
        if do:                                   # one extension for all of them: the fallback
            Image.open(io.BytesIO(data)).convert("RGB").save(dst, quality=88, optimize=True)

    print("\nCARS — rotated upright on a 9:16 canvas")
    for c in f1["constructors"]:
        src = os.path.join(ROOT, "img", "cars", "%s.png" % c["teamId"])
        if not os.path.exists(src):
            print("   %-14s no car cutout" % c["teamId"]); continue
        print("   %-14s %s" % (c["teamId"], c["color"]))
        if do:
            car_canvas(src, c["color"]).save(os.path.join(OUT, "car-%s.jpg" % c["teamId"]),
                                             quality=88, optimize=True)   # flattened already, no alpha to keep

    print("\n%d races with a photo, %d without" % (ok, miss))
    if not do:
        print("dry run — pass --apply to write img/social/")


if __name__ == "__main__":
    main()
