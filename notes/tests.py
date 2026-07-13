"""
Minimal regression suite — not meant for full coverage. Each test locks in
a fix that was previously silently broken by an unrelated change elsewhere,
so the same class of bug gets caught automatically instead of by hand.
"""
import re
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from ingest.models import DailyForecast, HistoricalActual, MediumLongRangeForecast, WeatherPoint
from notes.templatetags.weather_tags import fc_fill_style

User = get_user_model()


def _same_month_day(d, year):
    """Shift `d` to `year`, keeping month/day (falls back a day for Feb 29)."""
    try:
        return d.replace(year=year)
    except ValueError:
        return d.replace(year=year, day=28)


class HistorieVlastniRozsahTest(TestCase):
    """
    Manual comparison (rozsah=vlastni) with a day-of-year range reaching into
    the future for the current year. `current_year` must be pinned to the
    real current year (from the full, unfiltered series) — not to whatever
    year happens to be the max *after* the doy filter is applied, which
    silently anchors the "last N years" window a year (or more) too early
    whenever the requested range hasn't happened yet this year.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="tester", password="x")
        self.client.force_login(self.user)
        self.point = WeatherPoint.objects.create(
            name="Testov", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )

        self.today = date.today()
        # A range entirely in the future relative to "today" — the exact
        # scenario that triggered the bug.
        self.od_date = self.today + timedelta(days=10)
        self.do_date = self.today + timedelta(days=20)

        # Current year: data exists only up to today (like real ERA5 backfill),
        # nothing yet inside [od_date, do_date].
        HistoricalActual.objects.create(
            point=self.point, date=self.today, temp_min=15.0, temp_max=25.0,
        )

        # 11 previous years: full data across the requested doy window, so
        # they're eligible to appear in the comparison.
        self.past_years = list(range(self.today.year - 11, self.today.year))
        for year in self.past_years:
            start = _same_month_day(self.od_date, year)
            end = _same_month_day(self.do_date, year)
            d = start
            while d <= end:
                HistoricalActual.objects.create(
                    point=self.point, date=d, temp_min=10.0, temp_max=20.0,
                )
                d += timedelta(days=1)

    def _get(self, roky):
        return self.client.get("/historie/", {
            "bod": self.point.pk,
            "rozsah": "vlastni",
            "od": f"{self.od_date.day}.{self.od_date.month}",
            "do": f"{self.do_date.day}.{self.do_date.month}",
            "roky": roky,
        })

    def test_roky_window_anchors_to_true_current_year(self):
        # roky=2 → only the single most recent past year (current year itself
        # has no rows in this future doy slice, so it can't appear).
        years_2 = self._get(roky=2).context["chart_json"]["years"]
        self.assertEqual({y["year"] for y in years_2}, {self.today.year - 1})

        # roky=8 → the 7 most recent past years.
        years_8 = self._get(roky=8).context["chart_json"]["years"]
        expected_8 = set(range(self.today.year - 7, self.today.year))
        self.assertEqual({y["year"] for y in years_8}, expected_8)

    def test_changing_roky_changes_visible_year_count(self):
        counts = [len(self._get(roky=n).context["chart_json"]["years"]) for n in (2, 8, 12)]
        self.assertEqual(counts, sorted(counts))  # non-decreasing as the window widens
        self.assertLess(counts[0], counts[1])
        self.assertLess(counts[1], counts[2])


class HistorieForecastOverlayTest(TestCase):
    """
    Dashed forecast continuation of the current year on the Historie overlay:
    temperature merges short-range + EC46 + SEAS5 (in that priority),
    precipitation must stay short-range only, pct mode gets nothing, and the
    first forecast point repeats the last real one so the line connects.
    """

    def setUp(self):
        # The MediumLongRangeForecast fetch is cached for 1 hour (LocMemCache
        # survives across tests in-process) — clear per test or fixtures from
        # a previous method would leak into this one.
        cache.clear()
        self.user = User.objects.create_user(username="tester4", password="x")
        self.client.force_login(self.user)
        self.point = WeatherPoint.objects.create(
            name="Testovec", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )
        self.today = date.today()
        # Real ERA5 rows up to today (current year only — a January run must
        # not create rows that belong to the previous year's trace).
        for offset in range(5, -1, -1):
            d = self.today - timedelta(days=offset)
            if d.year == self.today.year:
                HistoricalActual.objects.create(
                    point=self.point, date=d, temp_min=10.0, temp_max=20.0, precip_mm=2.0,
                )

    def tearDown(self):
        cache.clear()

    def _make_short_forecasts(self):
        """Short-range rows today..today+3, temp midpoint 17.0, precip 3.0."""
        issued_at = timezone.now()
        for offset in range(4):
            DailyForecast.objects.create(
                point=self.point, forecast_date=self.today + timedelta(days=offset),
                horizon=DailyForecast.HORIZON_SHORT, issued_at=issued_at,
                temperature_min=12.0, temperature_max=22.0, precipitation_sum=3.0,
            )

    def _make_mlr_forecasts(self):
        """
        EC46 overlapping the short range (today+2, 99.0 — must lose to short)
        and beyond it (today+20, 18.0); SEAS5 on the same far date (5.0 —
        must lose to EC46) and further out (today+40, 6.0).
        """
        issued_at = timezone.now()
        for offset, temp in [(2, 99.0), (20, 18.0)]:
            MediumLongRangeForecast.objects.create(
                point=self.point, target_date=self.today + timedelta(days=offset),
                issued_at=issued_at, horizon=MediumLongRangeForecast.HORIZON_EC46,
                temp_mean=temp, precip_probability=80.0,
            )
        for offset, temp in [(20, 5.0), (40, 6.0)]:
            MediumLongRangeForecast.objects.create(
                point=self.point, target_date=self.today + timedelta(days=offset),
                issued_at=issued_at, horizon=MediumLongRangeForecast.HORIZON_SEAS5,
                temp_mean=temp, precip_probability=80.0,
            )

    def _get(self, **extra):
        params = {"bod": self.point.pk, "rozsah": "plna", "g": "d", "m": "t"}
        params.update(extra)
        return self.client.get("/historie/", params)

    def _current_entry(self, resp):
        years = resp.context["chart_json"]["years"]
        return next(y for y in years if y["year"] == self.today.year)

    def _doy(self, d):
        return d.timetuple().tm_yday

    def test_forecast_keys_present_with_short_range_data(self):
        self._make_short_forecasts()
        entry = self._current_entry(self._get())
        self.assertIn("forecast_x", entry)
        self.assertIn("forecast_values", entry)
        self.assertGreater(len(entry["forecast_x"]), 1)

    def test_forecast_keys_absent_without_forecast_data(self):
        entry = self._current_entry(self._get())
        self.assertNotIn("forecast_x", entry)
        self.assertNotIn("forecast_values", entry)

    def test_forecast_connects_to_last_real_point(self):
        self._make_short_forecasts()
        entry = self._current_entry(self._get())
        self.assertEqual(entry["forecast_x"][0], entry["x"][-1])
        self.assertEqual(entry["forecast_values"][0], entry["values"][-1])

    def test_temp_merges_all_sources_with_short_range_priority(self):
        self._make_short_forecasts()
        self._make_mlr_forecasts()
        entry = self._current_entry(self._get(m="t"))
        by_x = dict(zip(entry["forecast_x"], entry["forecast_values"]))
        # Short range wins over EC46's 99.0 on the overlapping date
        self.assertEqual(by_x[self._doy(self.today + timedelta(days=2))], 17.0)
        # EC46 wins over SEAS5's 5.0 beyond the short range
        self.assertEqual(by_x[self._doy(self.today + timedelta(days=20))], 18.0)
        # SEAS5 fills dates only it covers
        self.assertEqual(by_x[self._doy(self.today + timedelta(days=40))], 6.0)

    def test_precip_never_pulls_medium_long_range(self):
        self._make_short_forecasts()
        self._make_mlr_forecasts()
        entry = self._current_entry(self._get(m="p"))
        # Nothing beyond the last short-range date (today+3), even though
        # EC46/SEAS5 fixtures exist at today+20 and today+40
        self.assertLessEqual(max(entry["forecast_x"]), self._doy(self.today + timedelta(days=3)))

    def test_pct_mode_never_attaches_forecast(self):
        self._make_short_forecasts()
        entry = self._current_entry(self._get(rezim="pct"))
        self.assertNotIn("forecast_x", entry)
        self.assertNotIn("forecast_values", entry)


class PointDetailBranchSeparationTest(TestCase):
    """?bod=<id> (city view) and ?land=cz/sk (national view) must never mix data."""

    def setUp(self):
        self.user = User.objects.create_user(username="tester2", password="x")
        self.client.force_login(self.user)
        self.cz_point = WeatherPoint.objects.create(
            name="Brno", region="Jihomoravský", country="CZ", latitude=49.19, longitude=16.6,
        )
        self.sk_point = WeatherPoint.objects.create(
            name="Kosice", region="Kosicky", country="SK", latitude=48.72, longitude=21.25,
        )
        DailyForecast.objects.create(
            point=self.cz_point, forecast_date=date.today() + timedelta(days=1),
            horizon=DailyForecast.HORIZON_SHORT, temperature_min=5.0, temperature_max=12.0,
            issued_at=timezone.now(),
        )

    def test_bod_param_selects_city_view(self):
        resp = self.client.get("/bod/", {"bod": self.cz_point.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected"], self.cz_point)
        self.assertIsNone(resp.context["selected_land"])
        self.assertNotIn(
            "Detailní předpověď a graf jsou k dispozici",
            resp.content.decode(),
        )

    def test_land_param_selects_national_view_not_city(self):
        resp = self.client.get("/bod/", {"land": "cz"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["selected_land"], "cz")
        self.assertEqual(resp.context["nat_country"], "CZ")
        content = resp.content.decode()
        self.assertIn("Detailní předpověď a graf jsou k dispozici", content)
        # City-view-only forecast day-count form must not leak into national view
        self.assertNotIn('id="forecast-days-filter"', content)

    def test_land_sk_does_not_return_cz_national_data(self):
        resp = self.client.get("/bod/", {"land": "sk"})
        self.assertEqual(resp.context["selected_land"], "sk")
        self.assertEqual(resp.context["nat_country"], "SK")


class FcFillRegressionTest(TestCase):
    """
    fc-fill bar must reflect real temperatures, not a hardcoded
    left:15%/width:70% for every row (regressed once already).
    """

    def test_fc_fill_style_differs_for_different_temperatures(self):
        cold = fc_fill_style(-5, 2)
        warm = fc_fill_style(18, 27)
        self.assertNotEqual(cold, warm)

    def test_point_detail_renders_varying_fc_fill_bars(self):
        user = User.objects.create_user(username="tester3", password="x")
        self.client.force_login(user)
        point = WeatherPoint.objects.create(
            name="Ostrava", region="Moravskoslezsky", country="CZ", latitude=49.83, longitude=18.28,
        )
        issued_at = timezone.now()
        DailyForecast.objects.create(
            point=point, forecast_date=date.today() + timedelta(days=1),
            horizon=DailyForecast.HORIZON_SHORT, temperature_min=-5.0, temperature_max=2.0,
            issued_at=issued_at,
        )
        DailyForecast.objects.create(
            point=point, forecast_date=date.today() + timedelta(days=2),
            horizon=DailyForecast.HORIZON_SHORT, temperature_min=18.0, temperature_max=27.0,
            issued_at=issued_at,
        )

        resp = self.client.get("/bod/", {"bod": point.pk})
        content = resp.content.decode()
        styles = set(re.findall(r'class="fc-fill" style="([^"]+)"', content))
        self.assertGreater(len(styles), 1, "all fc-fill bars rendered identically — regression")
