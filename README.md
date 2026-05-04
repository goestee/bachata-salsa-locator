# DFW Bachata & Salsa Locator

A small Python aggregator that finds Salsa, Bachata (and adjacent Latin)
events across the Dallas–Fort Worth metroplex and writes a tidy
[`EVENTS.md`](EVENTS.md) you can read at a glance.

It runs automatically **twice a day** via GitHub Actions and only **adds**
events as it discovers them — already-known events are kept, past events are
pruned, and new ones are flagged in a "New since last run" section at the
top of `EVENTS.md`.

---

## Sources

| Source | How it's accessed | Notes |
|---|---|---|
| **Eventbrite** | Public city/keyword listing pages, JSON-LD parse | No public search API since 2019 |
| **Meetup** | Public website search SSR, JSON-LD parse | Their official API is now Pro-only |
| **DanceUS.org** | Public DFW calendar pages, JSON-LD parse | |
| **GoLatinDance** | iCal feed (preferred) → HTML JSON-LD fallback | DFW-specific |
| **SalsaVida** | DFW calendar page, JSON-LD parse | |
| **SalsaDallas** | Calendar page, JSON-LD parse | |
| **Studio22 Dallas** | Special events page, JSON-LD parse | |
| **Instagram** | Anonymous, **once-daily max** (20h cool-down) | Curated public accounts only — surfaces posts as advisories with a link, doesn't try to parse exact dates |

> **No Facebook scraping.** Their ToS prohibits it, their public event search
> was removed in 2018, and an hourly scraper would be banned within days. The
> sources above already cover most events that get cross-posted to FB anyway.

## Quick start (run it locally)

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# one-off run
python -m src.main

# only run a couple of sources, useful while iterating
python -m src.main --only=eventbrite,golatindance --verbose

# don't write any files
python -m src.main --dry-run
```

The output lives in [`EVENTS.md`](EVENTS.md). Persistent state (so we know
what's "new") is in `data/events.json`.

## Run it on a schedule (recommended: GitHub Actions)

The repo includes [`.github/workflows/update-events.yml`](.github/workflows/update-events.yml)
which runs at 13:00 UTC (~8 AM Central) and 01:00 UTC (~8 PM Central) and
commits any changes back to the repo.

To enable:

1. Push this repo to GitHub.
2. Settings → Actions → General → **Workflow permissions** → set to
   "Read and write permissions".
3. The cron will start running automatically. You can also hit
   "Run workflow" in the **Actions** tab any time.

### Want it on your PC instead?

Use Windows Task Scheduler:

```powershell
# in an Administrator PowerShell, after activating the venv at least once
$action = New-ScheduledTaskAction `
  -Execute "C:\Users\teego\Desktop\Bachata and Salsa Locator\.venv\Scripts\python.exe" `
  -Argument "-m src.main" `
  -WorkingDirectory "C:\Users\teego\Desktop\Bachata and Salsa Locator"

$trigger1 = New-ScheduledTaskTrigger -Daily -At 8am
$trigger2 = New-ScheduledTaskTrigger -Daily -At 8pm

Register-ScheduledTask -TaskName "DFW Dance Aggregator" `
  -Action $action -Trigger @($trigger1, $trigger2)
```

GitHub Actions is recommended because it works even when your PC is off.

## Configuration

All knobs live in [`config.yaml`](config.yaml). The most useful edits:

- `sources.instagram.accounts` — add/remove DFW dance Instagram handles you
  follow. Keep the list small (≤ 15) for IG's sake.
- `keywords.must_match_any` — broaden or narrow the dance-style filter.
- `geo.cities` — add suburbs.
- `output.horizon_days` — how far ahead `EVENTS.md` looks.
- Per-source `enabled: true/false` to toggle a noisy source off.

## How it works (under the hood)

```
config.yaml ─► main.py ─► [each source.fetch()] ─► raw events
                                       │
                                       ▼
                          filters.py (DFW + dance keyword)
                                       │
                                       ▼
                       storage.py  (dedupes via stable hash;
                       events.json  tracks first_seen_at,
                                    prunes past events)
                                       │
                                       ▼
                          render.py  ─►  EVENTS.md
```

Dedup uses a stable ID hashed from `(title, date, venue/city)`, so the same
event posted to Eventbrite *and* DanceUS won't appear twice.

## Limitations & honest caveats

- **Scrapers break.** Sites change HTML/JSON-LD structure occasionally. Each
  source is isolated, so one broken source doesn't fail the whole run — check
  the action logs and patch the affected `src/sources/*.py`.
- **Meetup access is shaky.** Their API is paywalled; the public-search SSR
  page may add anti-bot protections. If Meetup goes dark, disable it in
  config.
- **Instagram is best-effort.** Anonymous IG scraping is fragile. The IG
  source surfaces *advisory* posts (with a link), not parsed dates. Treat the
  IG entries as "go check this post yourself."
- **No Facebook.** See above. If a venue only posts on Facebook, you can
  manually add their RSS/Eventbrite/calendar URL to `config.yaml` instead.
- **Be respectful.** The default polite delay between requests (1.0–2.5s) is
  intentional. Don't set the cron to run more than a few times a day.

## Adding a new source

For most sites you can just add their calendar URL to one of the existing
generic blocks in `config.yaml`:

```yaml
sources:
  studio22:
    enabled: true
    url: https://studio22dallas.com/calendar-classes/special-events/
    # add more URLs here, or copy this block under a new source name and
    # wire it up in src/main.py::_build_sources
```

For something more bespoke (a custom HTML structure, an authenticated API,
etc.) drop a new module in `src/sources/` that subclasses `BaseSource` and
implements `fetch()`.
