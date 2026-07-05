import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from ingest.models import WeatherPoint, DailyForecast

DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
    "precipitation_probability_max",
    "weather_code",
]


def fetch_forecast(point):
    params = {
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Europe/Prague",
        "forecast_days": 16,
        "wind_speed_unit": "kmh",
    }
    if settings.OPEN_METEO_API_KEY:
        params["apikey"] = settings.OPEN_METEO_API_KEY

    url = f"{settings.OPEN_METEO_BASE_URL}/v1/forecast"
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def save_forecast(point, data, horizon, batch_time):
    """
    Insert one new DailyForecast row per date for this batch run.
    Skips dates already inserted today (one snapshot per calendar day).
    Returns count of new rows written.
    """
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    today_str = batch_time.date()
    rows_written = 0

    # Fetch which forecast_dates already have a row issued today for this point/horizon
    already_today = set(
        DailyForecast.objects
        .filter(point=point, horizon=horizon, issued_at__date=today_str)
        .values_list("forecast_date", flat=True)
    )

    new_rows = []
    for i, date_str in enumerate(dates):
        from datetime import date
        fd = date.fromisoformat(date_str)
        if fd in already_today:
            continue

        def get(var, idx=i):
            values = daily.get(var, [])
            return values[idx] if idx < len(values) else None

        new_rows.append(DailyForecast(
            point=point,
            forecast_date=date_str,
            horizon=horizon,
            issued_at=batch_time,
            temperature_max=get("temperature_2m_max"),
            temperature_min=get("temperature_2m_min"),
            precipitation_sum=get("precipitation_sum"),
            wind_speed_max=get("wind_speed_10m_max"),
            precipitation_prob_max=get("precipitation_probability_max"),
            weather_code=get("weather_code"),
        ))

    if new_rows:
        DailyForecast.objects.bulk_create(new_rows)
        rows_written = len(new_rows)

    return rows_written


class Command(BaseCommand):
    help = "Fetch short-range forecasts from Open-Meteo (one new snapshot per day for revision tracking)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horizon",
            default="short",
            choices=[DailyForecast.HORIZON_SHORT],
        )

    def handle(self, *args, **options):
        horizon = options["horizon"]
        points = list(WeatherPoint.objects.all())

        if not points:
            raise CommandError("No WeatherPoints found. Run 'python manage.py seed_points' first.")

        batch_time = timezone.now()
        self.stdout.write(f"Ingesting {horizon}-range forecasts (batch {batch_time:%Y-%m-%d %H:%M})...")
        total_rows = 0
        failed = []

        for point in points:
            try:
                data = fetch_forecast(point)
                rows = save_forecast(point, data, horizon, batch_time)
                total_rows += rows
                self.stdout.write(f"  {point.name}: {rows} new rows")
            except Exception as exc:
                self.stderr.write(f"  {point.name}: FAILED — {exc}")
                failed.append(point.name)

            time.sleep(0.1)

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed: {', '.join(failed)}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done. {total_rows} new rows across {len(points) - len(failed)} point(s)."
        ))
