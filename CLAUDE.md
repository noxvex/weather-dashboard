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
   `gh pr create --fill` then `gh pr merge --squash --delete-branch`
   yourself (gh CLI is authenticated as noxvex; do NOT pass `--auto` —
   auto-merge is disabled in this repo's GitHub settings and the flag
   fails with a GraphQL error). Never leave a phase's
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
  changes don't persist).
- FIXED (2026-07-14): Procfile's `release:` phase is not supported by
  Railpack (Railway's builder) — it was silently never executed, so
  migrations were likely only ever applied manually via `railway ssh`,
  not automatically on deploy. `migrate --noinput` now runs as the first
  step of the `web:` line instead, before collectstatic/gunicorn, so it
  executes on every deploy.
- Plotly `<script>` tag must never have `defer` — causes charts to silently
  not render.
- ERA5 precipitation aggregates as SUM, temperature as MEAN — don't conflate.
- `ingest_weather` (short-range 16-day forecast), `fetch_seasonal`
  (SEAS5), and `fetch_ec46` all have no cron/worker service yet — all
  three must be triggered manually via `railway ssh` until a scheduled
  worker service is set up. `fetch_seasonal.py`'s docstring says "Run
  daily via Railway Cron" but no such cron exists in the actual Railway
  project (confirmed via `railway status`) — don't trust that comment.
  If data looks stale/missing, check this first.

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
  `gh pr create --fill` → `gh pr merge --squash --delete-branch`
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
- Revize: střednědobá (EC46) and dlouhodobá (SEAS5) buckets now do real
  revision comparisons (`_mlr_revision_context()` in views.py), same
  shape as aktuální but temp_mean directly (1.0 °C threshold, noisier
  than aktuální's 0.5) and precip_prob_delta as a raw mm delta ("mm",
  not "pb" — precip_probability is a documented placeholder field, it
  stores the raw precip sum, not a real probability; label was wrong
  in the first cut, fixed). Still falls back to the not_enough_data
  card correctly when <2 snapshots exist. Tests: RevisionTrackerMlrBucketTest.
- `fetch_ec46` management command added (mirrors `fetch_seasonal.py`),
  requesting the pure EC46 ensemble mean explicitly via
  `models=ecmwf_ec46_ensemble_mean` — confirmed live against the API
  that `fetch_seasonal.py`'s SEAS5 call (no `models` param) actually
  uses Open-Meteo's default "Seasonal Seamless" blend, which already
  mixes EC46 into its first 46 days; this command isolates pure EC46
  instead. `forecast_days=46` confirmed as EC46's real horizon (values
  go null past day ~46 if you ask for more). Run once via `railway ssh`
  on 2026-07-13: 1012 rows across all 22 points (22 × 46 days), 0
  failures, 1 distinct issued_at snapshot. Confirmed střednědobá still
  correctly renders "zatím málo dat" with just this 1 snapshot (needs 2).
- Two cron wrapper commands added: `run_frequent_ingest` (ingest_weather
  → detect_changes → generate_outlook_notes) and `run_daily_ingest`
  (fetch_seasonal → fetch_ec46 → fetch_pollen → fetch_era5_backfill with
  a rolling 14-day `--start` → prune_notes). Each sub-step is its own try/except so one
  failure doesn't block the rest; both always exit 0 and print an
  "N/total steps succeeded" summary line — check that line + stderr in
  Railway's logs, don't rely on exit code to detect a partial failure.
  Neither is wired into Procfile — they're meant for their own Railway
  Cron services (see "Manual Railway setup required" below; code-side
  work is done, the actual cron service creation is a dashboard step).
  Tests: RunFrequentIngestTest, RunDailyIngestTest in ingest/tests.py
  (mock call_command to confirm a raised exception on one step doesn't
  stop the others).
- Fixed Procfile: dropped the `release:` line (Railpack doesn't support
  it, so migrations were silently never auto-applied on deploy) and
  moved `migrate --noinput` into the `web:` line, running before
  collectstatic/gunicorn. Migrations now run automatically on every
  deploy instead of needing manual `railway ssh` runs.
- Historie piny (PRs #10–#14, 2026-07-14): `HistoriePin` model (separate
  table by explicit user choice, NOT extra fields on Note; params stored
  in the exact Historie GET-param format so deep links are a plain
  urlencode). "Přidat pin" button + modal on the comparison form
  (rozsah=vlastni only; metric only t/p — pct mode doesn't exist for
  custom ranges), 📌 text-scatter marker on the overlay chart (no
  vertical line; y = nearest chart point, current year preferred),
  compact pin list under the chart filtered to the displayed bod+metric,
  click marker ↔ list both ways, detail = min/max/avg/std mini-table
  from the same daily `_historical_series` + year-window rules as the
  comparison itself. Cross-post: show_in_feed=True creates a linked
  human Note (pin.feed_note, OneToOne SET_NULL) rendered via the new
  shared `_note_card.html` include with param summary + deep link;
  read-only Aktuality column sits right of the Historie chart.
  Lifecycle: prune_notes handles pins with the same 14d-soft/30d-hard
  cutoffs (pinned exempt); pin EXPIRY intentionally does NOT delete its
  feed card (card has its own lifecycle) but explicit pin deletion
  cascades to the card (HistoriePin.delete()). Permissions mirror notes:
  author or leader/admin edit/delete (edit = body only, syncs card),
  pin/unpin leader/admin only. Tests: HistoriePinLifecycleTest,
  PinCreateViewTest, PinPermissionsTest (notes/tests.py), updated
  RunDailyIngestTest (5th step). Suite: 35 tests green.
- Pins feedback round (PR #16): FIXED Historie losing od/do/roky on
  every view switch (seg/rezim links + hidden inputs now carry them);
  FIXED overlapping pin markers looking like one pin (same-x markers
  stack vertically); pins now unlimited (no [:30]). Added: selecting a
  pin shades its od–do range in the chart; Aktuality pin cards show the
  min/max/avg/std table inline (cached 1h/pin, `_pin_stats_for`); new
  **Subhistorie** nav page — all pins regardless of bod/metric, summary
  stats + printable weekly progression (red ▲/teal ▼ deltas with °C AND
  %, per the delta convention) + @media print stylesheet. Tests:
  HistoriePinsRoundTwoTest. Suite: 39 tests green.

NOT YET DONE / KNOWN BROKEN (going into next session):
- No Railway cron/worker service yet for `ingest_weather` OR
  `fetch_seasonal`/`fetch_ec46` — all three are manual-only via
  `railway ssh` despite `fetch_seasonal.py`'s docstring claiming "Run
  daily via Railway Cron" (confirmed via `railway status`: only the web
  service + Postgres exist, no cron/worker resource at all — that line
  was aspirational, not actual). The `run_frequent_ingest`/
  `run_daily_ingest` wrapper commands now exist (see DONE above) but
  still need the manual Railway dashboard setup below before anything
  runs on a schedule.
- Revize střednědobá still won't show real revisions until `fetch_ec46`
  is run a 2nd time on a later day (1 snapshot exists as of 2026-07-13,
  needs 2 distinct issued_at values). Same for dlouhodobá/SEAS5, which
  also still has only 1 snapshot. Will self-resolve once the daily cron
  is set up and has run twice; can also be forced via `railway ssh` in
  the meantime.

### Manual Railway setup required (do this in the dashboard — NOT via
### railway.toml/config-as-code: Railway has a known Dec-2025 bug where
### cron schedules set via config-as-code silently get "stuck"; use the
### dashboard Settings > Cron Schedule field instead)
1. New service **"cron-frequent"**, same repo/branch as the web service.
   - Settings > Deploy > Custom Start Command: `python manage.py run_frequent_ingest`
   - Settings > Cron Schedule: `0 */12 * * *` (every 12h, UTC — Railway
     cron is always UTC, there's no timezone conversion field)
2. New service **"cron-daily"**, same repo/branch.
   - Settings > Deploy > Custom Start Command: `python manage.py run_daily_ingest`
   - Settings > Cron Schedule: `0 2 * * *` (02:00 UTC ≈ 03:00–04:00
     Prague depending on DST — off-peak, and after that day's frequent
     runs would already have refreshed short-range data)
3. Both new services need the same environment variables as the web
   service (`DATABASE_URL`, `OPEN_METEO_*` keys) — copy via Railway's
   "reference variables" or a shared variable group, don't duplicate
   secrets by hand.

## Priority order (revised — Bod deprioritized, Revize + pins now ahead
## of remaining original Bod/detail polish items)

1. **FÁZE 4 — stabilization** — DONE (PR #2): Historie custom-range year
   bug fixed, Bod accordion default-closed, minimal regression tests in
   notes/tests.py.
2. **Revize expansion** — DONE (view + template + ingestion): střednědobá
   (EC46) and dlouhodobá (SEAS5) buckets do real revision comparisons,
   `fetch_ec46` command exists and has been run via `railway ssh`. Only
   remaining gap is time — needs a 2nd `fetch_ec46`/`fetch_seasonal` run
   on a later day before either bucket shows real deltas (see NOT YET
   DONE above).
3. **Historie "pin" annotations** — DONE (PRs #10–#14, see DONE above).
   Deviations from the original sketch: separate `HistoriePin` table
   instead of extending Note (user's explicit choice), metric only
   temp-abs/precip (pct mode doesn't exist for custom ranges), card
   summary has no computed average (avg lives in the pin detail on
   Historie). Air-quality metric still future (needs the data first).
4. Remaining original "Bod/detail" polish (larger temp numbers, resize
   sections, scrollable table) — lower priority now, Bod itself matters
   less than short/medium-range change tracking per user's actual usage
5. Data expansion (pollen, air quality — distinguish gases/dust/pollen,
   UV, wind days, pressure) — unblocks air-quality as a pin metric later
6. Toggleable display layers
7. Mobile tweaks — last

Session B (not started): SMTP email, anomaly detection (medium-range
forecast vs. ERA5 baseline), daily 9:00 Prague digest with opt-in.