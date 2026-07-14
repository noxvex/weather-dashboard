"""
Runs the frequent-cadence ingestion steps in sequence: ingest_weather
(short-range forecast), detect_changes, generate_outlook_notes. Meant to run
every ~12h via a dedicated Railway Cron service ("cron-frequent") — NOT part
of the web service's Procfile, see CLAUDE.md for the dashboard setup steps.

Each step is isolated in its own try/except so one failure (e.g. an
Open-Meteo outage) doesn't block the rest. Always exits 0 so Railway doesn't
treat a partial run as a hard failure — check the per-step summary line and
stderr for what actually failed.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand

STEPS = ["ingest_weather", "detect_changes", "generate_outlook_notes"]


class Command(BaseCommand):
    help = "Run frequent-cadence ingestion: ingest_weather, detect_changes, generate_outlook_notes."

    def handle(self, *args, **options):
        succeeded = 0
        for step in STEPS:
            try:
                call_command(step)
                succeeded += 1
            except Exception as exc:
                self.stderr.write(self.style.WARNING(f"  {step}: FAILED — {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"run_frequent_ingest: {succeeded}/{len(STEPS)} steps succeeded."
        ))
