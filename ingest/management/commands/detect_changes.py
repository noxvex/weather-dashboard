"""
Analyses short-range forecast history and creates system_change notes when thresholds are crossed.
Operates on national CZ and SK averages to avoid per-point noise.
Safe to run daily — deduplicates by checking for existing notes created today.

Detects:
  - Temp swing: range ≥5°C across any 7-day window in the current forecast
  - Heat wave: 3+ consecutive days with temp_max ≥30°C
  - Rain flip: dry→wet or wet→dry transition (precip_sum crossing 1mm threshold)
  - Revision delta: same forecast_date, latest vs previous daily snapshot differ by ≥3°C nationally
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from django.db.models import Q

from ingest.models import DailyForecast, WeatherPoint
from notes.models import Note

SWING_THRESHOLD = 5.0    # °C within 7-day window
HEATWAVE_TEMP = 30.0     # °C
HEATWAVE_DAYS = 3        # consecutive days
PRECIP_WET = 1.0         # mm — above this = "wet day"
REVISION_THRESHOLD = 3.0 # °C difference between snapshots to trigger a note

User = get_user_model()


def _get_system_user():
    """Use the admin/noxvex account as author for system notes."""
    return User.objects.filter(is_superuser=True).order_by("pk").first()


def _national_avg(rows, field):
    vals = [getattr(r, field) for r in rows if getattr(r, field) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _note_exists_today(country_tag, event_tag):
    """Returns True if we already wrote this type of note today (deduplication)."""
    today = date.today()
    return Note.objects.filter(
        Q(note_type=Note.TYPE_SYSTEM_CHANGE) &
        Q(created_at__date=today) &
        Q(body__contains=event_tag) &
        Q(body__contains=country_tag)
    ).exists()


def _horizon_for(event_date):
    """
    Horizon by how far out the event is: within a week = krátkodobá (blue),
    beyond that = between krátkodobá and střednědobá, shown as mid (orange).
    """
    if event_date is None:
        return Note.HORIZON_SHORT
    lead_days = (event_date - date.today()).days
    return Note.HORIZON_SHORT if lead_days <= 7 else Note.HORIZON_MID


def _create_note(author, body, country="both", horizon=Note.HORIZON_SHORT):
    Note.objects.create(
        author=author,
        body=body,
        note_type=Note.TYPE_SYSTEM_CHANGE,
        country=country,
        horizon=horizon,
    )


def _get_latest_two_batch_dates(country):
    """Return the two most recent distinct issued_at dates for short-range forecasts."""
    dates = list(
        DailyForecast.objects
        .filter(horizon=DailyForecast.HORIZON_SHORT, point__country=country, issued_at__isnull=False)
        .order_by("-issued_at__date")
        .values_list("issued_at__date", flat=True)
        .distinct()[:2]
    )
    return dates  # [newest_date, previous_date] or fewer


def _get_national_series(country, issued_date, days=16):
    """
    Returns a list of (forecast_date, avg_temp_max, avg_precip_sum) tuples
    for the given country and batch date, ordered by forecast_date.
    """
    rows = list(
        DailyForecast.objects
        .filter(
            horizon=DailyForecast.HORIZON_SHORT,
            point__country=country,
            issued_at__date=issued_date,
        )
        .select_related("point")
        .order_by("forecast_date")
    )
    if not rows:
        return []

    # Group by forecast_date, compute national average
    from collections import defaultdict
    by_date = defaultdict(list)
    for r in rows:
        by_date[r.forecast_date].append(r)

    series = []
    for fd in sorted(by_date):
        day_rows = by_date[fd]
        series.append((
            fd,
            _national_avg(day_rows, "temperature_max"),
            _national_avg(day_rows, "precipitation_sum"),
        ))
    return series[:days]


class Command(BaseCommand):
    help = "Detect weather change patterns in forecast history and create system_change notes."

    def handle(self, *args, **options):
        # Expiry: system reports vanish after 14 days unless someone pinned them
        cutoff = timezone.now() - timedelta(days=14)
        purged, _ = Note.objects.filter(
            note_type__startswith="system_", is_pinned=False, created_at__lt=cutoff,
        ).delete()
        if purged:
            self.stdout.write(f"Purged {purged} expired unpinned system note(s).")

        author = _get_system_user()
        if not author:
            self.stderr.write("No superuser found to author system notes. Run seed_points first.")
            return

        notes_created = 0

        for country, country_label in [("CZ", "Česká republika"), ("SK", "Slovensko")]:
            batch_dates = _get_latest_two_batch_dates(country)
            if not batch_dates:
                self.stdout.write(f"  {country}: no issued_at data yet, skipping.")
                continue

            latest_date = batch_dates[0]
            series = _get_national_series(country, latest_date)
            if not series:
                continue

            temps = [t for _, t, _ in series if t is not None]
            precips = [p if p is not None else 0 for _, _, p in series]

            # ── Temp swing ──────────────────────────────────────────────
            for i in range(len(temps) - 6):
                window = temps[i:i + 7]
                if (max(window) - min(window)) >= SWING_THRESHOLD:
                    tag = f"teplotní výkyv_{country}"
                    if not _note_exists_today(country, f"teplotní výkyv"):
                        _create_note(author, country=country.lower(), horizon=_horizon_for(series[i][0]), body=
                           f"⚠️ {country_label}: Předpověď ukazuje teplotní výkyv "
                            f"{round(max(window) - min(window), 1)} °C v průběhu 7 dnů "
                            f"(od {series[i][0].strftime('%-d. %-m.')} do {series[i+6][0].strftime('%-d. %-m.')})."
                        )
                        notes_created += 1
                    break

            # ── Heat wave ────────────────────────────────────────────────
            streak = 0
            streak_start = None
            for fd, t, _ in series:
                if t is not None and t >= HEATWAVE_TEMP:
                    streak += 1
                    if streak == 1:
                        streak_start = fd
                    if streak >= HEATWAVE_DAYS:
                        if not _note_exists_today(country, "tropické dny"):
                            _create_note(author, country=country.lower(), horizon=_horizon_for(streak_start), body=
                               f"🌡 {country_label}: Předpovídány tropické dny (≥{HEATWAVE_TEMP:.0f} °C) "
                                f"od {streak_start.strftime('%-d. %-m.')} — "
                                f"zatím {streak} po sobě jdoucích dnů."
                            )
                            notes_created += 1
                        break
                else:
                    streak = 0
                    streak_start = None

            # ── Rain flip ────────────────────────────────────────────────
            for i in range(1, len(precips)):
                prev_wet = precips[i - 1] >= PRECIP_WET
                curr_wet = precips[i] >= PRECIP_WET
                if prev_wet != curr_wet:
                    fd = series[i][0]
                    direction = "sucho → déšť" if curr_wet else "déšť → sucho"
                    if not _note_exists_today(country, "přechod srážek"):
                        _create_note(author, country=country.lower(), horizon=_horizon_for(fd), body=
                           f"🌧 {country_label}: Předpověď naznačuje přechod srážek "
                            f"({direction}) kolem {fd.strftime('%-d. %-m.')}."
                        )
                        notes_created += 1
                    break

            # ── Revision delta ───────────────────────────────────────────
            if len(batch_dates) >= 2:
                prev_date = batch_dates[1]
                prev_series = {fd: t for fd, t, _ in _get_national_series(country, prev_date)}
                for fd, curr_t, _ in series:
                    prev_t = prev_series.get(fd)
                    if curr_t is not None and prev_t is not None:
                        delta = abs(curr_t - prev_t)
                        if delta >= REVISION_THRESHOLD:
                            direction = "vyšší" if curr_t > prev_t else "nižší"
                            if not _note_exists_today(country, "revize předpovědi"):
                                _create_note(author, country=country.lower(), horizon=_horizon_for(fd), body=
                                   f"🔄 {country_label}: Revize předpovědi pro "
                                    f"{fd.strftime('%-d. %-m.')} — teplota o {delta:.1f} °C {direction} "
                                    f"než předchozí verze ({prev_t:.1f} → {curr_t:.1f} °C)."
                                )
                                notes_created += 1
                            break

        self.stdout.write(self.style.SUCCESS(f"detect_changes: {notes_created} note(s) created."))
