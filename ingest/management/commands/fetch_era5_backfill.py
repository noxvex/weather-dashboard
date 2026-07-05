"""
One-time (and ongoing daily) backfill of ERA5 reanalysis data into HistoricalActual.
Chunks by calendar year so any single failure only loses one year for one point.
Uses bulk_create with ignore_conflicts=True so re-running is safe.

Usage:
    python manage.py fetch_era5_backfill                    # 2010-01-01 to today
    python manage.py fetch_era5_backfill --start 2020-01-01 --end 2020-12-31
    python manage.py fetch_era5_backfill --start 2010-01-01 --end 2010-12-31  # test one year first
"""
import time
from datetime import date, timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from ingest.models import WeatherPoint, HistoricalActual

DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
DEFAULT_START = date(2010, 1, 1)


def fetch_era5_year(point, year_start, year_end):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "start_date": year_start.isoformat(),
        "end_date": year_end.isoformat(),
        "daily": DAILY_VARS,
        "timezone": "Europe/Prague",
        "wind_speed_unit": "kmh",
    }
    api_key = getattr(settings, "OPEN_METEO_ARCHIVE_API_KEY", "")
    if api_key:
        params["apikey"] = api_key

    url = f"{settings.OPEN_METEO_ARCHIVE_URL}/v1/archive"
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


class Command(BaseCommand):
    help = "Backfill ERA5 historical actuals from 2010 (or custom range) to today."

    def add_arguments(self, parser):
        parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 2010-01-01)")
        parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")

    def handle(self, *args, **options):
        start = date.fromisoformat(options["start"]) if options["start"] else DEFAULT_START
        end = date.fromisoformat(options["end"]) if options["end"] else date.today() - timedelta(days=5)
        # ERA5 has a ~5-day lag before data is finalised; stop 5 days before today

        points = list(WeatherPoint.objects.all())
        years = list(range(start.year, end.year + 1))

        self.stdout.write(
            f"ERA5 backfill: {len(points)} points × {len(years)} year(s) "
            f"({start} → {end}). This may take several minutes."
        )

        total_rows = 0
        failed = []

        for point in points:
            point_rows = 0
            for year in years:
                year_start = max(start, date(year, 1, 1))
                year_end = min(end, date(year, 12, 31))
                if year_start > year_end:
                    continue

                try:
                    data = fetch_era5_year(point, year_start, year_end)
                    daily = data.get("daily", {})
                    dates = daily.get("time", [])

                    rows = []
                    for i, d_str in enumerate(dates):
                        def get(var, idx=i):
                            vals = daily.get(var, [])
                            return vals[idx] if idx < len(vals) else None

                        rows.append(HistoricalActual(
                            point=point,
                            date=d_str,
                            temp_max=get("temperature_2m_max"),
                            temp_min=get("temperature_2m_min"),
                            precip_mm=get("precipitation_sum"),
                            wind_kmh=get("wind_speed_10m_max"),
                        ))

                    created = HistoricalActual.objects.bulk_create(rows, ignore_conflicts=True)
                    point_rows += len(created)
                    total_rows += len(created)

                except Exception as exc:
                    self.stderr.write(f"  {point.name} {year}: FAILED — {exc}")
                    failed.append(f"{point.name}/{year}")

                time.sleep(0.15)  # ERA5 archive is a different host, be polite

            self.stdout.write(f"  {point.name}: {point_rows} rows inserted")

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed chunks: {', '.join(failed)}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done. {total_rows} HistoricalActual rows inserted across {len(points)} points."
        ))
