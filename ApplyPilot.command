#!/usr/bin/env bash
# Double-click me (macOS) to start the ApplyPilot control panel.
# First run walks you through setup; after that it goes straight to the GUI.
cd "$(dirname "$0")"

if [ ! -x .venv/bin/applypilot ]; then
  echo "ApplyPilot isn't installed yet."
  echo "Run the installer first -- see SETUP.md (or README) in this folder."
  read -r -p "Press Enter to close..." _
  exit 1
fi

if [ ! -f "${APPLYPILOT_DIR:-$HOME/.applypilot}/profile.json" ]; then
  echo "First run -- let's set up your resume and profile."
  ./.venv/bin/applypilot init
fi

exec ./.venv/bin/applypilot app
