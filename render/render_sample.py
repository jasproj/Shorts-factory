"""Sample Short #1 — 'Which bio bay is brightest' (Wander Puerto Rico).
Text-on-screen over generated bioluminescent-water visuals + generated ambient bed.
Prototype renderer (PIL + numpy -> ffmpeg). Production target: Remotion templates.
Usage: python3 render_sample.py out.mp4
"""
import subprocess, sys, wave
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS, DUR = 1080, 1920, 30, 15.0
N = int(FPS * DUR)
OUT = sys.argv[1] if len(sys.argv) > 1 else "sample.mp4"
FB = "/usr/share/fonts/opentype/inter/InterDisplay-Bold.otf"
FM = "/usr/share/fonts/opentype/inter/InterDisplay-Medium.otf"
font = lambda p, s: ImageFont.truetype(p, s)

GLOW = (90, 255, 220)
WHITE = (255, 255, 255)

# (start, end, kicker, headline, sub)
BEATS = [
    (0.0, 2.8, "PUERTO RICO", "3 bays glow\nat night.", "Only one is the\nbrightest on Earth."),
    (2.8, 6.0, "#1  ·  VIEQUES", "Mosquito Bay", "Brightest in the world.\nFerry or flight to get there."),
    (6.0, 9.2, "#2  ·  FAJARDO", "Laguna Grande", "45 min from San Juan.\nKayak through mangroves."),
    (9.2, 12.2, "#3  ·  LAJAS", "La Parguera", "The only one you\ncan swim in. By boat."),
    (12.2, 15.0, "THE CALL", "One night?\nFajardo.", "Whole trip? Vieques.\nMoon calendar + tours ↓"),
]

rng = np.random.default_rng(7)
BW, BH = 540, 960
# background gradient (night sky -> dark lagoon)
y = np.linspace(0, 1, BH)[:, None]
base = np.zeros((BH, BW, 3), np.float32)
base[..., 0] = 4 + 6 * y
base[..., 1] = 10 + 22 * y
base[..., 2] = 28 + 30 * (1 - abs(y - 0.45) * 1.6)
HORIZON = int(BH * 0.38)
# stars
stars = [(rng.integers(0, BW), rng.integers(0, HORIZON - 10), rng.uniform(0.3, 1), rng.uniform(0, 6.28)) for _ in range(140)]
# glow particles in water
P = 650
px = rng.uniform(0, BW, P); py = rng.uniform(HORIZON + 8, BH, P)
pph = rng.uniform(0, 6.28, P); psp = rng.uniform(0.6, 2.2, P)
# mangrove silhouette along horizon
xs = np.arange(BW)
ridge = HORIZON - (18 + 14 * np.sin(xs / 37.0) + 9 * np.sin(xs / 13.0 + 1) + 6 * rng.random(BW).cumsum() % 7)
# paddle ripples: (time, x, y)
RIPPLES = [(t, rng.uniform(80, BW - 80), rng.uniform(HORIZON + 120, BH - 80)) for t in np.arange(0.3, DUR, 0.9)]


def background(t):
    img = base.copy()
    # water shimmer bands
    wy = np.arange(HORIZON, BH)
    band = (np.sin(wy[:, None] / 6.0 + t * 1.3 + xs[None, :] / 40.0) * 0.5 + 0.5) * 6
    img[HORIZON:, :, 1] += band; img[HORIZON:, :, 2] += band * 1.2
    for x0 in range(BW):
        img[: int(max(0, ridge[x0])), x0] *= 1.0
        img[int(max(0, ridge[x0])):HORIZON, x0] = (2, 6, 8)
    layer = np.zeros((BH, BW), np.float32)
    for sx, sy, b, ph in stars:
        layer[sy, sx] += b * (0.6 + 0.4 * np.sin(t * 2 + ph))
    star_layer = layer.copy()
    glow = np.zeros((BH, BW), np.float32)
    inten = 0.25 + 0.75 * (np.sin(t * psp + pph) * 0.5 + 0.5) ** 3
    for (rt, rx, ry) in RIPPLES:
        age = t - rt
        if 0 <= age < 2.2:
            d = np.hypot(px - rx, (py - ry) * 1.8)
            r = 30 + age * 90
            inten += np.exp(-((d - r) ** 2) / 300) * (1 - age / 2.2) * 2.2
    xi = np.clip((px + np.sin(t * 0.4 + pph) * 4).astype(int), 0, BW - 1)
    yi = np.clip(py.astype(int), 0, BH - 1)
    np.add.at(glow, (yi, xi), inten)
    return img, star_layer, glow


def bg_frame(t):
    img, sl, gl = background(t)
    g = Image.fromarray(np.clip(gl * 255, 0, 255).astype(np.uint8))
    gb = np.asarray(g.filter(ImageFilter.GaussianBlur(5)), np.float32) / 255 * 11
    gs = np.asarray(g.filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255 * 4
    s = np.asarray(Image.fromarray(np.clip(sl * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7)), np.float32) / 255 * 2
    for c, k in enumerate((0.35, 1.0, 0.86)):
        img[..., c] += (gb + gs) * 255 * k
    img += s[..., None] * 220
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    return im


def ease(x):
    x = max(0.0, min(1.0, x)); return 1 - (1 - x) ** 3


def text_layer(t):
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for (a, b, kick, head, sub) in BEATS:
        if not (a <= t < b):
            continue
        tin = ease((t - a) / 0.35); tout = ease((b - t) / 0.25) if b < DUR else 1
        al = int(255 * min(tin, tout)); dy = int((1 - tin) * 40)
        top = 560 + dy
        # scrim
        hh = 130 * (head.count("\n") + 1)
        bot = top + 130 + hh + 75 * (sub.count("\n") + 1) + 50
        d.rounded_rectangle((70, top - 60, W - 70, bot), 48, fill=(0, 10, 18, int(al * 0.6)))
        d.text((W // 2, top), kick, font=font(FM, 44), fill=GLOW + (al,), anchor="ma")
        d.multiline_text((W // 2, top + 90), head, font=font(FB, 118), fill=WHITE + (al,), anchor="ma", align="center", spacing=8)
        d.multiline_text((W // 2, top + 130 + hh), sub, font=font(FM, 58), fill=(215, 240, 238, al), anchor="ma", align="center", spacing=14)
    # persistent brand + progress
    d.text((W // 2, 150), "WANDER PUERTO RICO", font=font(FB, 40), fill=(255, 255, 255, 200), anchor="ma")
    d.rectangle((0, H - 14, int(W * t / DUR), H), fill=GLOW + (230,))
    if t >= 12.6:
        al = int(255 * ease((t - 12.6) / 0.4))
        d.rounded_rectangle((200, 1560, W - 200, 1680), 60, fill=GLOW + (al,))
        d.text((W // 2, 1620), "wanderpuertorico.com", font=font(FB, 50), fill=(0, 30, 35, al), anchor="mm")
    return lay


def audio(path):
    sr = 44100; t = np.arange(int(sr * DUR)) / sr
    out = np.zeros_like(t)
    for f, a in [(55, .18), (82.4, .12), (110, .10), (164.8, .06), (220, .04), (329.6, .025)]:
        out += a * np.sin(2 * np.pi * f * t + 0.3 * np.sin(2 * np.pi * 0.1 * t))
    noise = rng.standard_normal(t.size)
    k = np.ones(400) / 400; water = np.convolve(noise, k, "same") * 3
    out += water * (0.5 + 0.5 * np.sin(2 * np.pi * 0.25 * t)) * 0.25
    for (a, *_r) in BEATS:  # soft chime on each beat
        idx = t >= a; tt = t[idx] - a
        for f in (659.3, 987.8):
            out[idx] += 0.10 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 3)
    env = np.minimum(1, t / 0.6) * np.minimum(1, (DUR - t) / 1.0)
    out = out * env; out /= np.abs(out).max() * 1.15
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((out * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    audio("/tmp/bed.wav")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-i", "/tmp/bed.wav", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5", "-shortest", "-movflags", "+faststart", OUT], stdin=subprocess.PIPE)
    for i in range(N):
        t = i / FPS
        fr = bg_frame(t).convert("RGBA")
        fr.alpha_composite(text_layer(t))
        ff.stdin.write(fr.convert("RGB").tobytes())
        if i in (40, 130, 230, 320, 430):
            fr.convert("RGB").save(f"/tmp/frame_{i}.png")
    ff.stdin.close(); ff.wait()
    print("done", OUT)
