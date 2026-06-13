# videogen — autonomous demo-video pipeline

One storyboard in, a finished narrated MP4 (+ GIF) out. No external accounts —
script, motion graphics, narration, editing, and self-review all run locally.

Inspired by the "Claude makes a whole video from one prompt" demos: the parts
that don't need a cloned voice or a video avatar (script → motion graphics →
narration → edit → visual self-review) are exactly what this does.

## What it does

1. **narrate** — macOS `say` renders each scene's narration to audio; its
   duration drives the scene length.
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

`shot` scenes use PNGs in `assets/shots/` — capture fresh ones from the live
app (any Playwright screenshot at ~1440×900 works).

The narration voice is macOS `say` ("Samantha" by default — change `VOICE` in
`build.py`). For higher-quality narration, install a Premium voice in
System Settings → Accessibility → Spoken Content, or swap in a cloud TTS.

## Not included (needs your own accounts)

A cloned voice (ElevenLabs) and a talking-head avatar (HeyGen) — those train on
your real voice/face and run through paid APIs. This pipeline is the
account-free path: animated motion graphics + local narration.
