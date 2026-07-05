#!/usr/bin/env python3
"""Unit tests for the pure billing/aggregation helpers in billing.py.

Zero external dependencies: run with either

    python3 tests/test_billing.py
    python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing  # noqa: E402

# The example schedule shipped in water-report.py.
TIERS = [
    (10, 0.7118),
    (30, 0.7472),
    (36, 0.9690),
    (float("inf"), 1.0172),
]
TZ = ZoneInfo("America/New_York")


class CostForUsage(unittest.TestCase):
    def test_marginal_cost_from_zero(self):
        # 46 CGL from an empty month walks all four tiers.
        self.assertAlmostEqual(billing.cost_for_usage(46, 0, TIERS), 38.048, places=3)

    def test_marginal_cost_spanning_a_boundary(self):
        # 9 -> 11 CGL: 1 CGL in tier 1, 1 CGL in tier 2.
        self.assertAlmostEqual(billing.cost_for_usage(2, 9, TIERS), 1.459, places=3)

    def test_zero_usage_costs_nothing(self):
        self.assertEqual(billing.cost_for_usage(0, 5, TIERS), 0.0)


class CurrentTier(unittest.TestCase):
    def test_tiers_by_position(self):
        self.assertEqual(billing.current_tier(5, TIERS), 1)
        self.assertEqual(billing.current_tier(15, TIERS), 2)
        self.assertEqual(billing.current_tier(33, TIERS), 3)
        self.assertEqual(billing.current_tier(40, TIERS), 4)


class BillingCycleBounds(unittest.TestCase):
    def test_common_day21_cycle(self):
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 7, 21), 21),
            (date(2026, 7, 21), date(2026, 8, 21)),
        )
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 7, 20), 21),
            (date(2026, 6, 21), date(2026, 7, 21)),
        )

    def test_year_boundary(self):
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 1, 5), 21),
            (date(2025, 12, 21), date(2026, 1, 21)),
        )

    # F-001: a fixed cycle day that does not exist in every month must not crash.
    def test_day31_in_short_month_does_not_crash(self):
        # April has 30 days; mid-month sits in the Mar-31 -> Apr-30 cycle.
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 4, 15), 31),
            (date(2026, 3, 31), date(2026, 4, 30)),
        )

    def test_day31_on_clamped_start_day(self):
        # Apr 30 is the clamped start of the Apr -> May cycle.
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 4, 30), 31),
            (date(2026, 4, 30), date(2026, 5, 31)),
        )

    def test_day31_february_non_leap(self):
        self.assertEqual(
            billing.billing_cycle_bounds(date(2026, 2, 15), 31),
            (date(2026, 1, 31), date(2026, 2, 28)),
        )

    def test_day29_leap_february(self):
        self.assertEqual(
            billing.billing_cycle_bounds(date(2024, 2, 29), 31),
            (date(2024, 2, 29), date(2024, 3, 31)),
        )


def _dt(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=TZ)


class HourlyDeltas(unittest.TestCase):
    def test_skips_negative_delta_resets(self):
        rows = [
            (_dt(2026, 7, 1, 0), 100.0),
            (_dt(2026, 7, 1, 1), 110.0),
            (_dt(2026, 7, 1, 2), 105.0),  # reset / rollover -> negative delta
            (_dt(2026, 7, 1, 3), 115.0),
        ]
        out = billing.hourly_deltas(rows)
        self.assertEqual([g for _, g in out], [10.0, 10.0])


class HourTotalsForDate(unittest.TestCase):
    # F-003: duplicate local hours must be summed, not overwritten.
    def test_duplicate_hours_are_summed(self):
        hourly = [
            (_dt(2026, 7, 1, 9), 2.0),
            (_dt(2026, 7, 1, 9), 5.0),
            (_dt(2026, 7, 1, 9), 7.0),
            (_dt(2026, 7, 1, 10), 3.0),
        ]
        totals = billing.hour_totals_for_date(hourly, date(2026, 7, 1))
        self.assertEqual(totals[9], 14.0)
        self.assertEqual(totals[10], 3.0)

    def test_only_the_requested_date(self):
        hourly = [
            (_dt(2026, 7, 1, 9), 2.0),
            (_dt(2026, 7, 2, 9), 5.0),
        ]
        totals = billing.hour_totals_for_date(hourly, date(2026, 7, 1))
        self.assertEqual(dict(totals), {9: 2.0})


class CostForDay(unittest.TestCase):
    # F-002: a day is priced within ITS OWN billing cycle, so the day-before at a
    # cycle boundary is not charged against the new (empty) cycle's tier 1.
    def test_yesterday_priced_in_its_own_cycle_at_boundary(self):
        # start_day 21. 7/19 and 7/20 belong to the 6/21 -> 7/21 cycle.
        daily = {
            date(2026, 7, 19): 1000.0,  # 10 CGL already used this cycle
            date(2026, 7, 20): 500.0,   # 5 CGL, should price in tier 2
            date(2026, 7, 21): 300.0,   # first day of the NEXT cycle
        }
        # 5 CGL starting from cumulative 10 CGL -> all tier 2: 5 * 0.7472.
        self.assertAlmostEqual(
            billing.cost_for_day(date(2026, 7, 20), daily, TIERS, 21),
            5 * 0.7472,
            places=4,
        )

    def test_cumulative_base_never_negative(self):
        daily = {date(2026, 7, 21): 300.0}
        # First day of a cycle: nothing before it, base is 0 (not negative).
        cost = billing.cost_for_day(date(2026, 7, 21), daily, TIERS, 21)
        self.assertAlmostEqual(cost, billing.cost_for_usage(3.0, 0, TIERS), places=6)


if __name__ == "__main__":
    unittest.main()
