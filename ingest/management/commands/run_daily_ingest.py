"""
Runs the daily-cadence ingestion steps in sequence: fetch_seasonal (SEAS5),
fetch_ec46, detect_mlr_changes (EC46/SEAS5 revision notes — right after the
fetches so it compares the snapshots this run just ingested), fetch_pollen,
fetch_era5_backfill (rolling 14-day window), and prune_notes (14/30-day
note + Historie-pin expiry). Meant to run once/day via a dedicated Railway
Cron service ("cron-daily") — NOT part of the web service's Procfile, see
CLAUDE.md for the dashboard setup steps.

Each step is isolated in its own try/except so one failure doesn't block the
rest. Always exits 0 so Railway doesn't treat a partial run as a hard
failure — check the per-step summary line and stderr for what actually
failed.
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand

STEPS = [
    "fetch_seasonal", "fetch_ec46", "detect_mlr_changes",
    "fetch_pollen", "fetch_era5_backfill", "prune_notes",
]


class Command(BaseCommand):
    help = "Run daily-cadence ingestion: fetch_seasonal, fetch_ec46, detect_mlr_changes, fetch_pollen, fetch_era5_backfill, prune_notes."

    def handle(self, *args, **options):
        succeeded = 0
        # Rolling 14-day window catches ERA5 revisions to recent days; --end
        # is left unset so fetch_era5_backfill applies its own today-5 lag default.
        rolling_start = (date.today() - timedelta(days=14)).isoformat()

        for step in STEPS:
            try:
                if step == "fetch_era5_backfill":
                    call_command(step, start=rolling_start)
                else:
                    call_command(step)
                succeeded += 1
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"  {step}: FAILED — {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"run_daily_ingest: {succeeded}/{len(STEPS)} steps succeeded."
        ))
