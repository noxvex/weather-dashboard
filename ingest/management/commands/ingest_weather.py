import time
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
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
    """Call Open-Meteo and return the parsed JSON, or raise on HTTP error."""
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


def save_forecast(point, data, horizon):
    """Upsert one DailyForecast row per date returned by Open-Meteo."""
    daily = data.get("daily", {})
    dates = daily.get("time", [])
    rows_saved = 0

    for i, date_str in enumerate(dates):
        def get(var):
            values = daily.get(var, [])
            return values[i] if i < len(values) else None

        DailyForecast.objects.update_or_create(
            point=point,
            forecast_date=date_str,
            horizon=horizon,
            defaults={
                "temperature_max": get("temperature_2m_max"),
                "temperature_min": get("temperature_2m_min"),
                "precipitation_sum": get("precipitation_sum"),
                "wind_speed_max": get("wind_speed_10m_max"),
                "precipitation_prob_max": get("precipitation_probability_max"),
                "weather_code": get("weather_code"),
            },
        )
        rows_saved += 1

    return rows_saved


class Command(BaseCommand):
    help = "Fetch weather forecasts from Open-Meteo and store them in the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--horizon",
            default="short",
            choices=[DailyForecast.HORIZON_SHORT],
            help="Which forecast horizon to ingest (default: short).",
        )

    def handle(self, *args, **options):
        horizon = options["horizon"]
        points = list(WeatherPoint.objects.all())

        if not points:
            raise CommandError("No WeatherPoints found. Run 'python manage.py seed_points' first.")

        self.stdout.write(f"Ingesting {horizon}-range forecasts for {len(points)} points...")
        total_rows = 0
        failed = []

        for point in points:
            try:
                data = fetch_forecast(point)
                rows = save_forecast(point, data, horizon)
                total_rows += rows
                self.stdout.write(f"  {point.name}: {rows} days saved")
            except Exception as exc:
                self.stderr.write(f"  {point.name}: FAILED — {exc}")
                failed.append(point.name)

            time.sleep(0.1)  # stay well under 600 req/min free-tier limit

        if failed:
            self.stderr.write(self.style.WARNING(f"Failed points: {', '.join(failed)}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done. {total_rows} rows upserted across {len(points) - len(failed)} point(s)."
        ))
