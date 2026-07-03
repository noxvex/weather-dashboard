from django.contrib import admin
from .models import WeatherPoint, DailyForecast


@admin.register(WeatherPoint)
class WeatherPointAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "country", "latitude", "longitude")
    list_filter = ("country",)


@admin.register(DailyForecast)
class DailyForecastAdmin(admin.ModelAdmin):
    list_display = ("point", "forecast_date", "horizon", "temperature_max", "temperature_min", "fetched_at")
    list_filter = ("horizon", "point__country")
    date_hierarchy = "forecast_date"
