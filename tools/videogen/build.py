#!/usr/bin/env python3
"""Autonomous video pipeline: a storyboard in, a finished MP4 (+GIF) out.

Each scene is data (narration + an animated layout). The build:
  1. narrate  — macOS `say` renders the narration to audio; its duration
     drives the scene length.
  2. render   — Playwright loads the universal GSAP scene (scenes/scene.html),
     plays its timeline, and screen-records the scene.
  3. mux      — FFmpeg lays the narration under the video (with a lead-in).
  4. stitch   — all scenes are concatenated into the final film + a web GIF.
  5. review   — one frame per scene is dumped to out/review/ so the author
     (you, or Claude) can eyeball every scene and re-render what looks off.

Reusable: edit storyboard.py and re-run. No external accounts — TTS and
rendering are all local.

  python build.py                 # full build from storyboard.py
  python build.py --no-narration  # silent (captions only)
  python build.py --review-only   # re-dump review frames from existing scenes
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
SCENES = OUT / "scenes"
REVIEW = OUT / "review"
SCENE_HTML = ROOT / "scenes" / "scene.html"

W, H, FPS = 1280, 720, 30
VOICE = "Samantha"
LEAD_IN = 0.45   # animation runs this long before narration starts
TAIL = 0.7       # hold after narration ends


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ffprobe_duration(path: Path) -> float:
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(path)]).stdout.strip())


def narrate(text: str, dst: Path, rate: int = 178) -> float:
    """Render narration to m4a via `say`; return its duration in seconds."""
    aiff = dst.with_suffix(".aiff")
    sh(["say", "-v", VOICE, "-r", str(rate), "-o", str(aiff), text])
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff),
        "-c:a", "aac", "-b:a", "160k", str(dst)])
    aiff.unlink(missing_ok=True)
    return ffprobe_duration(dst)


def record_scene_webm(scene: dict, ctx: dict, duration: float, dst_dir: Path) -> Path:
    """Play scene.html and screen-record for `duration` seconds. Returns webm."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    init = f"window.SCENE = {json.dumps(scene)}; window.CTX = {json.dumps(ctx)};"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        context = browser.new_context(
            viewport={"width": W, "height": H}, device_scale_factor=1,
            record_video_dir=str(dst_dir), record_video_size={"width": W, "height": H},
        )
        page = context.new_page()
        page.add_init_script(init)
        page.goto(SCENE_HTML.as_uri(), wait_until="load")
        try:
            page.wait_for_function("window.__ready === true", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(int(duration * 1000))
        context.close()  # finalizes the webm
        browser.close()
    return next(dst_dir.glob("*.webm"))


def scene_mp4(scene: dict, ctx: dict, idx: int) -> Path:
    """Narrate + render + mux one scene into a uniform mp4."""
    SCENES.mkdir(parents=True, exist_ok=True)
    audio = SCENES / f"s{idx:02d}.m4a"
    narr = scene.get("narration", "")
    if narr and ctx["narration"]:
        adur = narrate(narr, audio)
        duration = LEAD_IN + adur + TAIL
    else:
        audio = None
        duration = scene.get("duration", 3.5)
    duration = max(duration, scene.get("min_duration", 2.5))

    raw_dir = SCENES / f"raw{idx:02d}"
    for f in raw_dir.glob("*.webm"):
        f.unlink()
    webm = record_scene_webm(scene, {**ctx, "duration": duration}, duration, raw_dir)

    out = SCENES / f"s{idx:02d}.mp4"
    vf = f"scale={W}:{H}:flags=lanczos,fps={FPS},format=yuv420p"
    if audio:
        # delay narration by LEAD_IN; pad/trim video to audio+tail length
        sh(["ffmpeg", "-y", "-loglevel", "error",
            "-i", str(webm), "-i", str(audio),
            "-filter_complex",
            f"[0:v]{vf},tpad=stop_mode=clone:stop_duration={TAIL}[v];"
            f"[1:a]adelay={int(LEAD_IN*1000)}|{int(LEAD_IN*1000)},apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-c:a", "aac", "-b:a", "160k", str(out)])
    else:
        sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm),
            "-vf", vf, "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-crf", "20", "-preset", "medium",
            "-an", str(out)])
    # review frame: midpoint
    REVIEW.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{duration/2:.2f}",
        "-i", str(out), "-frames:v", "1", str(REVIEW / f"s{idx:02d}.png")])
    return out


def stitch(parts: list[Path], final: Path):
    listfile = OUT / "concat.txt"
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    # transitions: a light xfade chain is overkill here; hard cuts read clean
    sh(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-c:v", "libx264", "-crf", "20", "-preset", "medium",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final)])


def make_gif(mp4: Path, gif: Path, fps: int = 11, width: int = 900):
    pal = OUT / "palette.png"
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4),
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,palettegen=max_colors=128", str(pal)])
    sh(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-i", str(pal),
        "-lavfi", f"fps={fps},scale={width}:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
        str(gif)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", default="storyboard")
    ap.add_argument("--no-narration", action="store_true")
    ap.add_argument("--review-only", action="store_true")
    ap.add_argument("--name", default="applypilot_demo")
    args = ap.parse_args()

    import importlib
    sb = importlib.import_module(args.storyboard).STORYBOARD
    ctx_base = {"total": len(sb), "narration": not args.no_narration}

    final = OUT / f"{args.name}.mp4"
    gif = OUT / f"{args.name}.gif"

    if not args.review_only:
        parts = []
        for i, scene in enumerate(sb):
            print(f"[{i+1}/{len(sb)}] {scene.get('kind')}: {scene.get('narration','')[:50]}")
            parts.append(scene_mp4(scene, {**ctx_base, "index": i}, i))
        print("stitching…")
        stitch(parts, final)
        make_gif(final, gif)
        print(f"\n✓ {final}  ({ffprobe_duration(final):.1f}s)\n✓ {gif}")
    print(f"review frames → {REVIEW}/")


if __name__ == "__main__":
    main()
