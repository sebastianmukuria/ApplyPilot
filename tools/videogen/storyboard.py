"""The ApplyPilot demo — script as data.

This is the "script" the pipeline turns into a video. Each scene carries the
narration (which also drives its length) and an animated layout. Edit freely
and re-run build.py; swap kinds, reorder, rewrite copy.

kinds: title | statement | funnel | feature | shot
"""

SHOTS = "../assets/shots"  # relative to scenes/scene.html

STORYBOARD = [
    {
        "kind": "title",
        "eyebrow": "Application autopilot",
        "title": "ApplyPilot",
        "sub": "The job hunt, on autopilot — running entirely on your own machine.",
        "narration": "This is ApplyPilot. An autonomous job-search co-pilot that runs entirely on your own computer.",
        "caption": "ApplyPilot — the job hunt on autopilot.",
    },
    {
        "kind": "statement",
        "eyebrow": "The problem",
        "title": "Applying is a numbers game that eats your life.",
        "size": 58,
        "sub": "Hundreds of postings. Each one a form, a résumé tweak, a cover letter.",
        "narration": "Applying to jobs is a numbers game that eats your life. Hundreds of postings, each one a form to fill, a résumé to tweak, a cover letter to write.",
        "caption": "Applying is a numbers game that eats your life.",
    },
    {
        "kind": "funnel",
        "eyebrow": "Discover & score",
        "title": "It reads every posting and scores the fit — 1 to 10.",
        "cells": [
            {"label": "Discovered", "value": 5241},
            {"label": "Scored", "value": 619},
            {"label": "Strong", "value": 234},
            {"label": "Docs ready", "value": 160},
        ],
        "narration": "It scans the job boards, reads every posting, and scores each one against your résumé, so you only ever look at the jobs worth your time.",
        "caption": "Scans the boards, scores every job against your résumé.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/queue.png",
        "eyebrow": "Tailored, per job",
        "title": "Résumé + cover letter, written for each role.",
        "narration": "For the strong matches, it tailors a résumé and writes a cover letter — for every single job.",
        "caption": "Tailors a résumé and writes a cover letter for each job.",
    },
    {
        "kind": "feature",
        "eyebrow": "Supervised auto-apply",
        "title": "It fills the application in a real browser.",
        "sub": "And it never clicks Submit without you. It fills everything, pings your phone, and hands you the open window to review.",
        "pills": [
            {"text": "Fills every field", "color": "var(--sky)"},
            {"text": "Solves CAPTCHAs with you", "color": "var(--clay)"},
            {"text": "You approve & submit", "color": "var(--sage)"},
        ],
        "narration": "Then it fills out the application in a real Chrome window. And it never clicks submit without you — it pings your phone, and hands you the browser to review and send.",
        "caption": "Fills the form in a real browser. You approve before it submits.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/intel.png",
        "eyebrow": "Pipeline radar",
        "title": "It watches your inbox and charts who replied.",
        "narration": "It even watches your inbox — interviews, rejections, recruiter replies — and charts your real response rates.",
        "caption": "Watches your inbox and charts your real response rates.",
    },
    {
        "kind": "statement",
        "eyebrow": "The engine",
        "title": "All of it runs on a Claude subscription.",
        "size": 56,
        "sub": "No API keys. No per-application cost. Your data never leaves your machine.",
        "narration": "And all of it runs on a single Claude subscription. No API keys, no per-application cost, and your data never leaves your machine.",
        "caption": "Runs on a Claude subscription. No API keys. Fully local.",
    },
    {
        "kind": "title",
        "eyebrow": "github.com/sebastianmukuria/ApplyPilot",
        "title": "ApplyPilot.",
        "sub": "Apply smarter, not harder.",
        "narration": "ApplyPilot. Apply smarter, not harder.",
        "caption": "github.com/sebastianmukuria/ApplyPilot",
        "min_duration": 3.5,
    },
]
