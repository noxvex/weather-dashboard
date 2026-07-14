"""
Minimal regression suite for the cron wrapper commands — confirms that one
sub-step raising doesn't stop the rest from running, since that's the whole
point of wrapping each call_command() in its own try/except.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase


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
            ["fetch_seasonal", "fetch_ec46", "fetch_pollen", "fetch_era5_backfill", "prune_notes"],
        )
        self.assertIn("4/5 steps succeeded", out.getvalue())

    @patch("ingest.management.commands.run_daily_ingest.call_command")
    def test_era5_backfill_called_with_rolling_start(self, mock_call_command):
        call_command("run_daily_ingest", stdout=StringIO())
        era5_call = next(c for c in mock_call_command.call_args_list if c.args[0] == "fetch_era5_backfill")
        self.assertIn("start", era5_call.kwargs)
        self.assertNotIn("end", era5_call.kwargs)
