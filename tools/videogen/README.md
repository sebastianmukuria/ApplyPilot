# videogen — autonomous demo-video pipeline

One storyboard in, a finished narrated MP4 (+ GIF) out. No external accounts —
script, motion graphics, narration, editing, and self-review all run locally.

Inspired by the "Claude makes a whole video from one prompt" demos: the parts
that don't need a cloned voice or a video avatar (script → motion graphics →
narration → edit → visual self-review) are exactly what this does.

## What it does

1. **narrate** — your ElevenLabs cloned voice (if configured) or macOS `say`
   renders each scene's narration to audio; its duration drives the scene
   length. Per-scene generation keeps every clip short, so the voice never
   drifts.
2. **render** — Playwright loads `scenes/scene.html` (a universal GSAP scene in
   the Flight Deck visual language) and screen-records each scene.
3. **mux** — FFmpeg lays the narration under the animation with a lead-in.
4. **stitch** — scenes concatenate into the final film + a web GIF.
5. **review** — one frame per scene lands in `out/review/` so the author (you,
   or Claude reading the frames) can eyeball every scene and re-render what's off.

## Run it

```bash
cd tools/videogen
python build.py                 # full build from storyboard.py
python build.py --no-narration  # silent (captions only)
```

Output: `out/applypilot_demo.mp4` and `out/applypilot_demo.gif`, review frames
in `out/review/`. Requires the project venv (Playwright + Chromium) and FFmpeg;
`say` is built into macOS.

## Make your own

`storyboard.py` is the script — a list of scenes, each with `narration` and an
animated layout. Edit copy, reorder, add scenes, then re-run. Scene kinds:

| kind | use |
|---|---|
| `title` | wordmark + eyebrow + subtitle |
| `statement` | big per-word headline (the hook lines) |
| `funnel` | animated count-up stat band |
| `feature` | headline + sub + colored "pill" chips |
| `shot` | a real UI screenshot with a slow ken-burns + caption |

`shot` scenes use PNGs in `assets/shots/`. Capture fresh ones from the live app
with `python capture_shots.py` (tours every tab + settings at 2× while the app
runs on `127.0.0.1:8765`), or drop in any ~1440×900 screenshot. The label band
(eyebrow · title · sub) renders on clean canvas *above* the screenshot, so it
never overlaps the app's own UI text.

### Narration voice

By default narration is macOS `say` ("Samantha" — change `VOICE` in `build.py`),
which needs no account. To narrate in **your own ElevenLabs cloned voice**, set
these in `~/.applypilot/.env` (gitignored, never committed):

```
ELEVENLABS_API_KEY=sk_...          # key needs Text-to-Speech + Voices-Read scopes
ELEVENLABS_VOICE_ID=...            # your cloned voice's id
ELEVENLABS_STABILITY=0.5           # optional — lower = more expressive
ELEVENLABS_STYLE=0.4               # optional — higher = more energy/inflection
ELEVENLABS_SIMILARITY=0.85         # optional — closeness to your clone
```

The build prints which voice it's using before spending any credits, and
`NARRATION_SPEED` in `build.py` (default `1.1`) sets the overall pace. ElevenLabs
draws from your subscription's monthly credits — a full run is ~1k characters.

## Not included (needs your own account/hardware)

A talking-head avatar (HeyGen and similar) — that trains on your real face and
runs through a paid API. This pipeline covers everything else: script, motion
graphics, narration (local or your cloned voice), editing, and visual
self-review.
