"""Text-to-speech via ElevenLabs. One script in, one MP3 per voice out, plus per-character timings.

  python3 voice/tts.py scripts/batch-01.json 01 --voices FGY2WhTYpPnrIDTdsKH5,cgSgspJ2msm6clMCkdW9,9BWtsMINqrJLrRacOk9x --out out/voices/

Three voices by default for the sampling round. Picked voice IDs go in a VOICE env var later.
Spend guards: a matching (voice_id, text) file already present is skipped; MAX_TTS caps new calls per run.
"""
import argparse, base64, hashlib, json, os, sys, time, urllib.error, urllib.request

BASE = "https://api.elevenlabs.io/v1"
MODEL = os.environ.get("ELEVEN_MODEL", "eleven_turbo_v2_5")
MAX_TTS = int(os.environ.get("MAX_TTS", "10"))
LOG = None

VOICES = {
    "FGY2WhTYpPnrIDTdsKH5": "laura-upbeat",
    "cgSgspJ2msm6clMCkdW9": "jessica-expressive",
    "9BWtsMINqrJLrRacOk9x": "aria-expressive",
    "21m00Tcm4TlvDq8ikWAM": "rachel-calm",
    "EXAVITQu4vr4xnSDxMaL": "bella-soft",
}


def log(m):
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, file=sys.stderr)
    if LOG: LOG.write(line + "\n"); LOG.flush()


def call(path, body, voice_id):
    key = os.environ["ELEVENLABS_API_KEY"].strip()
    req = urllib.request.Request(
        BASE + path,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try: return e.code, json.loads(raw)
        except Exception: return e.code, {"raw": raw[:400]}


def char_to_word_times(chars, starts, ends):
    """Collapse per-character timings into [(word, start, end)]."""
    words, cur, cur_start = [], "", None
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((cur, cur_start, prev_end)); cur, cur_start = "", None
            continue
        if not cur: cur_start = a
        cur += ch; prev_end = b
    if cur: words.append((cur, cur_start, prev_end))
    return words


def make(script, voice_id, out_dir):
    label = VOICES.get(voice_id, voice_id[:8])
    tag = hashlib.sha1((voice_id + MODEL + script["vo"]).encode()).hexdigest()[:8]
    base = os.path.join(out_dir, script["id"], f"{label}-{tag}")
    mp3, tim = base + ".mp3", base + ".json"
    if os.path.exists(mp3) and os.path.exists(tim):
        log(f"{script['id']} / {label}: SKIP — already have {mp3}")
        return "have"
    if make.spent >= MAX_TTS:
        log(f"{script['id']} / {label}: NOT STARTED — MAX_TTS={MAX_TTS} reached this run"); return "capped"
    code, resp = call(
        f"/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128",
        {"text": script["vo"], "model_id": MODEL,
         "voice_settings": {"stability": 0.45, "similarity_boost": 0.75, "style": 0.35, "use_speaker_boost": True}},
        voice_id,
    )
    if code != 200 or "audio_base64" not in resp:
        log(f"{script['id']} / {label}: FAILED http={code} body={json.dumps(resp)[:400]}"); return "failed"
    make.spent += 1
    os.makedirs(os.path.dirname(mp3), exist_ok=True)
    open(mp3, "wb").write(base64.b64decode(resp["audio_base64"]))
    alt = resp.get("alignment") or resp.get("normalized_alignment") or {}
    words = char_to_word_times(alt.get("characters", []), alt.get("character_start_times_seconds", []), alt.get("character_end_times_seconds", []))
    json.dump({"voice_id": voice_id, "voice_label": label, "model": MODEL, "text": script["vo"],
               "duration": max([w[2] for w in words], default=0.0), "words": [[w, round(a, 3), round(b, 3)] for w, a, b in words]},
              open(tim, "w"), indent=1)
    log(f"{script['id']} / {label}: saved {os.path.getsize(mp3) // 1024}KB, {len(words)} words, {max([w[2] for w in words], default=0):.2f}s")
    return "new"


make.spent = 0


def main():
    global LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("queue"); ap.add_argument("key")
    ap.add_argument("--voices", default="FGY2WhTYpPnrIDTdsKH5,cgSgspJ2msm6clMCkdW9,9BWtsMINqrJLrRacOk9x")
    ap.add_argument("--out", default="out/voices")
    a = ap.parse_args()
    q = json.load(open(a.queue))
    hits = [s for s in q if s["id"] == a.key or s["id"].split("-")[0] == a.key.zfill(2)]
    if len(hits) != 1: sys.exit(f"[tts] key '{a.key}' matched {len(hits)} scripts in queue")
    s = hits[0]
    os.makedirs(os.path.join(a.out, "_log"), exist_ok=True)
    LOG = open(os.path.join(a.out, "_log", f"{s['id']}.log"), "a")
    log(f"=== {s['id']}: {len(a.voices.split(','))} voices, model={MODEL}, len(vo)={len(s['vo'].split())}w")
    if not os.environ.get("ELEVENLABS_API_KEY", "").strip():
        log("NO KEY — set ELEVENLABS_API_KEY as a repo secret."); sys.exit(2)
    results = {vid: make(s, vid, a.out) for vid in a.voices.split(",")}
    log(f"=== result: {json.dumps(results)}  new TTS calls this run: {make.spent}")
    sys.exit(0 if all(v in ("new", "have") for v in results.values()) else 1)


if __name__ == "__main__":
    main()
