"""
Fetches 5-day pollen + AQI forecasts from Open-Meteo Air Quality API.
Aggregates hourly data to daily max per variable before storing.
Run daily via Railway Cron.
"""
import time
from collections import defaultdict

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from ingest.models import WeatherPoint, PollenRecord

HOURLY_VARS = (
    "birch_pollen,grass_pollen,ragweed_pollen,alder_pollen,mugwort_pollen,european_aqi"
)
VAR_MAP = {
    "birch_pollen": "birch",
    "grass_pollen": "grass",
    "ragweed_pollen": "ragweed",
    "alder_pollen": "alder",
    "mugwort_pollen": "mugwort",
    "european_aqi": "aqi_european",
}


def fetch_pollen(point):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "hourly": HOURLY_VARS,
        "forecast_days": 5,
        "timezone": "Europe/Prague",
    }
    api_key = getattr(settings, "OPEN_METEO_AIRQUALITY_API_KEY", "")
    if api_key:
        params["apikey"] = api_key

    url = f"{settings.OPEN_METEO_AIRQUALITY_URL}/v1/air-quality"
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def aggregate_to_daily_max(data):
    """
    Open-Meteo air quality returns hourly values.
    Aggregate to daily max — relevant for peak pollen exposure.
    Returns dict: {date_str: {field_name: max_value}}
    """
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    daily = defaultdict(lambda: defaultdict(list))
    for i, dt_str in enumerate(times):
        date_str = dt_str[:10]  # "2026-07-04T12:00" → "2026-07-04"
        for api_var, model_field in VAR_MAP.items():
            vals = hourly.get(api_var, [])
            v = vals[i] if i < len(vals) else None
            if v is not None:
                daily[date_str][model_field].append(v)

    result = {}
    for date_str, fields in daily.items():
        result[date_str] = {
            field: max(values) if values else None
            for field, values in fields.items()
        }
    return result


class Command(BaseCommand):
    help = "Fetch 5-day pollen and AQI forecasts for all 22 points."

    def handle(self, *args, **options):
        points = list(WeatherPoint.objects.all())
        batch_time = timezone.now()
        total = 0
        failed = []

        self.stdout.write(f"fetch_pollen: {len(points)} points, batch {batch_time:%Y-%m-%d %H:%M}")

        for point in points:
            try:
                data = fetch_pollen(point)
                daily = aggregate_to_daily_max(data)

                rows = [
                    PollenRecord(
                        point=point,
                        date=date_str,
                        issued_at=batch_time,
                        birch=fields.get("birch"),
                        grass=fields.get("grass"),
                        ragweed=fields.get("ragweed"),
                        alder=fields.get("alder"),
                        mugwort=fields.get("mugwort"),
                        aqi_european=int(fields["aqi_european"]) if fields.get("aqi_european") is not None else None,
                    )
                    for date_str, fields in sorted(daily.items())
                ]

                PollenRecord.objects.bulk_create(rows)
                total += len(rows)
                self.stdout.write(f"  {point.name}: {len(rows)} days")
            except Exception as exc:
                self.stderr.write(f"  {point.name}: FAILED — {exc}")
                failed.append(point.name)

            time.sleep(0.15)

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed: {', '.join(failed)}"))
        self.stdout.write(self.style.SUCCESS(f"Done. {total} PollenRecord rows."))
