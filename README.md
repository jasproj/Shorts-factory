# Shorts factory — Wander Puerto Rico (bio bay)

Question-led Shorts: a real viewer question on screen, a 15-second answer. Not ads.

## Pipeline

| Step | Module | State |
|---|---|---|
| 1. Questions | `collector/collect.py` → `data/questions.json` | Built. YouTube source needs `YOUTUBE_API_KEY` |
| 2. Scripts | `scripts/*.md` → `writer/guard.py` → `scripts/batch-NN.json` | 12 hand-written scripts pass the guard. Auto-writer not built |
| 3. Voice | ElevenLabs TTS + word timings | Not built. Needs `ELEVENLABS_API_KEY` |
| 4. Render | `render/render_short.py` | Built. Generated background, estimated caption timing until step 3 exists |
| 5. Upload | YouTube Data API | Not built |
| 6. Schedule | GitHub Actions cron | Not built |

## Run it

    python3 collector/collect.py                      # rank questions
    python3 writer/guard.py scripts/test-batch-01.md --out scripts/batch-01.json
    python3 render/render_short.py scripts/batch-01.json 01 out.mp4

Needs Python 3, Pillow, numpy, ffmpeg and the Inter font.

## Rules

- Every script line maps to a fact ID in `facts/biobay.md`. The guard rejects anything else.
- Prices are never typed into a script.
- Nothing renders without passing the guard.
