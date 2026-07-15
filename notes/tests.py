"""
Minimal regression suite — not meant for full coverage. Each test locks in
a fix that was previously silently broken by an unrelated change elsewhere,
so the same class of bug gets caught automatically instead of by hand.
"""
import re
from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ingest.models import DailyForecast, HistoricalActual, MediumLongRangeForecast, WeatherPoint
from notes.models import HistoriePin, Note
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


class RevisionTrackerMlrBucketTest(TestCase):
    """
    Střednědobá (EC46) / dlouhodobá (SEAS5) revision buckets on /revize/ —
    same not_enough_data fallback as aktuální, but comparing temp_mean
    directly (1.0 °C threshold, noisier than aktuální's 0.5) and
    precip_probability as percentage points, not mm.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="tester5", password="x")
        self.client.force_login(self.user)
        self.point = WeatherPoint.objects.create(
            name="Testovice", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )

    def _get(self, bucket):
        return self.client.get(reverse("notes:revision_tracker"), {"rozsah": bucket})

    def test_not_enough_data_with_zero_snapshots(self):
        resp = self._get("strednedobe")
        self.assertTrue(resp.context["not_enough_data"])
        self.assertEqual(resp.context["revisions"], [])

    def test_not_enough_data_with_single_snapshot(self):
        MediumLongRangeForecast.objects.create(
            point=self.point, target_date=date.today() + timedelta(days=10),
            issued_at=timezone.now(), horizon=MediumLongRangeForecast.HORIZON_EC46,
            temp_mean=15.0, precip_probability=40.0,
        )
        resp = self._get("strednedobe")
        self.assertTrue(resp.context["not_enough_data"])

    def test_revision_shown_above_threshold_hidden_below(self):
        # 2.0 °C swing must show, 0.5 °C swing must not — aktuální's 0.5
        # threshold does not apply here, these buckets use 1.0.
        target_above = date.today() + timedelta(days=10)
        target_below = date.today() + timedelta(days=11)
        previous_issued = timezone.now() - timedelta(days=1)
        latest_issued = timezone.now()

        for target, prev_temp, latest_temp, prev_prob, latest_prob in [
            (target_above, 10.0, 12.0, 30.0, 45.0),
            (target_below, 10.0, 10.5, 30.0, 32.0),
        ]:
            MediumLongRangeForecast.objects.create(
                point=self.point, target_date=target, issued_at=previous_issued,
                horizon=MediumLongRangeForecast.HORIZON_EC46,
                temp_mean=prev_temp, precip_probability=prev_prob,
            )
            MediumLongRangeForecast.objects.create(
                point=self.point, target_date=target, issued_at=latest_issued,
                horizon=MediumLongRangeForecast.HORIZON_EC46,
                temp_mean=latest_temp, precip_probability=latest_prob,
            )

        resp = self._get("strednedobe")
        self.assertNotIn("not_enough_data", resp.context)
        revisions = resp.context["revisions"]
        dates_shown = {r["date"] for r in revisions}
        self.assertIn(target_above, dates_shown)
        self.assertNotIn(target_below, dates_shown)

        row = next(r for r in revisions if r["date"] == target_above)
        self.assertEqual(row["temp_delta"], 2.0)
        self.assertEqual(row["precip_prob_delta"], 15)

        # precip_prob_delta is a raw mm delta (documented placeholder, not a
        # real probability) — the rendered label must say "mm", never "pb".
        content = resp.content.decode()
        self.assertIn("+15&thinsp;mm", content)
        self.assertNotIn("pb", content)

    def test_seas5_fixtures_do_not_leak_into_ec46_bucket(self):
        MediumLongRangeForecast.objects.create(
            point=self.point, target_date=date.today() + timedelta(days=100),
            issued_at=timezone.now() - timedelta(days=1),
            horizon=MediumLongRangeForecast.HORIZON_SEAS5, temp_mean=8.0, precip_probability=20.0,
        )
        MediumLongRangeForecast.objects.create(
            point=self.point, target_date=date.today() + timedelta(days=100),
            issued_at=timezone.now(),
            horizon=MediumLongRangeForecast.HORIZON_SEAS5, temp_mean=10.0, precip_probability=25.0,
        )
        resp = self._get("strednedobe")
        self.assertTrue(resp.context["not_enough_data"])


class HistoriePinLifecycleTest(TestCase):
    """
    Pins share the notes lifecycle run by prune_notes: unpinned pins are
    soft-hidden at 14 days and hard-deleted at 30; pinned pins are exempt.
    A pin expiring must NOT take its cross-posted Aktuality card with it
    (the card has its own lifecycle) — only explicit deletion cascades.
    """

    def setUp(self):
        self.author = User.objects.create_user(username="pinworker", password="x")

    def _pin(self, days_old, is_pinned=False):
        pin = HistoriePin.objects.create(
            author=self.author, body="test pin", sel="cz",
            od="1.6", do="31.8", roky=5, metric="t",
        )
        # created_at is auto_now_add — backdate via queryset update
        HistoriePin.objects.filter(pk=pin.pk).update(
            created_at=timezone.now() - timedelta(days=days_old),
            is_pinned=is_pinned,
        )
        pin.refresh_from_db()
        return pin

    def test_unpinned_pin_soft_hidden_after_14_days(self):
        old = self._pin(15)
        fresh = self._pin(3)
        call_command("prune_notes", stdout=StringIO())
        old.refresh_from_db()
        fresh.refresh_from_db()
        self.assertTrue(old.is_hidden)
        self.assertFalse(fresh.is_hidden)

    def test_pinned_pin_survives_forever(self):
        pin = self._pin(400, is_pinned=True)
        call_command("prune_notes", stdout=StringIO())
        pin.refresh_from_db()
        self.assertFalse(pin.is_hidden)

    def test_unpinned_pin_hard_deleted_after_30_days_card_survives(self):
        pin = self._pin(31)
        card = Note.objects.create(author=self.author, body="test pin")
        HistoriePin.objects.filter(pk=pin.pk).update(feed_note=card)
        Note.objects.filter(pk=card.pk).update(is_pinned=True)
        call_command("prune_notes", stdout=StringIO())
        self.assertFalse(HistoriePin.objects.filter(pk=pin.pk).exists())
        self.assertTrue(Note.objects.filter(pk=card.pk).exists())

    def test_explicit_delete_cascades_to_card(self):
        pin = self._pin(1)
        card = Note.objects.create(author=self.author, body="test pin")
        pin.feed_note = card
        pin.save(update_fields=["feed_note"])
        pin.delete()
        self.assertFalse(Note.objects.filter(pk=card.pk).exists())

    def test_hidden_pin_leaves_pins_context(self):
        from notes.views import _pins_context
        visible = self._pin(3)
        hidden = self._pin(15)
        call_command("prune_notes", stdout=StringIO())
        pins = _pins_context(
            self.author, sel="cz", metric="t", gran="d",
            country="CZ", point_id=None, rows=[],
        )
        ids = [p["id"] for p in pins]
        self.assertIn(visible.pk, ids)
        self.assertNotIn(hidden.pk, ids)


class PinCreateViewTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="creator", password="x")
        self.client.force_login(self.user)
        self.params = {
            "sel": "cz", "od": "1.6", "do": "31.8", "roky": 5,
            "metric": "t", "body": "Kouknětě na tyhle data...",
        }

    def test_create_with_feed_cross_posts(self):
        resp = self.client.post(reverse("pin_create"), {**self.params, "show_in_feed": "on"})
        self.assertEqual(resp.status_code, 302)
        pin = HistoriePin.objects.get()
        self.assertEqual(pin.author, self.user)
        self.assertTrue(pin.show_in_feed)
        self.assertIsNotNone(pin.feed_note)
        self.assertEqual(pin.feed_note.body, pin.body)
        self.assertEqual(pin.feed_note.country, Note.COUNTRY_CZ)

    def test_create_historie_only_makes_no_note(self):
        self.client.post(reverse("pin_create"), self.params)
        pin = HistoriePin.objects.get()
        self.assertFalse(pin.show_in_feed)
        self.assertEqual(Note.objects.count(), 0)

    def test_tampered_params_rejected(self):
        resp = self.client.post(reverse("pin_create"), {**self.params, "od": "99.99"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(HistoriePin.objects.count(), 0)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("pin_create"), self.params)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(HistoriePin.objects.count(), 0)


class PinPermissionsTest(TestCase):
    """Author edits/deletes their own pin; leader/admin any; pin toggle is
    leader/admin only. Mirrors the Aktuality notes rules (_can_modify/_can_pin)."""

    def setUp(self):
        self.author = User.objects.create_user(username="autor", password="x")
        self.other = User.objects.create_user(username="cizi", password="x")
        self.leader = User.objects.create_user(username="vedouci", password="x", role="leader")
        self.pin = HistoriePin.objects.create(
            author=self.author, body="original", sel="cz",
            od="1.6", do="31.8", roky=5, metric="t",
        )

    def test_stranger_cannot_edit_or_delete(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("pin_edit", args=[self.pin.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("pin_delete", args=[self.pin.pk])).status_code, 404)
        self.assertTrue(HistoriePin.objects.filter(pk=self.pin.pk).exists())

    def test_author_can_delete_own(self):
        self.client.force_login(self.author)
        resp = self.client.post(reverse("pin_delete", args=[self.pin.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(HistoriePin.objects.filter(pk=self.pin.pk).exists())

    def test_leader_can_delete_any(self):
        self.client.force_login(self.leader)
        self.client.post(reverse("pin_delete", args=[self.pin.pk]))
        self.assertFalse(HistoriePin.objects.filter(pk=self.pin.pk).exists())

    def test_edit_syncs_feed_card_body(self):
        card = Note.objects.create(author=self.author, body="original")
        self.pin.feed_note = card
        self.pin.save(update_fields=["feed_note"])
        self.client.force_login(self.author)
        resp = self.client.post(reverse("pin_edit", args=[self.pin.pk]), {"body": "upraveno"})
        self.assertEqual(resp.status_code, 302)
        self.pin.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual(self.pin.body, "upraveno")
        self.assertEqual(card.body, "upraveno")

    def test_toggle_leader_only(self):
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(reverse("pin_toggle", args=[self.pin.pk])).status_code, 404)
        self.client.force_login(self.leader)
        self.client.post(reverse("pin_toggle", args=[self.pin.pk]))
        self.pin.refresh_from_db()
        self.assertTrue(self.pin.is_pinned)


class HistoriePinsRoundTwoTest(TestCase):
    """
    Fixes from the first live feedback round: manual-comparison params must
    survive every view switch, pins are unlimited, the feed card carries the
    stats table, and Subhistorie lists all pins with weekly progression.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="round2", password="x")
        self.client.force_login(self.user)
        self.point = WeatherPoint.objects.create(
            name="Testov", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )
        today = date.today()
        # Three weeks of daily June data across 2 years (covers od=1.6 do=21.6),
        # temps rising day by day so weekly deltas come out positive
        for year in (today.year - 1, today.year):
            for day in range(1, 22):
                HistoricalActual.objects.create(
                    point=self.point, date=date(year, 6, day),
                    temp_min=10.0 + day * 0.2, temp_max=20.0 + day * 0.2, precip_mm=1.0,
                )

    def test_switches_keep_manual_comparison_params(self):
        resp = self.client.get("/historie/?rozsah=vlastni&od=1.6&do=21.6&roky=8&bod=cz&m=t")
        html = resp.content.decode().replace("&amp;", "&")
        # rezim toggle keeps od/do/roky (this was the "roky jumps back to 5" bug)
        self.assertIn("rezim=pct&od=1.6&do=21.6&roky=8", html)
        # seg links keep them too
        self.assertIn("rozsah=plna&od=1.6&do=21.6&roky=8", html)
        # bod/metric selects submit them as hidden inputs
        self.assertIn('name="roky" value="8"', html)
        self.assertIn('name="od" value="1.6"', html)

    def test_pins_unlimited_and_marker_payload_has_range(self):
        for i in range(35):
            HistoriePin.objects.create(
                author=self.user, body=f"pin {i}", sel="cz",
                od="1.6", do="21.6", roky=5, metric="t",
            )
        resp = self.client.get("/historie/?rozsah=vlastni&od=1.6&do=21.6&roky=5&bod=cz&m=t")
        self.assertEqual(len(resp.context["pins"]), 35)
        marker = resp.context["pins_marker"][0]
        self.assertIn("x0", marker)
        self.assertIn("x1", marker)

    def test_feed_card_shows_stats_table(self):
        self.client.post(reverse("pin_create"), {
            "sel": "cz", "od": "1.6", "do": "21.6", "roky": 5, "metric": "t",
            "body": "pin s tabulkou", "show_in_feed": "on",
        })
        html = self.client.get(reverse("notes:aktuality")).content.decode()
        self.assertIn("pin s tabulkou", html)
        self.assertIn("Průměr", html)
        self.assertIn("Odchylka", html)

    def test_subhistorie_lists_all_pins_with_weekly_progression(self):
        HistoriePin.objects.create(
            author=self.user, body="cz pin", sel="cz",
            od="1.6", do="21.6", roky=5, metric="t",
        )
        HistoriePin.objects.create(
            author=self.user, body="sk pin", sel="sk",
            od="1.6", do="21.6", roky=5, metric="t",
        )
        resp = self.client.get(reverse("subhistorie"))
        html = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        # No bod/metric context filter — both pins visible on one page
        self.assertIn("cz pin", html)
        self.assertIn("sk pin", html)
        self.assertIn("Změna proti předchozímu týdnu", html)
        # Rising temps → at least one red ▲ delta rendered
        self.assertIn("delta-pos", html)


class HistoriePinsRoundThreeTest(TestCase):
    """
    Round three of live feedback: comparison works with missing od/do
    (defaults to the whole year, so "just set počet let" works), reversed
    bounds swap including the displayed labels, Subhistorie entries are
    collapsible with a daily progression and show the author's role.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="round3", password="x")
        self.client.force_login(self.user)
        self.point = WeatherPoint.objects.create(
            name="Testov", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )
        today = date.today()
        for year in (today.year - 1, today.year):
            for day in range(1, 22):
                HistoricalActual.objects.create(
                    point=self.point, date=date(year, 6, day),
                    temp_min=10.0, temp_max=20.0, precip_mm=1.0,
                )

    def test_vlastni_without_bounds_defaults_to_full_year(self):
        resp = self.client.get("/historie/?rozsah=vlastni&roky=3&bod=cz&m=t")
        self.assertEqual(resp.context["rozsah"], "vlastni")
        self.assertEqual(resp.context["od"], "1.1")
        self.assertEqual(resp.context["do"], "31.12")
        self.assertEqual(resp.context["chart_json"]["xrange"], [1, 365])

    def test_vlastni_with_only_od_defaults_do(self):
        resp = self.client.get("/historie/?rozsah=vlastni&od=15.6&roky=3&bod=cz&m=t")
        self.assertEqual(resp.context["od"], "15.6")
        self.assertEqual(resp.context["do"], "31.12")

    def test_reversed_bounds_swap_displayed_labels_too(self):
        resp = self.client.get("/historie/?rozsah=vlastni&od=21.6&do=1.6&roky=3&bod=cz&m=t")
        self.assertEqual(resp.context["od"], "1.6")
        self.assertEqual(resp.context["do"], "21.6")

    def test_compare_fields_clean_outside_vlastni(self):
        html = self.client.get("/historie/?rozsah=plna&bod=cz&m=t&od=1.6&do=21.6&roky=5").content.decode()
        # visible compare-box fields render empty (no stale values, no placeholder)
        self.assertIn('od <input type="text" name="od" value="">', html)
        self.assertNotIn("placeholder=", html.split("compare-box")[1].split("</form>")[0])

    def test_reset_button_present(self):
        html = self.client.get("/historie/").content.decode()
        self.assertIn("↺ Reset", html)

    def test_subhistorie_collapsible_daily_and_role(self):
        leader = User.objects.create_user(username="lead", password="x", role="leader")
        HistoriePin.objects.create(
            author=leader, body="denní pin", sel="cz",
            od="1.6", do="21.6", roky=5, metric="t",
        )
        html = self.client.get(reverse("subhistorie")).content.decode()
        self.assertIn("<details", html)
        self.assertIn("Změna proti předchozímu dni", html)
        self.assertIn("is-leader", html)
        self.assertIn("lead · leader", html)
