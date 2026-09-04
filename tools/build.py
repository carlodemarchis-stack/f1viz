#!/usr/bin/env python3
"""Build index.html from template.html + the data files (single source of truth for the inline step)."""
import os, json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def rd(p, default="null"):
    fp = os.path.join(ROOT, p)
    if not os.path.exists(fp): return default
    s = open(fp).read().strip()
    return s or default
tpl = open(os.path.join(ROOT, "template.html")).read()
out = (tpl.replace("/*__DATA__*/",   rd("data/f1.json"))
          .replace("/*__RECAPS__*/", rd("data/recaps.json"))
          .replace("/*__RADIO__*/",  rd("data/radio.json"))
          .replace("/*__LIVE__*/",   rd("data/live.json")))
open(os.path.join(ROOT, "index.html"), "w").write(out)
print("built index.html", len(out), "bytes")
