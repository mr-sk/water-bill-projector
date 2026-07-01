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

All config is at the top of `water-report.py`. You will need to edit:

- **`RATE_TIERS`** and **`SERVICE_CHARGE`** — the cumulative monthly rate schedule
  from your water bill. The values shipped here are an example. Each tier is
  `(upper_bound_in_CGL, dollars_per_CGL)`, where 1 CGL = 100 gallons.
- **`BILLING_CYCLE_START_DAY`** — the day of the month your cycle starts (default 21).
- **`SPRINKLER_DAYS`** / **`SPRINKLER_START`** / **`SPRINKLER_END`** — the days and
  time window you irrigate. Tip: look at your actual hourly deltas first and match
  the window to what the meter shows, not what the controller is set to.
- **`WATER_TZ`** (env) — your timezone, defaults to `America/New_York`.

By default `water-report.py` reads the newest `data/meter_*.csv`. Override with
`WATER_CSV=/path/to/file.csv` if you want a specific file.

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
short summary to Discord each night. That part is optional — the scripts above run
fine from plain `cron`. See [`docs/openclaw-cron.md`](docs/openclaw-cron.md) for the
agent/cron setup.

## License

MIT. See [LICENSE](LICENSE).
