"""
Fetches SEAS5 seasonal (monthly) forecasts for all 22 points.
Run daily via Railway Cron. Appends a new snapshot per run.
"""
import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from ingest.models import WeatherPoint, MediumLongRangeForecast

MONTHLY_VARS = "temperature_2m_mean,precipitation_sum"


def fetch_seas5(point):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "monthly": MONTHLY_VARS,
        "forecast_months": 7,
        "timezone": "Europe/Prague",
    }
    api_key = getattr(settings, "OPEN_METEO_SEASONAL_API_KEY", "")
    if api_key:
        params["apikey"] = api_key

    url = f"{settings.OPEN_METEO_SEASONAL_URL}/v1/forecast"
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
                monthly = data.get("monthly", {})
                dates = monthly.get("time", [])
                temps = monthly.get("temperature_2m_mean", [])
                precips = monthly.get("precipitation_sum", [])

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
                self.stdout.write(f"  {point.name}: {len(rows)} months")
            except Exception as exc:
                self.stderr.write(f"  {point.name}: FAILED — {exc}")
                failed.append(point.name)

            time.sleep(0.15)

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed: {', '.join(failed)}"))
        self.stdout.write(self.style.SUCCESS(f"Done. {total} rows across {len(points) - len(failed)} points."))
