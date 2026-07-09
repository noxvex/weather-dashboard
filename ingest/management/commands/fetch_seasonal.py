"""
Fetches SEAS5 seasonal forecasts for all 22 points from the free Open-Meteo
seasonal endpoint. Run daily via Railway Cron. Appends a new snapshot per run
(distinct issued_at) so revision tracking can compare snapshots over time.
"""
import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from ingest.models import WeatherPoint, MediumLongRangeForecast

# SEAS5 returns a daily ensemble mean out to ~7 months. We store the ensemble
# mean here; precip_probability holds the raw mm sum for now (a real probability
# will be derived once HistoricalActual has enough baseline data).
# The seasonal host uses the /v1/seasonal path with daily/forecast_days params —
# NOT /v1/forecast with monthly/forecast_months (that returns empty data).
DAILY_VARS = "temperature_2m_mean,precipitation_sum"
FORECAST_DAYS = 214  # ~7 months, the seasonal horizon


def fetch_seas5(point):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "daily": DAILY_VARS,
        "forecast_days": FORECAST_DAYS,
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
    help = "Fetch SEAS5 seasonal monthly forecasts for all 22 points."

    def handle(self, *args, **options):
        points = list(WeatherPoint.objects.all())
        batch_time = timezone.now()
        total = 0
        failed = []

        self.stdout.write(f"fetch_seasonal: {len(points)} points, batch {batch_time:%Y-%m-%d %H:%M}")

        for point in points:
            try:
                data = fetch_seas5(point)
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
                        horizon=MediumLongRangeForecast.HORIZON_SEAS5,
                        temp_mean=temps[i] if i < len(temps) else None,
                        temp_anomaly=None,  # computed later when HistoricalActual has enough data
                        precip_probability=precips[i] if i < len(precips) else None,
                        source_model="SEAS5",
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
