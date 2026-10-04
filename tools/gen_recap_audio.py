#!/usr/bin/env python3
"""Generate one MP3 per GP race recap via ElevenLabs.
Reads text from data/recaps.json, writes audio/rNN.mp3.
Usage: ELEVENLABS_API_KEY=... python3 tools/gen_recap_audio.py [round ...]
(no round args = all rounds)
Translations: EL_LANG=fr (it, es, de, ja, zh) reads recaps[r]["tr"][lang] and writes audio/<lang>/rNN.mp3;
the multilingual model takes the language from the text.
"""
import os, sys, json, time, urllib.request, urllib.error

KEY = os.environ.get("ELEVENLABS_API_KEY")
if not KEY:
    sys.exit("Set ELEVENLABS_API_KEY in the environment.")

VOICE_ID = os.environ.get("EL_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")   # 'Daniel' — British news presenter
MODEL    = os.environ.get("EL_MODEL", "eleven_multilingual_v2")
STAB     = float(os.environ.get("EL_STABILITY", "0.30"))
STYLE    = float(os.environ.get("EL_STYLE", "0.45"))
LANG     = os.environ.get("EL_LANG", "")                           # "" = the English recap
OUT_DIR  = os.environ.get("EL_OUT", "audio/" + LANG if LANG else "audio")   # variant folder, e.g. audio-antoni
FMT      = "mp3_44100_128"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

recaps = json.load(open(os.path.join(ROOT, "data", "recaps.json")))["recaps"]
os.makedirs(os.path.join(ROOT, OUT_DIR), exist_ok=True)

wanted = sys.argv[1:] or sorted(recaps, key=lambda k: int(k))
for rnd in wanted:
    rnd = str(rnd)
    entry = recaps.get(rnd)
    if not entry:
        print(f"R{rnd}: no recap, skip"); continue
    text = (entry.get("tr") or {}).get(LANG) if LANG else entry["recap"]
    if not text:
        print(f"R{rnd}: no {LANG} translation, skip"); continue
    body = json.dumps({
        "text": text,
        "model_id": MODEL,
        # lower stability + higher style = more expressive / passionate delivery
        "voice_settings": {"stability": STAB, "similarity_boost": 0.75, "style": STYLE, "use_speaker_boost": True},
    }).encode()
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format={FMT}"
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    out = os.path.join(ROOT, OUT_DIR, f"r{int(rnd):02d}.mp3")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        open(out, "wb").write(data)
        print(f"R{rnd} {entry['short']:10} -> {os.path.relpath(out, ROOT)}  {len(data)//1024} KB")
    except urllib.error.HTTPError as e:
        print(f"R{rnd} FAILED: HTTP {e.code} {e.read()[:200]!r}")
    except Exception as e:
        print(f"R{rnd} FAILED: {e}")
    time.sleep(0.5)
print("done")
