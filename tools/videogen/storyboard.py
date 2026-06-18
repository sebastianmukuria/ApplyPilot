"""The ApplyPilot demo — script as data.

A tab-by-tab product walkthrough: Deck → Queue → auto-apply → Intel →
Answers → Settings → the engine. Each scene carries the narration (which
also drives its length) and an animated layout. Edit freely and re-run
build.py; swap kinds, reorder, rewrite copy.

kinds: title | statement | funnel | feature | shot
"""

SHOTS = "../assets/shots"  # relative to scenes/scene.html

STORYBOARD = [
    {
        "kind": "title",
        "eyebrow": "Application autopilot",
        "title": "ApplyPilot",
        "sub": "An autonomous job-search co-pilot — a quick tour.",
        "narration": "This is ApplyPilot, an autonomous job-search co-pilot that runs entirely on your own machine. Let me walk you through it.",
        "caption": "ApplyPilot — a quick walkthrough.",
    },
    {
        "kind": "statement",
        "eyebrow": "Why",
        "title": "Applying is a numbers game that eats your life.",
        "size": 56,
        "sub": "ApplyPilot turns the whole funnel into one dashboard.",
        "narration": "Applying is a numbers game that eats your life. ApplyPilot turns the whole funnel into one dashboard.",
        "caption": "The whole job hunt, in one place.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/deck.png",
        "eyebrow": "The Deck",
        "title": "Your command center.",
        "sub": "A live funnel, up to three runs at once, and real-time Claude usage.",
        "narration": "This is the Deck, your command center. A live funnel tracks every job from discovered, to scored, to strong matches, to applied, and you can click any number to jump straight to those jobs. It runs up to three applications at the same time, streaming each one live, and a usage tracker shows exactly what Claude is doing.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/queue.png",
        "eyebrow": "The Queue",
        "title": "Every job, scored against your résumé.",
        "sub": "Filter and sort, then preview the tailored résumé and cover letter per role.",
        "narration": "The Queue is the job board. Every posting is scored one to ten against your résumé, so you only see roles worth your time. Expand any card to see why it scored that way, and preview the résumé and cover letter it tailored for that exact job.",
    },
    {
        "kind": "feature",
        "eyebrow": "Supervised auto-apply",
        "title": "It fills everything. You stay in control.",
        "sub": "It opens a real Chrome window and fills the whole application — but you solve the CAPTCHA and you click submit.",
        "pills": [
            {"text": "You solve the CAPTCHA", "color": "var(--clay)"},
            {"text": "You click submit", "color": "var(--sage)"},
            {"text": "Stays within site ToS", "color": "var(--sky)"},
        ],
        "narration": "Hit apply, and it opens a real Chrome window and fills out the entire application. But by design, it never solves the CAPTCHA, and it never clicks submit, you do. That keeps ApplyPilot within every site's terms of service, and it means you always get the final say before anything is sent.",
        "caption": "Fills the form. You solve the CAPTCHA and click submit.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/intel.png",
        "eyebrow": "Intel",
        "title": "Your campaign, measured.",
        "sub": "Inbox-tracked outcomes and response rates by score band.",
        "narration": "Intel is your campaign analytics. It watches your inbox, read-only, just sender, subject, and date, to automatically log interview invites, rejections, and recruiter replies, then charts your applications per day, your pipeline from applied to offer, and your response rate by score band.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/answers.png",
        "eyebrow": "Answers",
        "title": "Truthful answers, on tap.",
        "sub": "Drafted from a profile of your real projects and skills.",
        "narration": "The Answers tab handles those open-ended application questions. It drafts truthful answers grounded in a profile of your real projects and skills, so you're never rewriting why do you want to work here for the hundredth time.",
    },
    {
        "kind": "shot",
        "image": f"{SHOTS}/settings.png",
        "eyebrow": "Settings",
        "title": "Tunable — no config files.",
        "sub": "Model, salary, master résumé, alerts, and Gmail — all in the UI.",
        "narration": "Everything's tunable in settings. Pick your Claude model, decide how to answer the salary question, lock in a master résumé, wire up Telegram or browser alerts, and connect Gmail, all without touching a config file.",
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
