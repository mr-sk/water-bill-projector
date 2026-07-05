"""Pure, importable billing + aggregation helpers for the water report.

Everything here is side-effect free (no I/O, no globals, no clocks) so the
billing math can be unit-tested directly. water-report.py wires these together
with the config and file loading.
"""
import calendar
from collections import defaultdict
from datetime import date


def gal_to_cgl(gallons):
    """Gallons -> CGL (hundreds of gallons)."""
    return gallons / 100.0


def cost_for_usage(cgl_used, cum_before_cgl, tiers):
    """$ cost of consuming `cgl_used` CGL starting from cumulative monthly
    position `cum_before_cgl`, under the marginal `tiers` schedule.

    `tiers` is a list of (upper_bound_CGL, rate_per_CGL) with the last bound
    being float("inf"). The cumulative base is clamped at 0 so a caller can
    never accidentally price usage from a negative position.
    """
    remaining = cgl_used
    pos = max(cum_before_cgl, 0.0)
    cost = 0.0
    for boundary, rate in tiers:
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


def current_tier(cgl_cum, tiers):
    """1-indexed tier number for a cumulative monthly CGL position."""
    for i, (boundary, _rate) in enumerate(tiers, start=1):
        if cgl_cum < boundary:
            return i
    return len(tiers)


def _clamp_day(year, month, day):
    """The requested day-of-month, clamped to the last valid day of that month."""
    return min(day, calendar.monthrange(year, month)[1])


def billing_cycle_bounds(d, start_day):
    """(start, end) dates of the billing cycle containing date `d`.

    The cycle is anchored to `start_day` of the month (start inclusive, end
    exclusive). `start_day` values that do not exist in a given month (29-31)
    are clamped to that month's last day, so a utility that bills on the 31st
    does not crash in February/April/June/September/November.
    """
    eff_start_this_month = _clamp_day(d.year, d.month, start_day)
    if d.day >= eff_start_this_month:
        y, m = d.year, d.month
    elif d.month == 1:
        y, m = d.year - 1, 12
    else:
        y, m = d.year, d.month - 1

    start = date(y, m, _clamp_day(y, m, start_day))
    ey, em = (y + 1, 1) if m == 12 else (y, m + 1)
    end = date(ey, em, _clamp_day(ey, em, start_day))
    return start, end


def hourly_deltas(rows):
    """Cumulative meter readings -> per-hour usage.

    `rows` is a time-sorted list of (datetime, reading). Usage is the difference
    between consecutive readings; negative deltas (meter resets / rollovers) are
    skipped rather than counted as usage.
    """
    hourly = []
    for i in range(1, len(rows)):
        dt, reading = rows[i]
        _, prev = rows[i - 1]
        delta = reading - prev
        if delta < 0:
            continue
        hourly.append((dt, delta))
    return hourly


def daily_totals(hourly):
    """Per-hour usage -> {date: total gallons}."""
    totals = defaultdict(float)
    for dt, g in hourly:
        totals[dt.date()] += g
    return totals


def hour_totals_for_date(hourly, d):
    """{hour: gallons} for date `d`, summing (not overwriting) duplicate hours.

    Duplicate local hours happen on DST fall-back and with vendor backfills; a
    dict comprehension keyed on the hour would silently drop all but the last.
    """
    totals = defaultdict(float)
    for dt, g in hourly:
        if dt.date() == d:
            totals[dt.hour] += g
    return totals


def cost_for_day(d, daily_total, tiers, start_day):
    """$ cost of date `d`'s usage, priced within `d`'s OWN billing cycle.

    Because pricing is cumulative, a day must be charged against the usage that
    preceded it *within its own cycle*. Deriving the base by subtracting from a
    later cycle's running total breaks at cycle boundaries (the day before a new
    cycle would be priced against the new, empty cycle).
    """
    cycle_start, _ = billing_cycle_bounds(d, start_day)
    cum_before = sum(g for day, g in daily_total.items() if cycle_start <= day < d)
    usage = daily_total.get(d, 0.0)
    return cost_for_usage(gal_to_cgl(usage), gal_to_cgl(cum_before), tiers)
