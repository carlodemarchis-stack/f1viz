#!/usr/bin/env python3
"""Auto-transcribe team-radio clips into data/radio.json ('cap' per clip) with faster-whisper.
Resumable: skips clips that already have a 'cap'.

  python3 tools/gen_radio_captions.py [model]              # fill in whatever is missing
  FORCE=1 python3 tools/gen_radio_captions.py large-v3 --round 13   # redo one round properly

--round limits the work to a single round, which is what makes FORCE usable: re-running a
better model over one race costs minutes, over the whole season it costs hours.
"""
import json, os, sys
from faster_whisper import WhisperModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RJ = os.path.join(ROOT, "data", "radio.json")
args = sys.argv[1:]
ROUND = None
if "--round" in args:
    i = args.index("--round"); ROUND = args[i + 1]; del args[i:i + 2]
model_name = args[0] if args else os.environ.get("WMODEL", "large-v3")
FORCE = os.environ.get("FORCE") == "1"
# domain prompt biases the decoder toward F1 names + jargon (big accuracy win on noisy radio)
PROMPT = ("Formula 1 team radio between a driver and their race engineer. English is usual "
          "but Italian, Spanish and French are heard too. "
          "Drivers: Verstappen, Hamilton, Leclerc, Russell, Norris, Piastri, Antonelli, "
          "Sainz, Alonso, Gasly, Ocon, Albon, Hulkenberg, Stroll, Bottas, Perez, Hadjar, "
          "Lawson, Bearman, Colapinto, Bortoleto, Lindblad. "
          "Words: box, pit, DRS, undercut, overcut, safety car, VSC, front, behind, "
          "position, gap, tyres, push, flat out, sector, degradation, engineer, copy. "
          "Forza Kimi. Grazie ragazzi. Vamos. Allez.")
# Whisper fills near-silence with the filler it learnt from subtitled video; a radio clip
# never actually says these, so an empty caption is the honest answer.
HALLUCINATIONS = ("thank you for watching", "thanks for watching", "grazie a tutti",
                  "we'll be right back", "please subscribe", "subscribe to",
                  "sottotitoli", "amara.org", "www.")
print("loading model:", model_name, "force=", FORCE, "round=", ROUND or "all", flush=True)
m = WhisperModel(model_name, device="cpu", compute_type="int8")

d = json.load(open(RJ))
clips = [c for rk, r in d["radio"].items() if ROUND in (None, rk)
         for v in r.values() for c in v]
todo = [c for c in clips if FORCE or c.get("cap") is None]
print(f"{len(clips)} clips, {len(todo)} to transcribe", flush=True)

def save():
    json.dump(d, open(RJ, "w"), ensure_ascii=False, separators=(",", ":"))

done = 0
for c in clips:
    if not FORCE and c.get("cap") is not None:
        continue
    p = os.path.join(ROOT, "audio-radio", c["f"])
    if not os.path.exists(p):
        c["cap"] = ""; continue
    try:
        # language=None (auto): pinning it to English turned Antonelli's "Forza Kimi" into
        # "4 is a kibbe" and lost every Italian/Spanish exchange
        segs, _ = m.transcribe(p, beam_size=5, temperature=0,
                               condition_on_previous_text=False, vad_filter=True,
                               initial_prompt=PROMPT)
        txt = " ".join(s.text.strip() for s in segs).strip()
        if any(h in txt.lower() for h in HALLUCINATIONS):
            txt = ""
    except Exception as e:
        txt = ""
    c["cap"] = txt
    done += 1
    if done % 10 == 0:
        save()
        print(f"{done}/{len(todo)}  {c['f'][:14]} -> {txt[:60]!r}", flush=True)
save()
print("DONE", done, "transcribed", flush=True)
