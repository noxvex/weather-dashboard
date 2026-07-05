"""
Generates system_outlook notes from the current short-range 7-day forecast.
Always uses hedged probabilistic language — never states anything as certain.
Runs once per day; skips if outlook notes already exist today (deduplication).

Conditions checked (national CZ and SK averages separately):
  - High rain probability: avg precipitation_prob_max >60% across next 7 days
  - Low rain probability: avg precipitation_prob_max <20% across next 7 days
  - Approaching heat wave: avg temp_max 26-29°C for 3+ consecutive days (below confirmed threshold)
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from ingest.models import DailyForecast
from notes.models import Note

RAIN_HIGH_THRESHOLD = 60   # % avg prob — "expect rain"
RAIN_LOW_THRESHOLD = 20    # % avg prob — "expect dry"
HEAT_APPROACH_LOW = 26.0   # °C — starts looking warm
HEAT_APPROACH_HIGH = 30.0  # °C — confirmed heat wave (handled by detect_changes)
HEAT_APPROACH_DAYS = 3     # consecutive days in range

User = get_user_model()


def _get_system_user():
    return User.objects.filter(is_superuser=True).order_by("pk").first()


def _already_ran_today(country_label):
    # Per-country check so CZ and SK are independent — if one ran but the other
    # didn't (e.g. no data), a re-run can still fill the missing country.
    return Note.objects.filter(
        note_type=Note.TYPE_SYSTEM_OUTLOOK,
        created_at__date=date.today(),
        body__contains=f"Výhled {country_label}",
    ).exists()


def _get_latest_7day_series(country):
    """National daily averages for next 7 days from the most recent short-range batch."""
    from collections import defaultdict

    latest_issued = (
        DailyForecast.objects
        .filter(horizon=DailyForecast.HORIZON_SHORT, point__country=country, issued_at__isnull=False)
        .order_by("-issued_at")
        .values_list("issued_at__date", flat=True)
        .first()
    )
    if not latest_issued:
        return []

    today = date.today()
    rows = list(
        DailyForecast.objects
        .filter(
            horizon=DailyForecast.HORIZON_SHORT,
            point__country=country,
            issued_at__date=latest_issued,
            forecast_date__gte=today,
        )
        .order_by("forecast_date")
    )

    by_date = defaultdict(list)
    for r in rows:
        by_date[r.forecast_date].append(r)

    series = []
    for fd in sorted(by_date)[:7]:
        day_rows = by_date[fd]
        temps = [r.temperature_max for r in day_rows if r.temperature_max is not None]
        probs = [r.precipitation_prob_max for r in day_rows if r.precipitation_prob_max is not None]
        series.append({
            "date": fd,
            "temp_max": round(sum(temps) / len(temps), 1) if temps else None,
            "precip_prob": round(sum(probs) / len(probs), 0) if probs else None,
        })

    return series


class Command(BaseCommand):
    help = "Generate system_outlook notes from current short-range forecast. Runs once per day."

    def handle(self, *args, **options):
        author = _get_system_user()
        if not author:
            self.stderr.write("No superuser found. Aborting.")
            return

        created = 0

        for country, country_label in [("CZ", "ČR"), ("SK", "SR")]:
            if _already_ran_today(country_label):
                self.stdout.write(f"  {country}: outlook note already exists today, skipping.")
                continue

            series = _get_latest_7day_series(country)
            if not series:
                continue

            probs = [d["precip_prob"] for d in series if d["precip_prob"] is not None]
            temps = [d["temp_max"] for d in series if d["temp_max"] is not None]

            if not probs and not temps:
                continue

            # ── High rain probability ────────────────────────────────────
            if probs and (sum(probs) / len(probs)) > RAIN_HIGH_THRESHOLD:
                Note.objects.create(
                    author=author,
                    note_type=Note.TYPE_SYSTEM_OUTLOOK,
                    body=(
                        f"🌧 Výhled {country_label} — následujících 7 dní: "
                        f"Předpovídáme zvýšenou pravděpodobnost srážek "
                        f"(průměr {round(sum(probs)/len(probs))} %). "
                        f"Predikce se může změnit."
                    ),
                )
                created += 1

            # ── Low rain probability ─────────────────────────────────────
            elif probs and (sum(probs) / len(probs)) < RAIN_LOW_THRESHOLD:
                Note.objects.create(
                    author=author,
                    note_type=Note.TYPE_SYSTEM_OUTLOOK,
                    body=(
                        f"☀️ Výhled {country_label} — následujících 7 dní: "
                        f"Očekáváme převážně suché počasí "
                        f"(průměrná pravděpodobnost srážek {round(sum(probs)/len(probs))} %). "
                        f"Nelze vyloučit krátkodobé přeháňky."
                    ),
                )
                created += 1

            # ── Approaching heat wave (hedge language — below confirmed threshold) ──
            streak = 0
            for d in series:
                t = d["temp_max"]
                if t is not None and HEAT_APPROACH_LOW <= t < HEAT_APPROACH_HIGH:
                    streak += 1
                    if streak >= HEAT_APPROACH_DAYS:
                        Note.objects.create(
                            author=author,
                            note_type=Note.TYPE_SYSTEM_OUTLOOK,
                            body=(
                                f"🌡 Výhled {country_label}: Teploty se v příštích dnech blíží "
                                f"k tropickým hodnotám ({round(t)} °C), ale nepřekračují je. "
                                f"Predikce se může měnit — sledujte vývoj."
                            ),
                        )
                        created += 1
                        break
                else:
                    streak = 0

        self.stdout.write(self.style.SUCCESS(f"generate_outlook_notes: {created} note(s) created."))
