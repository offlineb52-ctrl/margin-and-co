#!/usr/bin/env bash
# Margin & Co. — run the research, rebuild the site, one command.
#
#   ./publish.sh 2          # run week 2 and rebuild the site
#   ./publish.sh 2 --serve  # ...and preview it at localhost:8000
#
# Deploying is a separate, deliberate step: check the numbers first, then push.

set -euo pipefail
cd "$(dirname "$0")"

WEEK="${1:?usage: ./publish.sh <week-number> [--serve]}"
shift || true

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "==> Sanity checks"
"$PY" -m tests.test_indicators | tail -2

echo
echo "==> Running week $WEEK"
"$PY" run_pipeline.py --universe all --week "$WEEK"

echo
echo "==> Advancing the live paper portfolio"
"$PY" live/run_live.py

echo
echo "==> Exporting live book + charts"
"$PY" -m live.export

echo
echo "==> Building site"
"$PY" site/build.py "$@"

echo
echo "Review site/dist/, then deploy:  npx wrangler pages deploy site/dist"
