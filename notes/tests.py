"""
Minimal regression suite — not meant for full coverage. Each test locks in
a fix that was previously silently broken by an unrelated change elsewhere,
so the same class of bug gets caught automatically instead of by hand.
"""
import re
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ingest.models import DailyForecast, HistoricalActual, WeatherPoint
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
