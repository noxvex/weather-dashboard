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
    forecast_date = models.DateField()       # the day this forecast row is FOR (= target_date)
    horizon = models.CharField(max_length=12, choices=HORIZON_CHOICES)
    issued_at = models.DateTimeField(null=True, blank=True)  # when this snapshot was fetched; null on pre-Phase-4 rows
    fetched_at = models.DateTimeField(auto_now=True)         # always updated; use issued_at for revision logic

    temperature_max = models.FloatField(null=True, blank=True)
    temperature_min = models.FloatField(null=True, blank=True)
    precipitation_sum = models.FloatField(null=True, blank=True)
    wind_speed_max = models.FloatField(null=True, blank=True)
    precipitation_prob_max = models.IntegerField(null=True, blank=True)
    weather_code = models.IntegerField(null=True, blank=True)

    class Meta:
        # unique_together removed: now multiple rows per (point, date, horizon) allowed for revision tracking
        indexes = [
            models.Index(fields=["point", "forecast_date", "horizon"]),
            models.Index(fields=["issued_at"]),
        ]
        ordering = ["forecast_date", "-issued_at"]

    def __str__(self):
        return f"{self.point.name} {self.forecast_date} [{self.horizon}]"


class MediumLongRangeForecast(models.Model):
    HORIZON_EC46 = "ec46"
    HORIZON_SEAS5 = "seas5"
    HORIZON_CHOICES = [
        (HORIZON_EC46, "EC46 (6 týdnů)"),
        (HORIZON_SEAS5, "SEAS5 (7 měsíců)"),
    ]

    point = models.ForeignKey(WeatherPoint, on_delete=models.CASCADE, related_name="medium_long_forecasts")
    target_date = models.DateField()
    issued_at = models.DateTimeField()
    horizon = models.CharField(max_length=10, choices=HORIZON_CHOICES)
    temp_mean = models.FloatField(null=True, blank=True)
    temp_anomaly = models.FloatField(null=True, blank=True)  # vs climatological mean; populated once HistoricalActual has enough data
    precip_probability = models.FloatField(null=True, blank=True)
    source_model = models.CharField(max_length=50, blank=True)

    class Meta:
        indexes = [models.Index(fields=["point", "target_date", "horizon", "issued_at"])]
        ordering = ["target_date", "-issued_at"]

    def __str__(self):
        return f"{self.point.name} {self.target_date} [{self.horizon}]"


class HistoricalActual(models.Model):
    point = models.ForeignKey(WeatherPoint, on_delete=models.CASCADE, related_name="historical_actuals")
    date = models.DateField()
    temp_min = models.FloatField(null=True, blank=True)
    temp_max = models.FloatField(null=True, blank=True)
    precip_mm = models.FloatField(null=True, blank=True)
    wind_kmh = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = [("point", "date")]  # one ground-truth row per point per day
        ordering = ["date"]

    def __str__(self):
        return f"{self.point.name} ERA5 {self.date}"


class PollenRecord(models.Model):
    point = models.ForeignKey(WeatherPoint, on_delete=models.CASCADE, related_name="pollen_records")
    date = models.DateField()
    issued_at = models.DateTimeField()
    birch = models.FloatField(null=True, blank=True)      # grains/m³ daily max
    grass = models.FloatField(null=True, blank=True)
    ragweed = models.FloatField(null=True, blank=True)
    alder = models.FloatField(null=True, blank=True)
    mugwort = models.FloatField(null=True, blank=True)
    aqi_european = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["point", "date", "issued_at"])]
        ordering = ["date", "-issued_at"]

    def __str__(self):
        return f"{self.point.name} pollen {self.date}"
