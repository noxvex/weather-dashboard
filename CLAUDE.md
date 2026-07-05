# Phase 4 Build Brief — Weather Dashboard

Paste this into a fresh Claude Code chat. Read fully before writing code. Ask me
anything ambiguous before generating files. I'm a Python/Django beginner — explain
non-obvious decisions briefly as you go (docstrings/comments are fine for this).

## Scope of this phase

Data collection pipelines for ALL forecast horizons + historical baseline, PLUS the
UI/logic that's achievable with data we already have. Medium/long-range and pollen
UI screens are NOT built yet — data collection starts now so it has time to
accumulate, screens come in a later phase.

## 1. New data sources (all Open-Meteo, dev = no API key)

| Source | Base URL env var | Endpoint |
|---|---|---|
| Existing short-range | `OPEN_METEO_BASE_URL` (already set) | `/v1/forecast` |
| Medium+seasonal | `OPEN_METEO_SEASONAL_URL` | `https://seasonal-api.open-meteo.com/v1/forecast` |
| Historical (ERA5) | `OPEN_METEO_ARCHIVE_URL` | `https://archive-api.open-meteo.com/v1/archive` |
| Pollen/air quality | `OPEN_METEO_AIRQUALITY_URL` | `https://air-quality-api.open-meteo.com/v1/air-quality` |

All follow the same customer-api + key swap pattern as the existing forecast source
— add matching `_API_KEY` env vars (blank in dev) for each, unused until production.

## 2. Model changes

**Extend existing forecast model** (or add if not already present):
- `target_date` (date the forecast is FOR)
- `issued_at` (timestamp the forecast was FETCHED) — append-only, never overwrite
- This pair is what makes revision tracking possible: compare rows with the same
  `target_date` and different `issued_at`.

**New: `MediumLongRangeForecast`**
- point (FK), target_date, issued_at, horizon (`ec46` or `seas5`), temp_mean,
  temp_anomaly, precip_probability, source_model
- Populated by scheduled fetch, no UI yet.

**New: `HistoricalActual`**
- point (FK), date, temp_min, temp_max, precip_mm, wind_kmh
- One-time backfill command + ongoing daily append so "today" eventually becomes
  history too.

**New: `PollenRecord`**
- point (FK), date, issued_at, birch, grass, ragweed, alder, mugwort (whichever
  CAMS returns for CZ/SK domain), aqi_european
- Forecast-only, 5-day horizon, no backfill possible.

**Extend `Note` model:**
- `note_type` field: `human`, `system_change`, `system_outlook` (badge color logic:
  human = author-based/yxes-red, system_change = teal "systém", system_outlook =
  teal "výhled" — same color, different label per the mockup)
- `save()` override: if `note_type` starts with `system_`, force `is_pinned=True`
  automatically, no manual step.

## 3. Management commands

- `fetch_seasonal` — daily pull from seasonal-api for all 22 points, stores to
  `MediumLongRangeForecast`
- `fetch_era5_backfill` — one-time, date-range argument, populates `HistoricalActual`
  from archive-api (start conservative — e.g. last 2 years — then widen; don't pull
  decades in one run against free tier without testing volume first)
- `fetch_pollen` — daily pull from air-quality-api, stores to `PollenRecord`
- `detect_changes` (extends/creates) — runs against short-range forecast history:
  - Swing: temp range ≥5°C within any rolling 7-day window → `system_change` note
  - Heat wave: 3+ consecutive days ≥30°C → `system_change` note
  - Rain flip: dry→wet or wet→dry day-to-day transition → `system_change` note
  - Revision delta: same `target_date`, new `issued_at` vs previous → store delta,
    surface via revision-tracker view (not necessarily a note for every tiny change —
    use a threshold, e.g. only auto-note deltas ≥3°C, to avoid spam)
- `generate_outlook_notes` (new) — reads current short-range forecast (0–16 days,
  data already available, no new source needed):
  - Rain probability >60% next 7 days → "Očekáváme déšť v následujícím týdnu"
  - Rain probability <20% next 7 days → "Očekáváme sucho v následujícím týdnu"
  - Temp trending toward heat-wave threshold but not yet confirmed → hedge language,
    e.g. "Teploty se blíží k tropickým hodnotám, ale predikce se může změnit"
  - ALWAYS hedge with probabilistic wording ("očekáváme", "může", "nelze vyloučit")
    per locked principle — never state medium/long-range as certain
  - `note_type='system_outlook'`

  NOT included yet (needs ERA5 baseline, defer to when HistoricalActual has enough
  data): "it should have rained but didn't" style anomaly-vs-normal detection. Once
  `HistoricalActual` has enough rows to compute a climatological average per point/
  time-of-year, this becomes a straightforward comparison — don't build it blind now.

## 4. Views / templates

- **Aktuality feed**: add author filter (chips: Vše / yxes / Systém / [named users]).
  Filter is a simple queryset filter on `Note.author`, no new model needed.
- **"Since last login" panel**: query forecast-history rows where
  `issued_at >= request.user.last_login` per point, compare oldest-in-range vs
  latest, list deltas. `last_login` already exists on `AbstractUser` — no new field.
- **Revision tracker view**: three buttons (Aktuální / Střednědobá / Dlouhodobá)
  filtering by lead-time bucket. Střednědobá/Dlouhodobá buttons render a
  "Data se sbírá, zobrazení brzy" (data collecting, display coming soon) state —
  they should NOT be hidden, just clearly marked not-yet-populated in UI, since the
  underlying data IS being collected from this phase onward.
- **Year-over-year card**: same coming-soon treatment, tied to `HistoricalActual`
  row count reaching a usable threshold.
- Graph placement: above the two-column Aktuality/revision layout, per approved
  mockup (attach mockup HTML if useful reference for template structure/dark theme
  CSS variables).

## 5. Config / env vars to add

```
OPEN_METEO_SEASONAL_URL=https://seasonal-api.open-meteo.com/v1/forecast
OPEN_METEO_SEASONAL_API_KEY=
OPEN_METEO_ARCHIVE_URL=https://archive-api.open-meteo.com/v1/archive
OPEN_METEO_ARCHIVE_API_KEY=
OPEN_METEO_AIRQUALITY_URL=https://air-quality-api.open-meteo.com/v1/air-quality
OPEN_METEO_AIRQUALITY_API_KEY=
```

## Build order suggestion (ask me to confirm before starting if unsure)

1. Model changes + migrations (Note fields, target_date/issued_at, new models)
2. Fetch commands for the 3 new sources (test each returns data before wiring UI)
3. `detect_changes` + `generate_outlook_notes` logic
4. Since-login query + author filter on existing Aktuality view
5. Revision tracker view with coming-soon states
6. Template/CSS pass matching the approved dark-theme mockup

## Reminders

- `.gitignore` still excludes `.env` — verify before committing new env vars
- All UI text Czech, all code/comments English
- Console email backend still fine, no SMTP needed for this phase
- Commit checkpoint after each numbered step above, not one giant commit