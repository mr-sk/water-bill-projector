# water-bill-projector

Reads a smart water meter every night and projects the full billing-cycle bill
before it arrives. It models tiered, cumulative pricing (so today's gallons are
priced at whatever tier the month has already reached), projects the cycle total
with a confidence band, detects sprinkler runs, and flags anomalies like spikes
and possible leaks.

Full writeup: https://skheavyindustries.com/blog/what-does-my-water-cost.html

The meter is read through [Eye on Water](https://eyeonwater.com) using the
[`pyonwater`](https://github.com/kdeyev/pyonwater) client, so this works for any
utility on the Eye on Water platform (e.g. Liberty Utilities).

## What's here

| File | Purpose |
|------|---------|
| `pull_usage.py` | Authenticates to Eye on Water and pulls ~90 days of hourly readings into `data/` (CSV + JSON). |
| `water-report.py` | Turns the readings into a daily report: tiered cost, projected bill + range, sprinkler detection, anomaly flags. |
| `water-report.sh` | Convenience wrapper: refresh the data, then print the report. |
| `docs/openclaw-cron.md` | How to run it nightly and post the summary to Discord via an [OpenClaw](https://github.com/openclaw/openclaw) agent (optional). |

## Setup

```bash
cp .env.example .env        # then edit .env with your Eye on Water login
pip install -r requirements.txt
python pull_usage.py        # writes data/meter_<id>.csv and .json
python water-report.py      # prints the report
```

Or run both steps at once:

```bash
./water-report.sh
```

## Configure it for your utility

All of the tunable config lives in one block at the top of `water-report.py`. The
values shipped here are an example (a Liberty Utilities schedule). You will get
wrong numbers until you replace them with your own. Here is what each one is and
where to find it.

### 1. Rate schedule: `RATE_TIERS` and `SERVICE_CHARGE`

This is the part that has to be right, and it comes straight off your water bill.

Most utilities bill water on a **tiered, cumulative monthly** schedule: the first
chunk of usage is cheap, and each further chunk is more expensive. The rate for a
tier applies only to the portion of the month's usage that falls inside that tier
(marginal pricing), which is why this project exists.

`RATE_TIERS` is a list of `(upper_bound_in_CGL, dollars_per_CGL)` pairs, where
**1 CGL = 100 gallons**. Boundaries are cumulative monthly totals, and the last
tier is open-ended with `float("inf")`.

To fill it in:

1. On your bill, find the "rate schedule", "tiered rates", or "consumption charge"
   table. It lists each tier's usage range and its price.
2. Convert each tier's upper bound to CGL (hundreds of gallons) and each price to
   dollars per CGL.
3. Put the fixed monthly charge (called "service", "base", "meter", or "readiness"
   charge) into `SERVICE_CHARGE`.

Example. If your bill reads:

```
Service charge:            $18.83 / month
First 1,000 gallons:       $0.7118 per 100 gal
Next 2,000 gallons:        $0.7472 per 100 gal
Next 600 gallons:          $0.9690 per 100 gal
Over 3,600 gallons:        $1.0172 per 100 gal
```

then, since 1,000 gal = 10 CGL, 3,000 gal = 30 CGL, 3,600 gal = 36 CGL:

```python
RATE_TIERS = [
    (10,           0.7118),
    (30,           0.7472),
    (36,           0.9690),
    (float("inf"), 1.0172),
]
SERVICE_CHARGE = 18.83
```

Special cases:

- **Flat rate** (no tiers): use a single open-ended tier, e.g.
  `RATE_TIERS = [(float("inf"), 0.85)]`.
- **Different units.** This project assumes your meter reports **gallons** and
  prices are per 100 gallons. If your meter or bill uses cubic feet, CCF
  (100 cubic feet = ~748 gallons), or liters, convert your rates into dollars per
  100 gallons first, or adjust the `gal_to_cgl` helper. Check the `unit` column in
  the CSV that `pull_usage.py` writes to confirm what your meter reports.
- **Sewer/stormwater charges.** Many bills add volumetric sewer fees on top of
  water. If you want the projection to match your total bill, fold those rates into
  `RATE_TIERS` or add them to `SERVICE_CHARGE`.

Sanity check: after editing, run `python water-report.py` and compare the
"Cycle-to-date" cost against a recent real bill for the same usage. If it matches,
your tiers are right.

### 2. Billing cycle: `BILLING_CYCLE_START_DAY`

Your cycle usually is not the calendar month. Find the "service period" or "billing
period" dates on your bill and set this to the start day of month (for example, a
cycle that runs the 21st to the 21st is `21`; a calendar-month utility is `1`).
This assumes a monthly cycle anchored to a fixed day. Bimonthly billing is not
handled out of the box.

### 3. Sprinkler window: `SPRINKLER_DAYS`, `SPRINKLER_START`, `SPRINKLER_END`

Used to confirm irrigation actually ran and to flag missed runs. `SPRINKLER_DAYS`
is a set of weekday integers (`Mon=0 ... Sun=6`); the shipped example `{0, 2, 4}`
is Monday/Wednesday/Friday. `SPRINKLER_START` and `SPRINKLER_END` are the clock
window to watch.

Set the window from the **data, not the controller**. After a data pull, look at
the early-morning rows in `data/meter_*.csv` on a watering day and find the hours
with large deltas. A controller's clock is often off from what you set, so match
the window to what the meter shows. A quick look:

```bash
# show the biggest hourly jumps so you can spot the irrigation window
python - <<'PY'
import csv, glob
rows = list(csv.DictReader(open(sorted(glob.glob("data/meter_*.csv"))[-1])))
prev = None
for r in rows:
    cur = float(r["reading"])
    if prev is not None:
        d = cur - prev
        if d > 20:                      # gallons; tune the threshold
            print(r["date"], round(d), "gal")
    prev = cur
PY
```

**No sprinklers?** Set `SPRINKLER_DAYS = set()`. The report then drops the
"Sprinklers today" line and the missed-run flag entirely. The morning-spike leak
check still runs every day (a large pre-dawn draw is flagged as a possible leak),
which is exactly what you want without irrigation. Everything else (cost, projection,
spike detection) is unaffected. The `SPRINKLER_START` / `SPRINKLER_END` values are
ignored when no days are set.

### 4. Timezone: `WATER_TZ` (environment variable)

An IANA timezone name (default `America/New_York`). Set it to your local zone so
the hourly buckets, billing cycle, and sprinkler window line up with real time:

```bash
WATER_TZ="America/Chicago" python water-report.py
```

### 5. Anomaly thresholds (optional)

Tune these to your household to control the flags at the bottom of the report:

- `NON_SPRINKLER_MORNING_SPIKE_GAL` - a morning hour above this many gallons on a
  non-watering day is flagged as a possible leak/pool-fill/manual watering.
- `SPRINKLER_DAY_MIN_GAL` - a watering day with less than this in the window is
  flagged as a possible missed run.
- `TODAY_VS_AVG_MULTIPLIER` - today above this multiple of the 7-day average is
  flagged as a spike.

### 6. Which CSV it reads: `WATER_CSV` (optional)

By default `water-report.py` reads the newest `data/meter_*.csv`. Point it at a
specific file with `WATER_CSV=/path/to/file.csv`.

## How the projection works

- Usage is the difference between consecutive cumulative meter readings (negative
  deltas from resets/rollovers are skipped).
- Cost uses marginal tier pricing: the cost of a block of gallons depends on where
  you already sit in the month's cumulative usage.
- The projection extends the cycle's daily average across the remaining days. The
  confidence band is applied only to the extrapolated (unknown) portion, so it is
  wide early in the cycle and collapses toward the real number by the end.

## Running it nightly with a local AI agent

The version in the writeup runs on a self-hosted [OpenClaw](https://github.com/openclaw/openclaw)
agent backed by a local model on a Mac Studio, which reads the report and posts a
short summary to Discord each night. That part is optional. The scripts above run
fine from plain `cron`. See [`docs/openclaw-cron.md`](docs/openclaw-cron.md) for the
agent/cron setup.

## License

MIT. See [LICENSE](LICENSE).
A product of [sk @ skheavyindustries.com](https://skheavyindustries.com)
