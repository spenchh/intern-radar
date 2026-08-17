# Intern Radar

A 24/7 monitor for **hardware / silicon / hardware-aerospace internships**.
It polls ~50 employer job boards plus a large aggregate feed every 15 minutes,
filters for the roles you actually want, pushes a Discord alert the moment one
opens, and publishes a searchable dashboard.

Software-engineering roles are deliberately filtered out.

**What it currently tracks:** 90 open roles across 51 sources, including
Marvell *Digital Logic + Design Verification Co-op*, Etched *RTL / DFT Intern*,
NXP and Intel *Physical Design*, SpaceX *Silicon Engineer*, Astera Labs
*Analog Mixed-Signal Layout*, Rocket Lab *Avionics Hardware*, and the FPGA desks
at Jane Street, Jump, DRW, Optiver, Citadel and IMC.

---

## How it works

```
Simplify aggregate feed  ─┐
Greenhouse boards (30)   ─┤
Lever boards (8)         ─┼──►  match & score  ──►  new?  ──►  Discord alert
Ashby boards (11)        ─┘         │                 │
                                    └──────────────────┴──►  docs/ dashboard
```

Two tiers of source, fetched concurrently in ~7 seconds:

- **Employer ATS APIs** (Greenhouse / Lever / Ashby) are the *fastest* signal.
  A posting appears there the instant it goes live, typically hours before any
  aggregator indexes it. All 49 company slugs were verified live.
- **The Simplify feed** provides breadth — thousands of roles across companies
  that don't expose a public board.

Every posting is scored against a weighted hardware vocabulary. Alerts fire
once per role: a `data/seen.json` fingerprint collapses the same job arriving
from multiple sources, so you never get notified twice.

> **A note on `intern-list.com`:** the site you shared renders its listings from
> a private `jobright.ai` endpoint inside an iframe. Scraping it would break the
> first time they change their markup, and it's on shaky ToS ground. The sources
> above are public, documented, structured, and read straight from the employer —
> strictly better data, and faster.

---

## Setup

### 1. Create the Discord webhook

In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick a
channel, then **Copy Webhook URL**. It looks like
`https://discord.com/api/webhooks/123.../abc...`.

Treat it as a password — anyone with it can post to your channel.

### 2. Try it locally

```powershell
cd C:\Users\HP\intern-radar
pip install -r requirements.txt

# See what matches, send nothing:
python -m intern_radar.main --dry-run --explain

# Send the current matches to Discord to confirm the webhook works:
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
python -m intern_radar.main --backfill --limit 5
```

Then open `docs/index.html` in your browser to see the dashboard.

### 3. Put it in the cloud so it runs with your PC off

```powershell
cd C:\Users\HP\intern-radar
git init -b main
git add .
git commit -m "Intern Radar"
git remote add origin https://github.com/<you>/intern-radar.git
git push -u origin main
```

**Make the repository public.** Public repos get unlimited GitHub Actions
minutes; private repos are capped at 2,000/month, which a 15-minute schedule
would exhaust. Nothing sensitive is committed — the webhook lives in a secret.

Then, in the repo settings:

| Where | What |
|---|---|
| **Settings → Secrets and variables → Actions → New repository secret** | Name `DISCORD_WEBHOOK_URL`, value = your webhook URL |
| **Settings → Pages → Source** | Deploy from a branch → `main` → `/docs` |
| **Actions tab** | Enable workflows if prompted |

Your dashboard goes live at `https://<you>.github.io/intern-radar/`.

To confirm it works end to end: **Actions → Intern Radar → Run workflow**.

### 4. Optional — run locally too, for tighter polling

`run_local.ps1` registers a Windows scheduled task that polls every 5 minutes
while your PC is on. Safe to use alongside the cloud runner.

```powershell
.\run_local.ps1 -WebhookUrl "https://discord.com/api/webhooks/..."
.\run_local.ps1 -Remove   # uninstall
```

---

## Tuning what you get alerted about

Everything lives in `config.yaml`. After any edit, preview the effect *without*
sending anything:

```powershell
python -m intern_radar.main --dry-run --explain
python audit.py             # every current match, grouped by category
python audit.py rejected    # near-misses, to catch anything good being dropped
python selftest.py          # 40 checks on matching, dedup and payload limits
```

**Too many alerts?** Raise `matching.min_score` from `2` to `3` or `4`.
**Missing roles?** Lower it to `1`, or add terms under `matching.weights`.
**Wrong roles slipping through?** Add the phrase to `matching.veto`.

Filters worth knowing about:

- `allowed_years` — currently `[2026, 2027, 2028]`. Postings that name no year
  are always kept, since most ATS titles omit it.
- `allowed_locations` / `excluded_locations` — both empty, so you see
  everything. There are a lot of Rocket Lab Auckland roles in the feed; to drop
  them, set `excluded_locations: [auckland, new zealand]`. To go US-only, set
  `allowed_locations: [united states, usa, remote]`.
- `exclude_sponsorship` — empty. Many hardware and defense roles are
  ITAR-restricted and require US citizenship; the status is shown on every alert
  rather than filtered, so you can judge case by case.

### Adding a company

Find its board slug from the careers-page URL and add it to the right list in
`config.yaml`:

| ATS | Careers URL looks like | Slug |
|---|---|---|
| Greenhouse | `job-boards.greenhouse.io/**acme**` | `acme` |
| Lever | `jobs.lever.co/**acme**` | `acme` |
| Ashby | `jobs.ashbyhq.com/**acme**` | `acme` |

Verify it before committing:

```powershell
python -c "from intern_radar.sources import fetch_greenhouse as f; print(len(f('acme')))"
```

Large semiconductor firms (NVIDIA, AMD, Intel, Qualcomm, Apple, Tesla) run
Workday, which has no clean public API — they're covered through the Simplify
feed instead, which is why Intel, Apple, Tesla and SpaceX roles still appear.

---

## Files

| Path | Purpose |
|---|---|
| `config.yaml` | All tuning: keywords, weights, vetoes, companies, filters |
| `intern_radar/sources.py` | Feed and ATS clients |
| `intern_radar/matcher.py` | Scoring and filtering |
| `intern_radar/store.py` | Seen-postings state, so alerts fire once |
| `intern_radar/notify.py` | Discord webhook delivery |
| `intern_radar/dashboard.py` | Static dashboard generator |
| `data/seen.json` | Fingerprints of everything already alerted |
| `docs/index.html` | The dashboard (GitHub Pages serves this) |
| `docs/jobs.json` | Same data as JSON, for any tooling you add |

### Command reference

```
python -m intern_radar.main                  normal run
                             --dry-run       change nothing, send nothing
                             --explain       show the match/reject breakdown
                             --backfill      alert on every current match
                             --limit N       cap alerts this run
                             --no-dashboard  skip regenerating docs/
                             -v              debug logging
```

---

## Notes

- **`data/seen.json` ships pre-seeded** with the 90 roles currently open. They
  appear on the dashboard but won't alert, so your first cloud run is quiet and
  you only hear about genuinely new postings. Want the current batch pushed to
  Discord anyway? Run with `--backfill`.
- **Alerts are capped** at 25 per run (`notify.max_per_run`). Anything over the
  cap is left unmarked and goes out on the next run rather than being dropped.
- **Failures are loud.** If a run errors, the workflow posts to the same Discord
  channel with a link to the logs. If every source fails, the run aborts without
  writing state, so a network blip can't wipe your history or re-alert you.
- **Scheduling is best-effort.** GitHub queues cron jobs, so the real gap is
  often 15–25 minutes rather than exactly 15. Run locally too if you want tighter.
