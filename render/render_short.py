"""Factory renderer — one script from the guarded queue in, one vertical Short out.
Question bubble on top, word-chunk captions timed to the voice-over, highlight words in glow colour.

  python3 render/render_short.py scripts/batch-01.json 01 out.mp4 [--voice vo.mp3] [--timings words.json]

--voice    narration audio; mixed over the ambient bed. Without it the Short renders with the bed only.
--timings  JSON list of [word, start_sec, end_sec] from the TTS step. Without it, captions are paced
           at SPEECH_WPS words per second, which is an estimate and will drift against a real voice.
Background is the generated bio-bay water from render_sample until real clips are wired in.
"""
import argparse, json, os, re, subprocess, sys, tempfile
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_sample as R

W, H, FPS = R.W, R.H, R.FPS
SPEECH_WPS = 2.9      # estimated speaking rate; replaced by --timings when a voice exists
LEAD, TAIL = 0.5, 0.9  # silence before the first word / hold after the last
CHUNK_CHARS = 16       # max characters per caption line group


def pick(queue, key):
    hits = [s for s in queue if s["id"] == key or s["id"].split("-")[0] == key.zfill(2)]
    if len(hits) != 1:
        sys.exit(f"[render] '{key}' matched {len(hits)} scripts in the queue (need exactly 1)")
    return hits[0]


def word_times(vo, timings):
    words = vo.split()
    if timings:
        if len(timings) != len(words):
            sys.exit(f"[render] timings has {len(timings)} words, VO has {len(words)} — refusing to guess")
        return [(w, float(a) + LEAD, float(b) + LEAD) for (w, (_, a, b)) in zip(words, timings)]
    out, t = [], LEAD
    for w in words:
        d = 0.5 + 0.08 * len(w)  # longer words take longer; total is normalised below
        pause = 0.28 if w[-1] in ".?!" else 0.12 if w[-1] in ",—-" else 0.0
        out.append((w, t, t + d)); t += d + pause
    scale = (len(words) / SPEECH_WPS) / (t - LEAD)  # normalise the total to the estimated rate
    return [(w, LEAD + (a - LEAD) * scale, LEAD + (b - LEAD) * scale) for w, a, b in out]


def chunks(wt):
    """Group words into caption cards: break on sentence ends, dashes, or when the card is full."""
    cards, cur = [], []
    for w, a, b in wt:
        cur.append((w, a, b))
        text = " ".join(x[0] for x in cur)
        if w[-1] in ".?!" or w in ("—", "-") or len(text) >= CHUNK_CHARS:
            cards.append(cur); cur = []
    if cur:
        cards.append(cur)
    out = []
    for i, c in enumerate(cards):
        words = [x[0] for x in c if x[0] not in ("—", "-")]
        if not words:
            continue
        end = cards[i + 1][0][1] if i + 1 < len(cards) else c[-1][2] + TAIL
        out.append((c[0][1], end, " ".join(words)))
    return out


def is_hi(word, highlights):
    w = re.sub(r"[^a-z0-9']", "", word.lower())
    return any(w and w in [re.sub(r"[^a-z0-9']", "", h) for h in hl.lower().split()] for hl in highlights)


def wrap(text, n):
    lines, cur = [], ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > n:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + ([cur] if cur else [])


def draw_bubble(d, t, question):
    al = int(255 * R.ease(t / 0.3))
    lines = wrap(question, 26)
    x0, y0 = 90, 330
    y1 = y0 + 124 + 62 * len(lines) + 30
    d.rounded_rectangle((x0, y0, W - 90, y1), 36, fill=(255, 255, 255, al))
    d.ellipse((x0 + 34, y0 + 34, x0 + 94, y0 + 94), fill=(120, 150, 160, al))
    d.text((x0 + 116, y0 + 44), "Reply to a viewer", font=R.font(R.FM, 34), fill=(110, 110, 120, al))
    d.multiline_text((x0 + 40, y0 + 112), "\n".join(lines), font=R.font(R.FB, 50), fill=(20, 20, 25, al), spacing=8)


def draw_caption(d, t, cards, highlights):
    for a, b, text in cards:
        if not (a <= t < b):
            continue
        s = 0.85 + 0.15 * R.ease((t - a) / 0.12)
        f = R.font(R.FB, int(104 * s))
        y = 1020
        for ln in wrap(text, 15):
            ws = ln.split(); sp = d.textlength(" ", font=f)
            x = W // 2 - (sum(d.textlength(w, font=f) for w in ws) + sp * (len(ws) - 1)) / 2
            for w in ws:
                col = R.GLOW if is_hi(w, highlights) else R.WHITE
                d.text((x, y), w, font=f, fill=col + (255,), stroke_width=8, stroke_fill=(0, 0, 0, 255))
                x += d.textlength(w, font=f) + sp
            y += int(122 * s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queue"); ap.add_argument("key"); ap.add_argument("out")
    ap.add_argument("--voice"); ap.add_argument("--timings")
    a = ap.parse_args()
    s = pick(json.load(open(a.queue, encoding="utf-8")), a.key)
    timings = json.load(open(a.timings)) if a.timings else None
    wt = word_times(s["vo"], timings)
    cards = chunks(wt)
    dur = round(wt[-1][2] + TAIL, 2)
    print(f"[render] {s['id']}: {len(wt)} words, {len(cards)} caption cards, {dur}s, "
          f"timing={'voice' if timings else 'ESTIMATED'}, voice={'yes' if a.voice else 'NONE (bed only)'}", file=sys.stderr)

    # point the shared background/audio helpers at this Short's length
    R.DUR = dur
    R.BEATS[:] = [(c[0], c[1], "", "", "") for c in cards if c[2][-1] in ".?!"]
    R.RIPPLES[:] = [(t, R.rng.uniform(80, R.BW - 80), R.rng.uniform(R.HORIZON + 120, R.BH - 80)) for t in np.arange(0.3, dur, 0.9)]
    tmp = tempfile.mkdtemp(prefix="short_")
    bed = os.path.join(tmp, "bed.wav"); R.audio(bed)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", bed]
    if a.voice:
        cmd += ["-i", a.voice, "-filter_complex",
                f"[1:a]volume=0.22[b];[2:a]adelay={int(LEAD * 1000)}:all=1[v];[b][v]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]",
                "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", a.out]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(FPS * dur)
    stills = {int(FPS * (c[0] + 0.4)) for c in cards[:: max(1, len(cards) // 4)]}
    for i in range(n):
        t = i / FPS
        fr = R.bg_frame(t).convert("RGBA")
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        draw_bubble(d, t, s["bubble"]); draw_caption(d, t, cards, s["highlights"])
        fr.alpha_composite(lay)
        ff.stdin.write(fr.convert("RGB").tobytes())
        if i in stills:
            fr.convert("RGB").save(os.path.join(tmp, f"still_{i:04d}.png"))
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("[render] ffmpeg failed")
    print(f"[render] done {a.out}  (stills in {tmp})", file=sys.stderr)


if __name__ == "__main__":
    main()
