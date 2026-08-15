#!/usr/bin/env python3
"""Auto-transcribe team-radio clips into data/radio.json ('cap' per clip) with faster-whisper.
Resumable: skips clips that already have a 'cap'. Usage: python3 tools/gen_radio_captions.py [model]
"""
import json, os, sys
from faster_whisper import WhisperModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RJ = os.path.join(ROOT, "data", "radio.json")
model_name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WMODEL", "small.en")
print("loading model:", model_name, flush=True)
m = WhisperModel(model_name, device="cpu", compute_type="int8")

d = json.load(open(RJ))
clips = [c for r in d["radio"].values() for v in r.values() for c in v]
todo = [c for c in clips if c.get("cap") is None]
print(f"{len(clips)} clips, {len(todo)} to transcribe", flush=True)

def save():
    json.dump(d, open(RJ, "w"), ensure_ascii=False, separators=(",", ":"))

done = 0
for c in clips:
    if c.get("cap") is not None:
        continue
    p = os.path.join(ROOT, "audio-radio", c["f"])
    if not os.path.exists(p):
        c["cap"] = ""; continue
    try:
        segs, _ = m.transcribe(p, language="en", beam_size=1, vad_filter=True)
        txt = " ".join(s.text.strip() for s in segs).strip()
    except Exception as e:
        txt = ""
    c["cap"] = txt
    done += 1
    if done % 10 == 0:
        save()
        print(f"{done}/{len(todo)}  {c['f'][:14]} -> {txt[:60]!r}", flush=True)
save()
print("DONE", done, "transcribed", flush=True)
