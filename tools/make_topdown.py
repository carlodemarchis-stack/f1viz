#!/usr/bin/env python3
"""Turn a studio top-down car photo into a transparent, nose-right PNG for the grid view.

  python3 tools/make_topdown.py ~/Downloads/mercedes.png mercedes
  python3 tools/make_topdown.py ~/Downloads/foo.png haas --rotate -90   # nose was pointing UP

Writes img/cars-top/<teamId>.png (used by the qualifying grid; teams without a file
fall back to the drawn SVG car).

The backdrop is removed by flood-filling inward from the border rather than by keying a
colour globally, so livery that happens to match the backdrop survives -- Mercedes' Petronas
teal on a cyan backdrop, Red Bull's navy on a blue one. Only background actually connected
to the edge is cleared.
"""
import os, sys
from collections import deque
from PIL import Image
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "img", "cars-top")
WIDTH = 900          # output length along the track
TOL = 62             # colour distance from the sampled backdrop that still counts as backdrop
FEATHER = 34         # width of the soft alpha ramp just outside the car


def build(src, team, rotate=90, width=WIDTH, tol=TOL):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(np.float32)
    h, w, _ = a.shape

    # backdrop colour = median of the four corners (studio shots are uniform)
    bg = np.median(np.array([a[3, 3], a[3, w - 4], a[h - 4, 3], a[h - 4, w - 4]]), axis=0)
    dist = np.sqrt(((a - bg) ** 2).sum(axis=2))
    near = dist < tol

    # keep only the backdrop reachable from the border, so enclosed livery is never punched out
    seen = np.zeros((h, w), bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if near[y, x] and not seen[y, x]:
                seen[y, x] = True; q.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if near[y, x] and not seen[y, x]:
                seen[y, x] = True; q.append((y, x))
    while q:
        y, x = q.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and near[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True; q.append((ny, nx))

    alpha = np.where(seen, 0.0, 255.0)
    # feather the anti-aliased rim: pixels just beyond the cut that still lean towards the backdrop
    rim = (~seen) & (dist < tol + FEATHER)
    alpha[rim] = np.clip((dist[rim] - tol) * 255.0 / FEATHER, 0, 255)

    # decontaminate the rim: an edge pixel is obs = af*car + (1-af)*backdrop, so solving for the
    # car colour strips the backdrop tint that otherwise shows as a coloured halo round the car.
    rgb = a.copy()
    af = (alpha / 255.0)[:, :, None]
    mix = (alpha > 0) & (alpha < 255)
    if mix.any():
        solved = (a - (1.0 - af) * bg) / np.maximum(af, 0.08)
        rgb[mix] = np.clip(solved[mix], 0, 255)

    out = Image.fromarray(np.dstack([rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2], alpha]).astype(np.uint8))
    box = out.getbbox()
    if not box:
        raise SystemExit("nothing left after keying - try a different --tol")
    out = out.crop(box)
    if rotate:
        out = out.rotate(rotate, expand=True, resample=Image.BICUBIC)   # positive = counter-clockwise
    out = out.resize((width, max(1, round(out.size[1] * width / out.size[0]))), Image.LANCZOS)
    os.makedirs(OUTDIR, exist_ok=True)
    dst = os.path.join(OUTDIR, team + ".png")
    out.save(dst)
    cleared = 100.0 * seen.mean()
    print("%s -> %s  %dx%d  (backdrop rgb %s, %.1f%% cleared)"
          % (os.path.basename(src), os.path.relpath(dst, ROOT), out.size[0], out.size[1],
             tuple(int(v) for v in bg), cleared))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    args = sys.argv[1:]
    rot = 90
    if "--rotate" in args:
        i = args.index("--rotate"); rot = int(args[i + 1]); del args[i:i + 2]
    tol = TOL
    if "--tol" in args:
        i = args.index("--tol"); tol = int(args[i + 1]); del args[i:i + 2]
    build(os.path.expanduser(args[0]), args[1], rotate=rot, tol=tol)
