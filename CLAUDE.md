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
7. ALWAYS commit and push directly to `main`, never to a worktree or feature
   branch, unless explicitly told otherwise. Railway only auto-deploys from
   `main` — work pushed elsewhere silently never reaches production. Before
   reporting a task as done, confirm with `git branch --show-current` that
   the commit landed on `main`.

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
DONE: horizon colors + readability (badges, borders, delta %), Bod page
CZ/SK country selector with expandable city lists, Aktuality/Bod filters
(time range, horizon, country), DB indexes, seasonal-data caching, shared
query helpers in `aktuality()`, fixed multiline-comment bug, migrated
Postgres to Netherlands region to match web service.

NEXT UP (original roadmap order): Bod/detail page — larger temperature
numbers, clarify the unlabeled "bar" element's purpose, selectable forecast
horizon instead of fixed 7 days, labels above % change indicators, resize
current-weather vs. forecast sections, scrollable expectations table,
reconsider graph zoom interaction. Then: Historie (monthly granularity,
multi-year average curve), data expansion (pollen/AQ/UV/wind/pressure),
toggleable display layers, graph annotations, mobile — in that order.