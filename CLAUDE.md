# Weather CZ/SK — project context

Internal weather dashboard for a ~10-person CZ/SK marketing team, built solo by
a Python/Django beginner (Adam). UI text is Czech; all code/comments English.

## Stack
Django + PostgreSQL (Railway, private networking, **Netherlands region — both
web and DB must stay in the same region**) + Plotly.js + Whitenoise.
Data: Open-Meteo (forecast, EC46/SEAS5 seasonal, ERA5 historical, CAMS).
Repo: `noxvex/weather-dashboard` (public). Live: `weather-dashboard-production-6c0f.up.railway.app`.

## Locked design system — do not change without explicit approval
- Colors: bg `#0A0E1A`, surface `#131C2E`, border `#243044`, text `#E8EDF5`,
  muted `#8A97AD`, accent blue `#4A9EFF`
- Horizon colors: `--h-short #4A9EFF` (blue), `--h-mid #FBBF24` (yellow),
  `--h-long #F472B6` (pink — was green, changed, do not revert)
- Reserved colors — never reuse for horizons: `--teal #2DD4BF` (system notes
  only), `--red #F87171` (leader notes / delta-up), `#A78BFA` (admin notes)
- Delta convention: warmer/up = red + ▲, cooler/down = teal + ▼, always show
  both °C and % together

## Workflow rules (strict — follow exactly)
1. Plan one phase in claude.ai first, lock spec, THEN build in Claude Code
2. One phase per Claude Code session — small verified steps, not big batches
3. Before changing anything, verify facts first (git diff, actual DB/API
   response, browser inspection) — never blind-fix code that "looks wrong"
4. If a requirement is ambiguous or data doesn't exist yet, ASK, don't invent
5. After any change: verify in browser (or via a real request), not just
   "code ran without error"
6. Django template comments `{# ... #}` must be SINGLE LINE ONLY — multiline
   ones render as literal visible text. Use `{% comment %}...{% endcomment %}`
   for multiline.
7. Claude Code has a built-in Git Safety Protocol that blocks direct
   `git push` to `main`/`master` — this is hardcoded and NOT overridable
   via .claude/settings.json permissions.allow, even with explicit user
   approval (confirmed: github.com/anthropics/claude-code/issues/22636).
   Don't fight this — work with it: at the end of every phase, after
   committing and pushing to a feature/worktree branch, ALWAYS run
   `gh pr create --fill` then `gh pr merge --squash --delete-branch --auto`
   yourself (gh CLI is authenticated as noxvex). Never leave a phase's
   work sitting unmerged on a branch — it will NOT reach Railway (which
   only deploys from `main`) until merged. Confirm after merging with
   `git log origin/main --oneline -3` that the commit actually landed
   on main, not just that gh reported success.
8. If `notes/tests.py` (or a `notes/tests/` package) exists, run
   `python manage.py test` before ending every phase and before merging —
   it's what catches a fix in one place silently breaking something else.

## Known gotchas (already debugged once — don't rediscover)
- Railway "private networking" only resolves `*.railway.internal` from
  WITHIN Railway's network. Local `railway run` can't reach it — use
  `railway ssh` to run management commands against production DB.
- `railway run` also can't share region latency issues away — if
  web and DB services are in different regions, every single query pays
  full cross-region round-trip (~140ms was measured US↔EU). Always confirm
  both services are in the same region.
- Whitenoise/static files: `collectstatic` must run in the `web:` Procfile
  line, not Pre-deploy Command (Pre-deploy runs in an ephemeral container,
  changes don't persist). Migrations correctly go in Pre-deploy Command.
- Plotly `<script>` tag must never have `defer` — causes charts to silently
  not render.
- ERA5 precipitation aggregates as SUM, temperature as MEAN — don't conflate.
- `ingest_weather` (short-range 16-day forecast) has no cron/worker service
  yet — must be triggered manually via `railway ssh` until a scheduled
  worker service is set up. If data looks stale/missing, check this first.

## Performance patterns to maintain
- Avoid duplicate DB round-trips across helper functions in the same view
  (e.g. don't re-query DailyForecast in 3 different helpers when one shared
  fetch works) — every query pays full network latency even on fast networks.
- Cache computed data that changes at most once/day (seasonal SEAS5/EC46
  data, `.exists()` checks) via Django's cache framework, not per-request.
- DB indexes exist on: WeatherPoint.country, HistoricalActual(point,date),
  HistoricalActual.date, DailyForecast(point,forecast_date,horizon),
  DailyForecast.issued_at, MediumLongRangeForecast(point,target_date,horizon,issued_at).
  Add matching indexes for any new frequent filter/sort field.

## Model choice for Claude Code sessions
- Multi-file builds, wide-scope fixes, anything with real regression risk →
  **Fable, highest effort** (confirmed more reliable in practice than
  Sonnet for this, even though slower/costlier — fewer follow-up fix rounds)
- Logic/data plumbing/backend/git/ops, single clearly-scoped fix → Sonnet
- Visual/color/chart aesthetic work → Opus high effort, or Fable if scope
  is wide

## Current status (update this section as phases complete)
DONE (verified live on production):
- Horizon colors + readability (badges, borders, delta % everywhere)
- Bod page CZ/SK country selector with expandable city lists
- Aktuality/Bod filters (time range, horizon, country)
- DB indexes (WeatherPoint.country, HistoricalActual, DailyForecast,
  MediumLongRangeForecast) + seasonal-data caching + shared query helpers
- Fixed multiline Django comment bug (was rendering as visible text)
- Migrated Postgres to Netherlands region to match web service (was
  cross-region US↔EU, caused ~140ms latency per query)
- Fixed hardcoded fc-fill temperature bar (now reflects real min/max)
- Added 4th "Vlastní" (custom year/week) tab to Bod's horizon switcher
- `ingest_weather` confirmed working when run manually via `railway ssh`
  (still no scheduled cron/worker service — see NEXT UP)
- gh CLI authenticated (noxvex) — Claude Code's built-in Git Safety
  Protocol blocks direct push to main; workflow is now: feature branch →
  `gh pr create --fill` → `gh pr merge --squash --delete-branch --auto`
  (documented in workflow rules above)
- FÁZE 4 stabilization (PR #2): Historie custom-range `current_year`
  anchor fixed (determined independently of the doy filter), Bod CZ/SK
  accordions default to closed, first regression tests in notes/tests.py
  (historie custom-range year filtering, point_detail routing, fc-fill)
- ERA5 historical backfill extended to 2015-01-01 (was 2 years) — 92,554
  HistoricalActual rows across all 22 points as of 2026-07-13; the
  archive-api free tier rate-limits hard around ~150 requests per run, so
  a full re-backfill needs 2-3 passes (safe/idempotent via
  `ignore_conflicts=True`) rather than one shot.
- Historie: current-year overlay continues as a dashed forecast segment
  (temp: short 16-day + EC46 + SEAS5 merged per date, short wins on
  overlap, then EC46; precip: short-range only — MLR has just
  precip_probability). Works in plna + vlastni, abs mode only (skipped
  for rezim=pct). Raw MLR fetch cached 1h (`_get_mlr_forecast_rows`);
  aggregation mirrors `_historical_series` rules. Note: ERA5 lags ~5
  days behind today, so the dashed connector visibly bridges that gap —
  expected, not a bug. Tests: HistorieForecastOverlayTest.

NOT YET DONE / KNOWN BROKEN (going into next session):
- No Railway cron/worker service yet for `ingest_weather` — still manual
  via `railway ssh`. Needs a scheduled worker service set up.

## Priority order (revised — Bod deprioritized, Revize + pins now ahead
## of remaining original Bod/detail polish items)

1. **FÁZE 4 — stabilization** — DONE (PR #2): Historie custom-range year
   bug fixed, Bod accordion default-closed, minimal regression tests in
   notes/tests.py.
2. **Revize expansion** — currently only shows the nearest day or two
   for CZ. Needs to cover the full available future range (short-range
   16 days + medium-range), not just tomorrow — this is more useful for
   the marketing team's actual planning horizon than Bod page detail work.
3. **Historie "pin" annotations** (scoped-down from the original graph-pin
   idea — much lower risk than initially assessed):
   - Reuses the EXISTING manual-comparison form on Historie (od/do/roky/
     bod/metric) — no new pixel-click-on-graph interaction needed
   - User configures a comparison (date range, years, metric: temp abs/%,
     precipitation; air quality metric later once that data exists)
   - "Add pin" button saves it as a Note (existing model) tagged with
     those parameters + author name, default pre-filled comment text
     ("Kouknětě na tyhle data...", user edits it)
   - Pin appears on the Historie graph itself (marker/annotation, NOT a
     live embedded chart) and optionally cross-posts to Aktuality feed
     as a note with a deep link back to Historie with those exact params
   - Aktuality card shows TEXT SUMMARY only (e.g. "Duben–červenec,
     posledních 5 let, průměr 18°C") — no embedded graph in the feed,
     keep it simple first
4. Remaining original "Bod/detail" polish (larger temp numbers, resize
   sections, scrollable table) — lower priority now, Bod itself matters
   less than short/medium-range change tracking per user's actual usage
5. Data expansion (pollen, air quality — distinguish gases/dust/pollen,
   UV, wind days, pressure) — unblocks air-quality as a pin metric later
6. Toggleable display layers
7. Mobile tweaks — last

Session B (not started): SMTP email, anomaly detection (medium-range
forecast vs. ERA5 baseline), daily 9:00 Prague digest with opt-in.