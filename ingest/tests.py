"""
Minimal regression suite for the cron wrapper commands — confirms that one
sub-step raising doesn't stop the rest from running, since that's the whole
point of wrapping each call_command() in its own try/except.
"""
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ingest.models import MediumLongRangeForecast, WeatherPoint
from notes.models import Note

User = get_user_model()


class RunFrequentIngestTest(TestCase):

    @patch("ingest.management.commands.run_frequent_ingest.call_command")
    def test_continues_past_a_failing_step(self, mock_call_command):
        def side_effect(step, **kwargs):
            if step == "detect_changes":
                raise RuntimeError("boom")
        mock_call_command.side_effect = side_effect

        out = StringIO()
        call_command("run_frequent_ingest", stdout=out)

        called_steps = [c.args[0] for c in mock_call_command.call_args_list]
        self.assertEqual(called_steps, ["ingest_weather", "detect_changes", "generate_outlook_notes"])
        self.assertIn("2/3 steps succeeded", out.getvalue())

    @patch("ingest.management.commands.run_frequent_ingest.call_command")
    def test_all_steps_succeed(self, mock_call_command):
        out = StringIO()
        call_command("run_frequent_ingest", stdout=out)
        self.assertEqual(mock_call_command.call_count, 3)
        self.assertIn("3/3 steps succeeded", out.getvalue())


class RunDailyIngestTest(TestCase):

    @patch("ingest.management.commands.run_daily_ingest.call_command")
    def test_continues_past_a_failing_step(self, mock_call_command):
        def side_effect(step, **kwargs):
            if step == "fetch_ec46":
                raise RuntimeError("boom")
        mock_call_command.side_effect = side_effect

        out = StringIO()
        call_command("run_daily_ingest", stdout=out)

        called_steps = [c.args[0] for c in mock_call_command.call_args_list]
        self.assertEqual(
            called_steps,
            ["fetch_seasonal", "fetch_ec46", "detect_mlr_changes",
             "fetch_pollen", "fetch_era5_backfill", "prune_notes"],
        )
        self.assertIn("5/6 steps succeeded", out.getvalue())

    @patch("ingest.management.commands.run_daily_ingest.call_command")
    def test_era5_backfill_called_with_rolling_start(self, mock_call_command):
        call_command("run_daily_ingest", stdout=StringIO())
        era5_call = next(c for c in mock_call_command.call_args_list if c.args[0] == "fetch_era5_backfill")
        self.assertIn("start", era5_call.kwargs)
        self.assertNotIn("end", era5_call.kwargs)


class DetectMlrChangesTest(TestCase):
    """
    EC46/SEAS5 revisions ≥1.0 °C must land in the Aktuality feed as
    system_change notes with the right horizon label, once per day at most
    (idempotent), reusing the same _mlr_revision_context the Revize page uses.
    """

    def setUp(self):
        User.objects.create_superuser(username="root", password="x", email="root@example.com")
        self.point = WeatherPoint.objects.create(
            name="Testov", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )
        self.today = date.today()

    def _snapshot(self, horizon, days_ago, temp):
        """One MLR snapshot: two future target dates at the given temp_mean."""
        issued = timezone.now() - timedelta(days=days_ago)
        for offset in (1, 2):
            MediumLongRangeForecast.objects.create(
                point=self.point, target_date=self.today + timedelta(days=offset),
                horizon=horizon, issued_at=issued,
                temp_mean=temp, precip_probability=10.0,
            )

    def _run(self):
        out = StringIO()
        call_command("detect_mlr_changes", stdout=out)
        return out.getvalue()

    def test_ec46_revision_creates_mid_note_with_facts(self):
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 1, 15.0)
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 0, 16.5)  # +1.5 °C
        self._run()
        note = Note.objects.get()
        self.assertEqual(note.note_type, Note.TYPE_SYSTEM_CHANGE)
        self.assertEqual(note.horizon, Note.HORIZON_MID)
        self.assertEqual(note.country, Note.COUNTRY_CZ)
        self.assertIn("(EC46)", note.body)
        self.assertIn("střednědobé", note.body)
        self.assertIn("1.5 °C", note.body)
        self.assertIn("vyšší", note.body)
        self.assertIn("15.0 → 16.5 °C", note.body)

    def test_seas5_revision_creates_long_note(self):
        self._snapshot(MediumLongRangeForecast.HORIZON_SEAS5, 1, 15.0)
        self._snapshot(MediumLongRangeForecast.HORIZON_SEAS5, 0, 13.5)  # −1.5 °C
        self._run()
        note = Note.objects.get()
        self.assertEqual(note.horizon, Note.HORIZON_LONG)
        self.assertIn("(SEAS5)", note.body)
        self.assertIn("dlouhodobé", note.body)
        self.assertIn("nižší", note.body)

    def test_below_threshold_creates_nothing(self):
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 1, 15.0)
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 0, 15.9)  # 0.9 < 1.0
        self._run()
        self.assertEqual(Note.objects.count(), 0)

    def test_single_snapshot_skips_without_note(self):
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 0, 15.0)
        out = self._run()
        self.assertEqual(Note.objects.count(), 0)
        self.assertIn("fewer than 2 snapshots", out)

    def test_repeated_run_is_idempotent(self):
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 1, 15.0)
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 0, 16.5)
        self._run()
        self._run()
        self.assertEqual(Note.objects.count(), 1)

    def test_not_blocked_by_short_range_mid_note_same_day(self):
        # detect_changes can also write a horizon=mid note for the same
        # country+day — the (EC46) body marker must keep dedup separate
        root = User.objects.get(username="root")
        Note.objects.create(
            author=root, note_type=Note.TYPE_SYSTEM_CHANGE,
            country=Note.COUNTRY_CZ, horizon=Note.HORIZON_MID,
            body="⚠️ Česká republika: Předpověď ukazuje teplotní výkyv 6 °C…",
        )
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 1, 15.0)
        self._snapshot(MediumLongRangeForecast.HORIZON_EC46, 0, 16.5)
        self._run()
        self.assertEqual(Note.objects.filter(body__contains="(EC46)").count(), 1)


class ForecastArchiveTest(TestCase):
    """
    Fetchers must aggregate hourly previous-runs data to daily rows, store
    single-run horizons only for leads 8–15, and never re-call the API for a
    window that's already covered (rows — even null ones — are the cache).
    """

    def setUp(self):
        self.point = WeatherPoint.objects.create(
            name="Testov", region="Test", country="CZ", latitude=50.0, longitude=15.0,
        )
        self.day = date(2025, 6, 10)

    @patch("ingest.forecast_archive._get_json")
    def test_previous_runs_aggregates_hourly_to_daily(self, mock_get):
        from ingest.forecast_archive import fetch_previous_runs
        from ingest.models import ArchivedForecast
        hours = [f"2025-06-10T{h:02d}:00" for h in range(24)]
        payload = {"hourly": {"time": hours}}
        # lead 1: temps 10..33 (max 33, min 10), precip 0.5/h → 12.0 total
        payload["hourly"]["temperature_2m_previous_day1"] = [10.0 + h for h in range(24)]
        payload["hourly"]["precipitation_previous_day1"] = [0.5] * 24
        # leads 2–7: nulls (archive empty) — rows must still be stored
        for n in range(2, 8):
            payload["hourly"][f"temperature_2m_previous_day{n}"] = [None] * 24
            payload["hourly"][f"precipitation_previous_day{n}"] = [None] * 24
        mock_get.return_value = payload

        fetch_previous_runs(self.point, self.day, self.day)
        row = ArchivedForecast.objects.get(point=self.point, valid_date=self.day, lead_days=1)
        self.assertEqual(row.temp_max, 33.0)
        self.assertEqual(row.temp_min, 10.0)
        self.assertEqual(row.precip_mm, 12.0)
        # null leads stored as coverage markers
        self.assertEqual(ArchivedForecast.objects.filter(point=self.point, valid_date=self.day).count(), 7)
        self.assertIsNone(ArchivedForecast.objects.get(point=self.point, valid_date=self.day, lead_days=5).temp_max)

    @patch("ingest.forecast_archive._get_json")
    def test_single_run_stores_only_leads_8_to_15(self, mock_get):
        from ingest.forecast_archive import fetch_single_run
        from ingest.models import ArchivedForecast
        run = date(2025, 6, 1)
        days = [(run + timedelta(days=i)).isoformat() for i in range(16)]
        mock_get.return_value = {"daily": {
            "time": days,
            "temperature_2m_max": [20.0 + i for i in range(16)],
            "temperature_2m_min": [10.0] * 16,
            "precipitation_sum": [1.0] * 16,
        }}
        fetch_single_run(self.point, run)
        leads = sorted(ArchivedForecast.objects.filter(point=self.point).values_list("lead_days", flat=True))
        self.assertEqual(leads, list(range(8, 16)))
        row = ArchivedForecast.objects.get(point=self.point, lead_days=10)
        self.assertEqual(row.valid_date, run + timedelta(days=10))
        self.assertEqual(row.temp_max, 30.0)

    @patch("ingest.forecast_archive._get_json")
    def test_covered_window_makes_no_api_calls(self, mock_get):
        from ingest.forecast_archive import ensure_archive
        from ingest.models import ArchivedForecast
        # coverage markers: lead-1 row for the day (previous-runs) and lead-8
        # rows for every needed single run (day−15 .. day−8)
        ArchivedForecast.objects.create(
            point=self.point, valid_date=self.day, lead_days=1,
            source=ArchivedForecast.SOURCE_PREVIOUS_RUNS,
        )
        for run_offset in range(8, 16):
            run = self.day - timedelta(days=run_offset)
            ArchivedForecast.objects.create(
                point=self.point, valid_date=run + timedelta(days=8), lead_days=8,
                source=ArchivedForecast.SOURCE_SINGLE_RUNS,
            )
        calls, exhausted = ensure_archive(self.point, self.day, self.day)
        self.assertEqual(calls, 0)
        self.assertFalse(exhausted)
        mock_get.assert_not_called()

    @patch("ingest.forecast_archive._get_json")
    def test_budget_exhaustion_reports_partial(self, mock_get):
        from ingest.forecast_archive import ensure_archive
        mock_get.return_value = {"hourly": {"time": []}, "daily": {"time": []}}
        calls, exhausted = ensure_archive(self.point, self.day, self.day, max_calls=3)
        self.assertEqual(calls, 3)
        self.assertTrue(exhausted)
