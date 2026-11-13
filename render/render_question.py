"""Question-led Short: viewer question bubble + fast caption answer. No brand box, no CTA button.
Usage: python3 render_question.py out.mp4
"""
import subprocess, sys
from PIL import Image, ImageDraw
import render_sample as R

OUT = sys.argv[1] if len(sys.argv) > 1 else "q.mp4"
W, H, FPS, DUR = R.W, R.H, R.FPS, R.DUR
QUESTION = "wait can you actually swim\nin the glowing water??"
# (start, end, caption, highlight-word)
CAPS = [
    (0.6, 2.4, "Short answer:", None),
    (2.4, 4.4, "in 2 of the 3 bays,\nNO.", "NO."),
    (4.4, 7.0, "Vieques + Fajardo\nare protected.", "protected."),
    (7.0, 9.2, "Kayak only.", "Kayak"),
    (9.2, 12.0, "La Parguera is\nthe exception.", "exception."),
    (12.0, 15.0, "Boat out, then\nyou get in.", "get in."),
]
R.BEATS[:] = [(c[0], c[1], "", "", "") for c in CAPS]  # chime timing for audio bed


def bubble(d, t):
    al = int(255 * R.ease(t / 0.3))
    x0, y0 = 90, 330
    d.rounded_rectangle((x0, y0, W - 90, y0 + 250), 36, fill=(255, 255, 255, al))
    d.ellipse((x0 + 34, y0 + 34, x0 + 94, y0 + 94), fill=(120, 150, 160, al))
    d.text((x0 + 116, y0 + 44), "Reply to a viewer", font=R.font(R.FM, 34), fill=(110, 110, 120, al))
    d.multiline_text((x0 + 40, y0 + 112), QUESTION, font=R.font(R.FB, 50), fill=(20, 20, 25, al), spacing=8)


def captions(d, t):
    for a, b, txt, hi in CAPS:
        if not (a <= t < b):
            continue
        s = 0.85 + 0.15 * R.ease((t - a) / 0.15)
        f = R.font(R.FB, int(104 * s))
        lines = txt.split("\n"); y = 1020
        for ln in lines:
            col = R.GLOW if hi and hi.rstrip(".") in ln else R.WHITE
            d.text((W // 2, y), ln, font=f, fill=col + (255,), anchor="ma", stroke_width=8, stroke_fill=(0, 0, 0, 255))
            y += int(122 * s)


if __name__ == "__main__":
    R.audio("/tmp/bed.wav")
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                           "-i", "/tmp/bed.wav", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5", "-shortest", "-movflags", "+faststart", OUT], stdin=subprocess.PIPE)
    for i in range(int(FPS * DUR)):
        t = i / FPS
        fr = R.bg_frame(t).convert("RGBA")
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
        bubble(d, t); captions(d, t)
        fr.alpha_composite(lay)
        ff.stdin.write(fr.convert("RGB").tobytes())
        if i in (20, 100, 160, 230, 300, 420):
            fr.convert("RGB").save(f"/tmp/q_{i}.png")
    ff.stdin.close(); ff.wait(); print("done", OUT)
