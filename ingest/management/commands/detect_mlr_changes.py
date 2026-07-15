"""
Analyses medium/long-range forecast snapshots (EC46 / SEAS5) and creates
system_change notes when a revision moves the national mean temperature by
≥1.0 °C — the same threshold and computation the Revize page buckets use
(notes.views._mlr_revision_context is imported, not duplicated).

At most one note per (country, horizon) per day, carrying the largest
delta of the run — mirrors detect_changes, which also caps at one revision
note per country per day instead of flooding the feed with every changed
date. Facts only, no advice.

Runs once a day via run_daily_ingest (the cron-daily service), right after
fetch_seasonal/fetch_ec46 so it compares the snapshots ingested by the same
run. Safe to re-run: dedup is field-based (note_type + horizon + country +
created today + the "(EC46)"/"(SEAS5)" marker in the body), so a repeated
run without a new revision creates nothing.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ingest.models import MediumLongRangeForecast
from notes.models import Note
from notes.views import _mlr_revision_context

REVISION_THRESHOLD = 1.0  # °C — same as the Revize page mid/long buckets

User = get_user_model()

# (MLR horizon, Note horizon, Czech adjective for the body, model marker)
HORIZONS = [
    (MediumLongRangeForecast.HORIZON_EC46, Note.HORIZON_MID, "střednědobé", "EC46"),
    (MediumLongRangeForecast.HORIZON_SEAS5, Note.HORIZON_LONG, "dlouhodobé", "SEAS5"),
]

COUNTRIES = [
    ("ČR", Note.COUNTRY_CZ, "Česká republika"),
    ("SR", Note.COUNTRY_SK, "Slovensko"),
]


def _get_system_user():
    """Use the admin account as author for system notes (same as detect_changes)."""
    return User.objects.filter(is_superuser=True).order_by("pk").first()


def _note_exists_today(note_horizon, country_code, model_label):
    """
    Field-based dedup. The body marker distinguishes this note from a
    detect_changes note that may also land on horizon=mid for the same
    country+day (its _horizon_for pushes far-out short-range events to mid).
    """
    return Note.objects.filter(
        note_type=Note.TYPE_SYSTEM_CHANGE,
        horizon=note_horizon,
        country=country_code,
        created_at__date=date.today(),
        body__contains=f"({model_label})",
    ).exists()


class Command(BaseCommand):
    help = "Create system notes for EC46/SEAS5 forecast revisions ≥1.0 °C (same logic as the Revize page)."

    def handle(self, *args, **options):
        author = _get_system_user()
        if not author:
            self.stderr.write("No superuser found to author system notes.")
            return

        notes_created = 0
        for mlr_horizon, note_horizon, label_adj, model_label in HORIZONS:
            ctx = _mlr_revision_context(mlr_horizon, threshold=REVISION_THRESHOLD)
            if ctx.get("not_enough_data"):
                self.stdout.write(f"  {model_label}: fewer than 2 snapshots, skipping.")
                continue

            for country_short, country_code, country_label in COUNTRIES:
                rows = [r for r in ctx["revisions"] if r["country"] == country_short]
                if not rows:
                    continue
                if _note_exists_today(note_horizon, country_code, model_label):
                    continue
                top = max(rows, key=lambda r: abs(r["temp_delta"]))
                direction = "vyšší" if top["temp_delta"] > 0 else "nižší"
                Note.objects.create(
                    author=author,
                    note_type=Note.TYPE_SYSTEM_CHANGE,
                    country=country_code,
                    horizon=note_horizon,
                    body=(
                        f"🔄 {country_label}: Revize {label_adj} předpovědi ({model_label}) pro "
                        f"{top['date'].strftime('%-d. %-m.')} — teplota o {abs(top['temp_delta']):.1f} °C "
                        f"{direction} než předchozí snímek "
                        f"({top['temp_prev']:.1f} → {top['temp_latest']:.1f} °C)."
                    ),
                )
                notes_created += 1

        self.stdout.write(self.style.SUCCESS(f"detect_mlr_changes: {notes_created} note(s) created."))
