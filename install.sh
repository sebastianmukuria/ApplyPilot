#!/usr/bin/env bash
# ApplyPilot installer — macOS / Linux.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/sebastianmukuria/ApplyPilot/fixes/pre-flight/install.sh | bash
#
# Installs into ~/ApplyPilot (override with APPLYPILOT_HOME=/path).
set -euo pipefail

REPO_URL="${APPLYPILOT_REPO:-https://github.com/sebastianmukuria/ApplyPilot.git}"
BRANCH="${APPLYPILOT_BRANCH:-fixes/pre-flight}"
DEST="${APPLYPILOT_HOME:-$HOME/ApplyPilot}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required. On macOS, run: xcode-select --install  (then re-run this installer)"

# Find a Python >= 3.11
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || fail "Python 3.11+ is required. macOS: brew install python@3.12  -- or download from https://www.python.org/downloads/"

say "Getting ApplyPilot ($BRANCH) into $DEST"
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" fetch origin "$BRANCH"
  git -C "$DEST" checkout "$BRANCH"
  git -C "$DEST" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

say "Creating an isolated Python environment"
"$PY" -m venv "$DEST/.venv"
PIP="$DEST/.venv/bin/pip"
"$PIP" install --quiet --upgrade pip

say "Installing ApplyPilot + Flight Deck"
"$PIP" install --quiet -e "$DEST[app,gui]"

say "Installing the job-board scraper"
# jobspy pins an exact numpy in its metadata; it works fine with modern numpy,
# so install it without deps and add its real runtime deps separately.
"$PIP" install --quiet --no-deps python-jobspy
"$PIP" install --quiet pydantic tls-client requests markdownify regex

say "Installing the browser used for enrichment and PDF rendering"
"$DEST/.venv/bin/playwright" install chromium

chmod +x "$DEST/ApplyPilot.command" 2>/dev/null || true

say "Installed."
cat <<EOF

Next steps
  1) One-time setup (your resume, profile, and job searches):
       $DEST/.venv/bin/applypilot init
  2) Put your Gemini API key in ~/.applypilot/.env  -- see SETUP.md section 3
     (key from https://aistudio.google.com/apikey; LLM_MODEL=gemini-3.1-flash-lite)
  3) Start the control panel:
       macOS:    double-click  $DEST/ApplyPilot.command
       any OS:   $DEST/.venv/bin/applypilot app

Full guide: $DEST/SETUP.md
EOF
