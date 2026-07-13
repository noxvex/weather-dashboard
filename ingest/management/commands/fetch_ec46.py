"""
Fetches EC46 (ECMWF IFS, 46-day ensemble mean) forecasts for all 22 points
from the same free Open-Meteo seasonal endpoint as SEAS5. Run manually via
`railway ssh` until a scheduled worker/cron service exists (see fetch_seasonal.py
and CLAUDE.md — no such service is actually configured in Railway today,
despite fetch_seasonal's docstring). Appends a new snapshot per run (distinct
issued_at) so revision tracking can compare snapshots over time.
"""
import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from ingest.models import WeatherPoint, MediumLongRangeForecast

# EC46 is a distinct model from the default "ECMWF Seasonal Seamless" that
# fetch_seasonal.py's SEAS5 call implicitly uses (Open-Meteo blends EC46 into
# the first 46 days there when no `models` param is passed) — this command
# explicitly requests the pure EC46 ensemble mean via `models=`, confirmed
# against the live API (ecmwf_ec46_ensemble_mean; the seemingly-plausible
# ecmwf_ifs_046_ensemble_mean variant does NOT exist and 400s).
# precip_probability holds the raw mm sum, same documented placeholder as
# SEAS5 — Open-Meteo's seasonal API has no true precip-probability variable.
DAILY_VARS = "temperature_2m_mean,precipitation_sum"
FORECAST_DAYS = 46  # EC46's documented horizon; requesting more returns null past day ~46
MODEL = "ecmwf_ec46_ensemble_mean"


def fetch_ec46(point):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "daily": DAILY_VARS,
        "forecast_days": FORECAST_DAYS,
        "models": MODEL,
        "timezone": "Europe/Prague",
    }
    api_key = getattr(settings, "OPEN_METEO_SEASONAL_API_KEY", "")
    if api_key:
        params["apikey"] = api_key

    url = f"{settings.OPEN_METEO_SEASONAL_URL}/v1/seasonal"
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


class Command(BaseCommand):
    help = "Fetch EC46 46-day ensemble-mean forecasts for all 22 points."

    def handle(self, *args, **options):
        points = list(WeatherPoint.objects.all())
        batch_time = timezone.now()
        total = 0
        failed = []

        self.stdout.write(f"fetch_ec46: {len(points)} points, batch {batch_time:%Y-%m-%d %H:%M}")

        for point in points:
            try:
                data = fetch_ec46(point)
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                temps = daily.get("temperature_2m_mean", [])
                precips = daily.get("precipitation_sum", [])

                rows = []
                for i, date_str in enumerate(dates):
                    rows.append(MediumLongRangeForecast(
                        point=point,
                        target_date=date_str,
                        issued_at=batch_time,
                        horizon=MediumLongRangeForecast.HORIZON_EC46,
                        temp_mean=temps[i] if i < len(temps) else None,
                        temp_anomaly=None,  # computed later when HistoricalActual has enough data
                        precip_probability=precips[i] if i < len(precips) else None,
                        source_model="EC46",
                    ))

                MediumLongRangeForecast.objects.bulk_create(rows)
                total += len(rows)
                self.stdout.write(f"  {point.name}: {len(rows)} days")
            except Exception as exc:
                self.stderr.write(f"  {point.name}: FAILED — {exc}")
                failed.append(point.name)

            time.sleep(0.15)

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed: {', '.join(failed)}"))
        self.stdout.write(self.style.SUCCESS(f"Done. {total} rows across {len(points) - len(failed)} points."))
