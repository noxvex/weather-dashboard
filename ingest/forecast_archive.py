"""
On-demand fetch of archived forecasts from Open-Meteo into ArchivedForecast.

Two archives cover different lead ranges:
  - previous-runs API: leads 1–7 for any valid-date window in ONE request
    per point (hourly *_previous_dayN variables, aggregated here to daily
    max/min/sum over local days). Archived back to ~2022 (GFS) / 2024.
  - single-runs API (ECMWF IFS): one archived model run per request; a run
    initialized L days before a valid date carries that date at lead L, so
    fetching runs (start−15 .. end−8) fills leads 8–15 for a window. Runs
    are archived since 2024-03-14; every fetched run stores its whole
    horizon, not just the requested window (free cache warm-up).

Everything lands in ArchivedForecast (unique per point+valid_date+lead) via
bulk_create(ignore_conflicts=True): repeated fetches are idempotent and an
already-cached window costs zero API calls. Rows are stored even when the
API returns nulls for a day — a stored null means "fetched, archive has
nothing", which is what the coverage checks rely on.
"""
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from ingest.models import ArchivedForecast

PREV_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
SINGLE_RUNS_URL = "https://single-runs-api.open-meteo.com/v1/forecast"

PREV_LEADS = range(1, 8)      # previous-runs archive: 1..7 days
SINGLE_LEADS = range(8, 16)   # single-runs (ECMWF IFS): 8..15 days
SINGLE_RUNS_MIN_RUN = date(2024, 3, 14)  # ECMWF runs archived from here on


def _get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _daily_from_hourly(times, values):
    """Group hourly values by ISO date -> list of non-null floats."""
    by_day = defaultdict(list)
    for ts, v in zip(times, values):
        if v is not None:
            by_day[ts[:10]].append(v)
    return by_day


def fetch_previous_runs(point, start, end):
    """
    One API call: leads 1–7 for every day in [start, end] for one point.
    Returns the number of rows written (0 rows ≠ failure — archive may be
    empty for the window; rows with nulls still count as coverage).
    """
    hourly_vars = ",".join(
        f"temperature_2m_previous_day{n},precipitation_previous_day{n}" for n in PREV_LEADS
    )
    qs = urllib.parse.urlencode({
        "latitude": point.latitude, "longitude": point.longitude,
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "hourly": hourly_vars, "timezone": "Europe/Prague",
    })
    data = _get_json(f"{PREV_RUNS_URL}?{qs}")
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    rows = []
    for lead in PREV_LEADS:
        temps = _daily_from_hourly(times, hourly.get(f"temperature_2m_previous_day{lead}", []))
        precs = _daily_from_hourly(times, hourly.get(f"precipitation_previous_day{lead}", []))
        d = start
        while d <= end:
            key = d.isoformat()
            tvals = temps.get(key, [])
            pvals = precs.get(key, [])
            rows.append(ArchivedForecast(
                point=point, valid_date=d, lead_days=lead,
                temp_max=round(max(tvals), 1) if tvals else None,
                temp_min=round(min(tvals), 1) if tvals else None,
                precip_mm=round(sum(pvals), 1) if pvals else None,
                source=ArchivedForecast.SOURCE_PREVIOUS_RUNS,
            ))
            d += timedelta(days=1)
    ArchivedForecast.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def fetch_single_run(point, run_date):
    """
    One API call: the full daily horizon of the ECMWF IFS run initialized at
    run_date 00:00 UTC. Stores every day of the horizon whose lead falls in
    SINGLE_LEADS. The lead-8 row doubles as the "this run was fetched"
    marker for coverage checks.
    """
    if run_date < SINGLE_RUNS_MIN_RUN:
        return 0
    qs = urllib.parse.urlencode({
        "latitude": point.latitude, "longitude": point.longitude,
        "run": f"{run_date.isoformat()}T00:00", "models": "ecmwf_ifs",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "forecast_days": 16, "timezone": "UTC",
    })
    data = _get_json(f"{SINGLE_RUNS_URL}?{qs}")
    daily = data.get("daily", {})
    rows = []
    for i, ds in enumerate(daily.get("time", [])):
        d = date.fromisoformat(ds)
        lead = (d - run_date).days
        if lead not in SINGLE_LEADS:
            continue
        tmax = daily.get("temperature_2m_max", [None] * 99)[i]
        tmin = daily.get("temperature_2m_min", [None] * 99)[i]
        prec = daily.get("precipitation_sum", [None] * 99)[i]
        rows.append(ArchivedForecast(
            point=point, valid_date=d, lead_days=lead,
            temp_max=tmax, temp_min=tmin, precip_mm=prec,
            source=ArchivedForecast.SOURCE_SINGLE_RUNS,
        ))
    ArchivedForecast.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def ensure_archive(point, start, end, max_calls=20):
    """
    Make sure ArchivedForecast covers [start, end] for the point, fetching
    only what's missing. Returns (api_calls_used, exhausted) — exhausted=True
    means the call budget ran out before the window was fully covered (the
    view shows a hint to retry / narrow the range). API errors on individual
    calls are swallowed so one bad run doesn't kill the whole analysis; the
    affected sub-range simply stays unfetched and is retried next time.
    """
    calls = 0

    # previous-runs: fetch the missing sub-range in one call
    have = set(
        ArchivedForecast.objects
        .filter(point=point, source=ArchivedForecast.SOURCE_PREVIOUS_RUNS,
                valid_date__gte=start, valid_date__lte=end, lead_days=1)
        .values_list("valid_date", flat=True)
    )
    missing = [start + timedelta(days=i) for i in range((end - start).days + 1)
               if start + timedelta(days=i) not in have]
    if missing and calls < max_calls:
        try:
            fetch_previous_runs(point, min(missing), max(missing))
        except Exception:
            pass
        calls += 1

    # single-runs: one call per missing run (lead-8 row is the marker)
    first_run = max(start - timedelta(days=max(SINGLE_LEADS)), SINGLE_RUNS_MIN_RUN)
    last_run = end - timedelta(days=min(SINGLE_LEADS))
    if last_run >= first_run:
        run_dates = [first_run + timedelta(days=i) for i in range((last_run - first_run).days + 1)]
        fetched_markers = set(
            ArchivedForecast.objects
            .filter(point=point, source=ArchivedForecast.SOURCE_SINGLE_RUNS, lead_days=8,
                    valid_date__gte=first_run + timedelta(days=8),
                    valid_date__lte=last_run + timedelta(days=8))
            .values_list("valid_date", flat=True)
        )
        for run_date in run_dates:
            if run_date + timedelta(days=8) in fetched_markers:
                continue
            if calls >= max_calls:
                return calls, True
            try:
                fetch_single_run(point, run_date)
            except Exception:
                pass
            calls += 1

    return calls, False
