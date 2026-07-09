"""
Daily lifecycle management for notes:
  - Soft-delete: unpinned notes ≥14 days old are marked is_hidden=True and
    disappear from the feed while remaining in the DB (audit trail).
  - Hard-delete: unpinned notes ≥30 days old are permanently removed from the DB.
Pinned notes are exempt from both steps — they survive until manually unpinned.
Safe to run multiple times (idempotent).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from notes.models import Note

SOFT_DELETE_DAYS = 14
HARD_DELETE_DAYS = 30


class Command(BaseCommand):
    help = "Soft-delete notes older than 14 days; hard-delete notes older than 30 days (unpinned only)."

    def handle(self, *args, **options):
        now = timezone.now()
        soft_cutoff = now - timedelta(days=SOFT_DELETE_DAYS)
        hard_cutoff = now - timedelta(days=HARD_DELETE_DAYS)

        # Hard-delete first so we don't soft-delete something we're about to remove.
        hard_qs = Note.objects.filter(is_pinned=False, created_at__lt=hard_cutoff)
        hard_count, _ = hard_qs.delete()

        # Soft-delete: flag as hidden so they vanish from feed but stay in DB.
        soft_count = Note.objects.filter(
            is_pinned=False, is_hidden=False, created_at__lt=soft_cutoff,
        ).update(is_hidden=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"prune_notes: {soft_count} soft-deleted (≥{SOFT_DELETE_DAYS}d), "
                f"{hard_count} hard-deleted (≥{HARD_DELETE_DAYS}d)."
            )
        )
