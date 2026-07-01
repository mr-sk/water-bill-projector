# Running the report nightly (OpenClaw + local model)

The scripts in this repo run fine from plain `cron`. This doc covers the setup from
the writeup: a self-hosted [OpenClaw](https://github.com/openclaw/openclaw) agent,
backed by a local model, that runs the report each night and posts a short summary
to Discord.

## Plain cron (simplest)

```cron
# every night at 21:00, refresh + print the report to a log
0 21 * * *  cd /path/to/water-bill-projector && ./water-report.sh >> water.log 2>&1
```

## The local model

In the writeup the summarizing step is a local, on-device model rather than a cloud
API, so no usage data leaves the machine. Any OpenAI-compatible local server works.
On Apple Silicon, [MLX](https://github.com/ml-explore/mlx) serves one over an
OpenAI-compatible endpoint:

```bash
# example: serve a local model with mlx_lm's OpenAI-compatible server
pip install mlx-lm
mlx_lm.server --model <a-local-model> --port 8080
```

A mixture-of-experts model with a few billion active parameters is a good fit here:
these reports are short agentic loops, and wall-clock per turn matters more than raw
model size.

## The OpenClaw cron job

Register a scheduled job that runs `water-report.sh` and asks the agent to summarize
the output to your chat channel. The prompt below is the one used in the writeup
(fill in your own channel):

```
Run: bash /path/to/water-bill-projector/water-report.sh
Summarize for Discord in 6-8 lines. The script prints a
"PROJECTED FULL-CYCLE BILL" line with a point estimate + range — surface that as
the FIRST line of the post, prominently. Then cover: today gallons + cost,
yesterday gallons + cost, sprinkler activity if it's a watering day, cycle-to-date
dollars and gallons, and any anomaly flags. Be concise.
```

Job settings used in the writeup:

- schedule: `0 21 * * *`, timezone `America/New_York`
- timeout: 300s
- delivery: announce to your Discord channel

Consult the OpenClaw docs for the exact command to register a cron job and bind a
Discord channel in your version. The bot token and channel binding live in your
OpenClaw config / environment — never commit them.
