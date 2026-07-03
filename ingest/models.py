from django.db import models


class WeatherPoint(models.Model):
    name = models.CharField(max_length=100)
    region = models.CharField(max_length=100)
    country = models.CharField(max_length=2)  # "CZ" or "SK"
    latitude = models.DecimalField(max_digits=6, decimal_places=4)
    longitude = models.DecimalField(max_digits=7, decimal_places=4)

    class Meta:
        ordering = ["country", "name"]

    def __str__(self):
        return f"{self.name} ({self.country})"


class DailyForecast(models.Model):
    HORIZON_SHORT = "short"
    HORIZON_MID = "mid"
    HORIZON_LONG = "long"
    HORIZON_HISTORICAL = "historical"
    HORIZON_CHOICES = [
        (HORIZON_SHORT, "Short (16 days)"),
        (HORIZON_MID, "Mid (6 weeks)"),
        (HORIZON_LONG, "Long (7 months)"),
        (HORIZON_HISTORICAL, "Historical (ERA5)"),
    ]

    point = models.ForeignKey(WeatherPoint, on_delete=models.CASCADE, related_name="forecasts")
    forecast_date = models.DateField()
    horizon = models.CharField(max_length=12, choices=HORIZON_CHOICES)
    fetched_at = models.DateTimeField(auto_now=True)

    temperature_max = models.FloatField(null=True, blank=True)
    temperature_min = models.FloatField(null=True, blank=True)
    precipitation_sum = models.FloatField(null=True, blank=True)
    wind_speed_max = models.FloatField(null=True, blank=True)
    precipitation_prob_max = models.IntegerField(null=True, blank=True)
    weather_code = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [("point", "forecast_date", "horizon")]
        ordering = ["forecast_date"]

    def __str__(self):
        return f"{self.point.name} {self.forecast_date} [{self.horizon}]"
