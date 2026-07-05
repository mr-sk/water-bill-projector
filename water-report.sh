#!/usr/bin/env bash
# water-report.sh — Refresh Eye on Water data and print the daily water report.
set -euo pipefail
cd "$(dirname "$0")"

# Refresh the meter data. Keep the chatty progress quiet on success, but surface
# the full output and fail loudly if the pull errors — a nightly job serving
# stale data because a fetch silently failed is worse than no report.
pull_log="$(mktemp)"
trap 'rm -f "$pull_log"' EXIT
if ! python3 pull_usage.py >"$pull_log" 2>&1; then
    echo "water-report: data refresh failed (pull_usage.py):" >&2
    cat "$pull_log" >&2
    exit 1
fi

python3 water-report.py
