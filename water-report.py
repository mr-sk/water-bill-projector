#!/usr/bin/env python3
"""
water-report.py — Daily water usage report with tiered cost projection,
sprinkler-window detection, and month-to-date bill estimate.

Outputs structured text for lil-sis to summarize. All config at top.
"""
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Config ────────────────────────────────────────────────────────────
# Set to your local timezone (used for billing-cycle and sprinkler-window math).
TZ = ZoneInfo(os.getenv("WATER_TZ", "America/New_York"))

# The CSV written by pull_usage.py (data/meter_<id>.csv). By default we pick the
# most recent meter_*.csv in data/; override with WATER_CSV=/path/to/file.csv.
DATA_DIR = Path(__file__).parent / "data"
if os.getenv("WATER_CSV"):
    CSV_PATH = Path(os.environ["WATER_CSV"])
else:
    _found = sorted(DATA_DIR.glob("meter_*.csv"))
    CSV_PATH = _found[-1] if _found else DATA_DIR / "meter.csv"

# EDIT THESE FOR YOUR UTILITY.
# Tiered monthly rates, $/CGL where CGL = 100 gallons. Tiers are cumulative
# monthly thresholds: (upper_bound_CGL, rate_for_usage_within_this_tier).
# The values below are an example schedule — replace with your own from your bill.
RATE_TIERS = [
    (10,             0.7118),  # first 10 CGL  (0–1,000 gal)
    (30,             0.7472),  # next 20 CGL   (1,000–3,000 gal)
    (36,             0.9690),  # next 6  CGL   (3,000–3,600 gal)
    (float("inf"),   1.0172),  # 36+ CGL       (3,600+ gal)
]
SERVICE_CHARGE = 18.83  # flat per month

# Billing cycle. Many utilities bill on a fixed day-of-month, not the 1st.
BILLING_CYCLE_START_DAY = 21          # cycle runs the 21st -> 21st

# Sprinkler schedule. Set the days you water and the window to watch. Tip: read
# your actual hourly deltas first — a controller's clock is often not what you
# think, so match the window to what the meter shows, not what you set.
SPRINKLER_DAYS = {0, 2, 4}            # weekday ints: Mon=0 ... Sun=6 (here M/W/F)
SPRINKLER_START = time(3, 30)
SPRINKLER_END   = time(5, 30)

# Heuristic thresholds
NON_SPRINKLER_MORNING_SPIKE_GAL = 30  # any non-M/W/F morning hour >X gal => flag
SPRINKLER_DAY_MIN_GAL          = 30   # M/W/F sprinkler window <X gal => flag
TODAY_VS_AVG_MULTIPLIER         = 2.0 # today > Nx 7-day avg => flag

# ── Helpers ───────────────────────────────────────────────────────────
def cost_for_usage(cgl_used, cum_before_cgl):
    """$ cost of consuming `cgl_used` CGL starting from cumulative monthly position `cum_before_cgl`."""
    remaining = cgl_used
    pos = cum_before_cgl
    cost = 0.0
    for boundary, rate in RATE_TIERS:
        if pos >= boundary:
            continue
        room = boundary - pos
        used = min(remaining, room)
        cost += used * rate
        pos += used
        remaining -= used
        if remaining <= 1e-9:
            break
    return cost

def current_tier(cgl_cum):
    """Return 1-indexed tier number for the given cumulative monthly CGL position."""
    for i, (boundary, _rate) in enumerate(RATE_TIERS, start=1):
        if cgl_cum < boundary:
            return i
    return len(RATE_TIERS)

def gal_to_cgl(g):
    return g / 100.0

def in_sprinkler_window(dt):
    """True if the hourly delta at `dt` overlaps the sprinkler schedule.

    Flume reports a cumulative reading at top-of-hour; the delta at dt
    represents usage over [dt-1h, dt]. Sprinklers running 4:30-5:30 fall
    across two hourly buckets (dt=5:00 covers 4:00-5:00; dt=6:00 covers
    5:00-6:00), so we test the *bucket window* against the schedule.
    """
    if dt.weekday() not in SPRINKLER_DAYS:
        return False
    bucket_start = (dt - timedelta(hours=1)).time()
    bucket_end = dt.time()
    return max(bucket_start, SPRINKLER_START) < min(bucket_end, SPRINKLER_END)

# ── Load + bucket data ────────────────────────────────────────────────
if not CSV_PATH.exists():
    print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
    sys.exit(1)

rows = []
with open(CSV_PATH) as f:
    for r in csv.DictReader(f):
        dt = datetime.fromisoformat(r["date"]).astimezone(TZ)
        rows.append((dt, float(r["reading"])))
rows.sort(key=lambda x: x[0])

# Per-hour gallons used (cumulative reading delta).
hourly = []  # list of (dt, gal)
for i in range(1, len(rows)):
    dt, reading = rows[i]
    _, prev = rows[i-1]
    delta = reading - prev
    if delta < 0:
        continue
    hourly.append((dt, delta))

# Per-day totals
daily_total = defaultdict(float)
for dt, g in hourly:
    daily_total[dt.date()] += g

# ── Periods of interest ───────────────────────────────────────────────
now    = datetime.now(TZ)
today  = now.date()
yest   = today - timedelta(days=1)

# Billing cycle runs BILLING_CYCLE_START_DAY of one month to the same day of the
# next (start inclusive, end exclusive). On the start day itself a new cycle begins.
def billing_cycle_bounds(d):
    day = BILLING_CYCLE_START_DAY
    if d.day >= day:
        start = d.replace(day=day)
    elif d.month == 1:
        start = d.replace(year=d.year - 1, month=12, day=day)
    else:
        start = d.replace(month=d.month - 1, day=day)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1, day=day)
    else:
        end = start.replace(month=start.month + 1, day=day)
    return start, end

cycle_start, cycle_end = billing_cycle_bounds(today)
cycle_days_total = (cycle_end - cycle_start).days
cycle_day_of    = (today - cycle_start).days + 1   # 1-indexed: today is day N of the cycle
days_remaining_in_cycle = (cycle_end - today).days - 1  # excludes today

today_hours = {dt.hour: g for dt, g in hourly if dt.date() == today}
today_total = sum(today_hours.values())
today_sprinkler_gal = sum(g for dt, g in hourly if dt.date() == today and in_sprinkler_window(dt))
yest_total = daily_total.get(yest, 0.0)

# 7-day avg (prior 7 complete days; today excluded since it's in progress)
seven_day_dates = [today - timedelta(days=i) for i in range(1, 8)]
seven_day_avg = sum(daily_total.get(d, 0.0) for d in seven_day_dates) / 7

# Billing-cycle-to-date: sum from cycle_start through (today exclusive),
# then add today's running total.
bcd_before_today = sum(g for d, g in daily_total.items() if cycle_start <= d < today)
bcd_total        = bcd_before_today + today_total

# Projection: extrapolate current cycle's daily avg to remaining days
complete_days_so_far = cycle_day_of - 1   # full days completed before today
if complete_days_so_far > 0:
    avg_daily_so_far = bcd_before_today / complete_days_so_far
else:
    avg_daily_so_far = today_total or seven_day_avg
projected_cycle_gal = bcd_total + avg_daily_so_far * days_remaining_in_cycle

# ── Cost ──────────────────────────────────────────────────────────────
today_cost     = cost_for_usage(gal_to_cgl(today_total), gal_to_cgl(bcd_before_today))
bcd_before_yest = bcd_before_today - yest_total
yest_cost      = cost_for_usage(gal_to_cgl(yest_total), gal_to_cgl(bcd_before_yest))
bcd_usage_cost = cost_for_usage(gal_to_cgl(bcd_total), 0)
projected_usage_cost = cost_for_usage(gal_to_cgl(projected_cycle_gal), 0)
projected_bill = SERVICE_CHARGE + projected_usage_cost
tier_now = current_tier(gal_to_cgl(bcd_total))

# ── By-period breakdown today (overnight/morning/afternoon/evening) ──
periods = {"overnight": 0.0, "morning": 0.0, "afternoon": 0.0, "evening": 0.0}
for h, g in today_hours.items():
    if 0 <= h < 5:    periods["overnight"] += g
    elif 5 <= h < 12: periods["morning"]   += g
    elif 12 <= h < 18:periods["afternoon"] += g
    else:             periods["evening"]   += g

# ── Anomaly flags ─────────────────────────────────────────────────────
flags = []
expected_sprinkler_today = today.weekday() in SPRINKLER_DAYS

if today_total > TODAY_VS_AVG_MULTIPLIER * seven_day_avg and seven_day_avg > 0:
    flags.append(f"Today's {today_total:.0f} gal is {today_total/seven_day_avg:.1f}x the 7-day avg of {seven_day_avg:.0f}.")

# Big unexpected morning draw (possible leak) on a day nothing is scheduled.
# With no sprinkler schedule set, this runs every day as a plain leak check.
if not expected_sprinkler_today:
    tag = " on a non-watering day" if SPRINKLER_DAYS else ""
    for dt, g in hourly:
        if dt.date() == today and 4 <= dt.hour < 7 and g > NON_SPRINKLER_MORNING_SPIKE_GAL:
            flags.append(f"Morning spike {g:.0f} gal at {dt.hour:02d}:00{tag} (could be a leak, pool fill, or manual watering).")
            break

# Watering day but the window had almost no usage (only if a schedule is set)
if expected_sprinkler_today and now.hour >= 6 and today_sprinkler_gal < SPRINKLER_DAY_MIN_GAL:
    win = f"{SPRINKLER_START.strftime('%-H:%M')}-{SPRINKLER_END.strftime('%-H:%M')}"
    flags.append(f"Watering day but only {today_sprinkler_gal:.0f} gal during the {win} window; system may have failed to run.")

# ── Output ────────────────────────────────────────────────────────────
out = []
out.append(f"Water Report - {today.strftime('%a %b %d, %Y')} (as of {now.strftime('%H:%M %Z')})")
out.append("")
out.append(f"Today:       {today_total:6.1f} gal   ~${today_cost:.2f}")
out.append(f"Yesterday:   {yest_total:6.1f} gal   ~${yest_cost:.2f}")
out.append(f"7-day avg:   {seven_day_avg:6.1f} gal/day")
out.append("")
# Only show the sprinkler line if a watering schedule is configured.
if SPRINKLER_DAYS:
    _names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    days_label = "/".join(_names[d] for d in sorted(SPRINKLER_DAYS))
    window = f"{SPRINKLER_START.strftime('%-H:%M')}-{SPRINKLER_END.strftime('%-H:%M')}"
    sched_label = f"scheduled {days_label} {window}"
    out.append(f"Sprinklers today: {today_sprinkler_gal:.1f} gal "
               f"({sched_label if expected_sprinkler_today else 'not a watering day'})")
    out.append("")
out.append("By period today:")
out.append(f"  Overnight (00-05): {periods['overnight']:6.1f} gal")
out.append(f"  Morning   (05-12): {periods['morning']:6.1f} gal")
out.append(f"  Afternoon (12-18): {periods['afternoon']:6.1f} gal")
out.append(f"  Evening   (18-24): {periods['evening']:6.1f} gal")
out.append("")
cycle_label = f"{cycle_start.strftime('%-m/%-d')}-{cycle_end.strftime('%-m/%-d')}"
# Confidence band: ±10% of remaining-days extrapolation (early in cycle = wider).
# Tightens as cycle_day_of grows.
remaining_unknown_gal = avg_daily_so_far * days_remaining_in_cycle
band_gal = remaining_unknown_gal * 0.20  # ±20% on the extrapolated portion
proj_low_gal  = max(bcd_total, projected_cycle_gal - band_gal)
proj_high_gal = projected_cycle_gal + band_gal
proj_low_bill  = SERVICE_CHARGE + cost_for_usage(gal_to_cgl(proj_low_gal),  0)
proj_high_bill = SERVICE_CHARGE + cost_for_usage(gal_to_cgl(proj_high_gal), 0)

out.append(f"PROJECTED FULL-CYCLE BILL: ~${projected_bill:.2f}  (range ${proj_low_bill:.2f}-${proj_high_bill:.2f})")
out.append(f"   {projected_cycle_gal:.0f} gal projected total, cycle ends {cycle_end.strftime('%a %-m/%-d')}")
out.append("")
out.append(f"Billing cycle {cycle_label} (day {cycle_day_of} of {cycle_days_total}):")
out.append(f"  Cycle-to-date: {bcd_total:.0f} gal "
           f"({gal_to_cgl(bcd_total):.1f} CGL, in tier {tier_now}) - "
           f"~${bcd_usage_cost:.2f} usage + ${SERVICE_CHARGE:.2f} service = ${bcd_usage_cost + SERVICE_CHARGE:.2f}")
out.append("")
if flags:
    out.append("Flags:")
    for fl in flags:
        out.append(f"  - {fl}")
else:
    out.append("No anomalies flagged.")

print("\n".join(out))
