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
"$PY" -m tests.test_lookup | tail -2
"$PY" -m tests.test_cleanup | tail -2
command -v node >/dev/null && node tests/js/test_csv_export.mjs | tail -2

echo
echo "==> Running week $WEEK"
"$PY" run_pipeline.py --universe all --week "$WEEK"

echo
echo "==> Scoring the week (Survival Score, archive, free + Pro reports)"
# run_pipeline.py above draws the charts and writes the markdown report. It
# does NOT compute the Survival Score, record the week to the archive, or
# build the tiered reports -- that is this step, and leaving it out publishes
# a week with last week's scores still on the site.
"$PY" run_scores.py --week "$WEEK" --universe all --export-lookup

echo
echo "==> Advancing the live paper portfolio"
# Only safe after the US close has settled. The daily CI job runs at 22:15 UTC
# for that reason: Yahoo can still revise a daily bar in the first hour or so
# after the close, and this ledger is append-only -- a session marked early is
# never re-marked, because run_live skips any date already in the equity
# curve. Set SKIP_LIVE=1 to publish the research without touching the book.
if [ "${SKIP_LIVE:-0}" = "1" ]; then
  echo "    SKIPPED (SKIP_LIVE=1) -- the scheduled 22:15 UTC job will record it."
else
  "$PY" live/run_live.py
fi

echo
echo "==> Exporting live book + charts"
"$PY" -m live.export

echo
echo "==> Building site"
"$PY" site/build.py "$@"

echo
echo "Review site/dist/, then deploy:  npx wrangler pages deploy site/dist"
