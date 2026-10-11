#!/usr/bin/env bash
# Weekly runner: makes sure the Python deps are installed (in a git-ignored .venv),
# then runs run_weekly.py with whatever flags you pass.
#   ./weekly.sh --fetch-only          # no API keys needed
#   ./weekly.sh --dry-run             # needs ANTHROPIC_API_KEY (OPENAI_API_KEY optional)
#   ./weekly.sh                       # writes content/ (then commit + PR)
# Keys: export them, or put them in .env (git-ignored) next to this script.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
if ! .venv/bin/python -c "import feedparser, yaml, requests, anthropic, openai" 2>/dev/null; then
  .venv/bin/python -m pip install -q -r requirements.txt
fi
exec .venv/bin/python run_weekly.py "$@"
