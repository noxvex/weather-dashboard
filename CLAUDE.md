# Weather Dashboard — Marketing Team (CZ/SK)

## Project Overview
Internal web tool for a marketing team (10 people) covering Czechia and Slovakia.
Purpose: help the team plan sales/promo timing around weather patterns, seasonal
trends, and historical analogs. Humans make the calls — the app surfaces data,
suggestions, and history; it does not automate any decisions.

**Builder context:** The person building this is a Python/Django beginner.
Explain reasoning briefly before/while writing code. This is a real production
tool for a real company, not a practice project — code quality matters.

**Languages:** All UI, reports, and alerts are in **Czech**. All code, comments,
commit messages, and variable names are in **English**.

---

## Tech Stack
- **Backend:** Django (auth, password hashing, admin panel)
- **Frontend:** Django HTML templates + vanilla JavaScript, Plotly.js for charts
  (built-in PNG export)
- **Database:** PostgreSQL, hosted on Railway
- **Scheduled ingest:** Django management commands, triggered by Railway Cron
  Service (no Celery — deliberately lean for a 10-user tool)
- **Excel/CSV export:** server-side via pandas
- **Hosting:** Railway (Hobby plan, $5/mo, usage-based)

## Non-Negotiables
- Secrets **never** committed — everything sensitive lives in `.env`, which is
  git-ignored
- Database backups must be on
- Passwords are hashed (Django's default hasher), never stored plain
- Free-tier vs paid-tier API/hosting keys are swapped via `.env` only —
  **zero code changes** required to go from dev to production

---

## Data Sources

### Weather — Open-Meteo (commercial tier)
One consistent JSON schema across all endpoints. Base URL and API key are read
from `.env` so we can develop against the free tier and flip to paid later:
- **Dev/now:** `https://api.open-meteo.com` — free, non-commercial, no key,
  10,000 calls/day, 600/min limit
- **Production (before go-live):** `https://customer-api.open-meteo.com` +
  `apikey=` param — Standard tier (1M calls/month)

Horizons pulled, all in one schema:
- **Short:** 7–16 days
- **Mid:** up to 6 weeks (EC46)
- **Long:** up to ~7 months (SEAS5)
- **Historical actuals:** ERA5 reanalysis back to 2015 (full backfill on
  Phase 1, all 22 points, all variables)

### Pollen & Air Quality — CAMS (via Open-Meteo Air Quality API)
- CZ + SK national level only, 5-day maximum forecast
- Unified panel shown on the short-range view
- Static seasonal-tendency calendar layer (e.g. "birch peaks late April") shown
  on mid/long views where live pollen data doesn't reach

### Historical Warnings — ČHMÚ
- Layered in later (Phase 9), not part of initial build

### Accuracy Dataset (proprietary, grows over time)
- Every forecast is stored alongside the eventual real actual once it occurs
- Accuracy-history seed: 2021–2024, grows from launch forward
- Powers comparison graphs on the History screen

---

## Data Model: Points vs. National Aggregate
- **22 regional points** (CZ + SK) are the underlying granular data — pulled,
  stored, and used to feed the map + regional drill-down + accuracy dataset
- **National CZ and SK aggregates** (computed average across each country's
  points) are the **headline numbers** shown on the Landing screen — the app
  targets whole-country planning, not regional
- Map + individual regional points are available as a drill-down layer under
  the national figures

---

## Roles & Auth
- **noxvex** — ADMIN. Django superuser. Creates all accounts, full control.
- **yxes** — LEADER. Can moderate/edit/delete ANY note (not just their own).
  Notes authored by yxes are shown in RED and pinned to top.
- **Workers** — named accounts. Can edit/delete only their own notes.
- **Login:** username = first name, lowercase. Password set initially by
  admin; user changes it after first login.
- **No self-service password reset** — admin resets manually if someone is
  locked out. Acceptable tradeoff at this team size.
- Passwords hashed via Django's built-in hasher.

---

## Screens

### Landing / Aktuality
- Forecast + ~5 key headline numbers (national CZ/SK aggregates), detail on
  click
- "What changed" section (vs. previous pull)
- Historical-analog suggestions, **two color-coded streams**:
  - **APP (blue)** — weather-framed analog engine, e.g. "~87% like 2022, ran
    wetter → expect rain"
  - **TEAM (by author)** — human marketing takes; yxes shown in red, pinned
- Notes + tags live in the main area of this screen
- Per-user UNREAD vs READ state on notes

### History / Graphs
- Year-over-year comparison
- Selectable granularity: years / months / weeks / days
- All weather variables selectable (pollen excluded — data only goes 5 days,
  not useful for year comparison)
- % similarity score, e.g. "this year ≈ 2019, 87%"
- Tags/notes can be pinned directly to graph points
- Downloads: PNG (chart) + Excel/CSV (data)

---

## Notes & Tags
- Shared — everyone sees everyone's notes
- Two placements: pinned-to-graph-point, and general main-area notes
- Tagged by topic (e.g. "promo", "weather event", "sales result")
- New vs. read state tracked per user

## Alerts
- **09:00 Czech morning report** — leads with pollen/air quality line, then
  "what changed," then the three horizons
- **Immediate alert** on a big change (thresholds are tunable config values,
  not hardcoded)
- Delivered via email + in-app notification; Slack optional/future
- Cron scheduling note: Railway's minimum cron frequency is 5 minutes, which
  comfortably covers both the daily digest and threshold-triggered checks

---

## Build Order (each phase = a working, testable thing)
1. Setup + DB + first CZ/SK data pull
2. Login / roles
3. Landing / Aktuality
4. History / graphs
5. Notes / tags
6. Alerts
7. Pollen/air quality + seasonal layer
8. Deploy (flip Open-Meteo + Railway to paid tiers here)
9. ČHMÚ integration + accuracy graphs

**Nothing gets built until the person explicitly says "build now."** Before
that point: ask questions, flag tradeoffs honestly, suggest better approaches
where relevant — don't assume and proceed.

---

## Setup Status (as of last session)
- ✅ PyCharm + Claude Code (JetBrains plugin) connected
- ✅ GitHub account ready
- ✅ Railway account created, project `weather-dashboard` created, PostgreSQL
  added, `DATABASE_URL` saved in local `.env`
- ✅ Open-Meteo: building against free tier now, will swap to
  `customer-api.open-meteo.com` + key before Phase 8 (go-live)
- ⬜ `.gitignore` — not yet created (needed before first commit)
- ⬜ Django project itself — not yet started (Phase 1, awaiting "build now")

## Working Conventions
- One Django app per concern: `accounts/`, `ingest/`, `dashboard/`, `notes/`,
  `alerts/`
- Explain what each command/file does briefly when introducing it — the
  person is learning Python/Django as we go
- Before any code is written: ask clarifying questions, flag honest
  tradeoffs, suggest alternatives. Wait for explicit "build now."