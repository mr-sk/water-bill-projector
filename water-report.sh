#!/usr/bin/env bash
# water-report.sh — Refresh Flume data and print the daily water report.
set -euo pipefail
cd "$(dirname "$0")"
python3 pull_usage.py > /dev/null 2>&1
python3 water-report.py
