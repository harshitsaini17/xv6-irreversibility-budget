#!/usr/bin/env bash
# Start the xv6 agent dashboard (creates the venv on first run).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install --no-cache-dir -q -r requirements.txt
fi
exec .venv/bin/python server.py
