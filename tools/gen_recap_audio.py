#!/usr/bin/env python3
"""Generate one MP3 per GP race recap via ElevenLabs.
Reads text from data/recaps.json, writes audio/rNN.mp3.
Usage: ELEVENLABS_API_KEY=... python3 tools/gen_recap_audio.py [round ...]
(no round args = all rounds)
"""
import os, sys, json, time, urllib.request, urllib.error

KEY = os.environ.get("ELEVENLABS_API_KEY")
if not KEY:
    sys.exit("Set ELEVENLABS_API_KEY in the environment.")

VOICE_ID = os.environ.get("EL_VOICE_ID", "onwK4e9ZLuTAKqWW03F9")   # 'Daniel' — British news presenter
MODEL    = os.environ.get("EL_MODEL", "eleven_multilingual_v2")
FMT      = "mp3_44100_128"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

recaps = json.load(open(os.path.join(ROOT, "data", "recaps.json")))["recaps"]
os.makedirs(os.path.join(ROOT, "audio"), exist_ok=True)

wanted = sys.argv[1:] or sorted(recaps, key=lambda k: int(k))
for rnd in wanted:
    rnd = str(rnd)
    entry = recaps.get(rnd)
    if not entry:
        print(f"R{rnd}: no recap, skip"); continue
    body = json.dumps({
        "text": entry["recap"],
        "model_id": MODEL,
        # lower stability + higher style = more expressive / passionate delivery
        "voice_settings": {"stability": 0.30, "similarity_boost": 0.75, "style": 0.45, "use_speaker_boost": True},
    }).encode()
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format={FMT}"
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"xi-api-key": KEY, "Content-Type": "application/json"})
    out = os.path.join(ROOT, "audio", f"r{int(rnd):02d}.mp3")
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
