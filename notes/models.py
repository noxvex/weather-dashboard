import urllib.parse

from django.conf import settings
from django.db import models
from django.urls import reverse


class Note(models.Model):
    TYPE_HUMAN = "human"
    TYPE_SYSTEM_CHANGE = "system_change"
    TYPE_SYSTEM_OUTLOOK = "system_outlook"
    NOTE_TYPE_CHOICES = [
        (TYPE_HUMAN, "Člověk"),
        (TYPE_SYSTEM_CHANGE, "Systém – změna"),
        (TYPE_SYSTEM_OUTLOOK, "Systém – výhled"),
    ]

    COUNTRY_CZ = "cz"
    COUNTRY_SK = "sk"
    COUNTRY_BOTH = "both"
    COUNTRY_CHOICES = [
        (COUNTRY_CZ, "ČR"),
        (COUNTRY_SK, "SR"),
        (COUNTRY_BOTH, "ČR + SR"),
    ]

    HORIZON_SHORT = "short"
    HORIZON_MID = "mid"
    HORIZON_LONG = "long"
    HORIZON_CHOICES = [
        (HORIZON_SHORT, "Krátkodobá"),
        (HORIZON_MID, "Střednědobá"),
        (HORIZON_LONG, "Dlouhodobá"),
    ]

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_pinned = models.BooleanField(default=False)
    note_type = models.CharField(max_length=20, choices=NOTE_TYPE_CHOICES, default=TYPE_HUMAN)
    # Which country the note concerns (chip in the feed); human notes default to both
    country = models.CharField(max_length=5, choices=COUNTRY_CHOICES, default=COUNTRY_BOTH)
    # Which forecast horizon the note is based on (drives left-border color)
    horizon = models.CharField(max_length=6, choices=HORIZON_CHOICES, default=HORIZON_SHORT)
    # Soft-delete: set True by prune_notes at 14 days old (unpinned only)
    # Pinned notes are exempt — is_hidden is never set while is_pinned=True
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def save(self, *args, **kwargs):
        # System notes start unpinned and auto-expire after 14 days
        # (purged by detect_changes) unless a leader/admin pins them.
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.author.username} [{self.note_type}] — {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_system(self):
        return self.note_type != self.TYPE_HUMAN


class HistoriePin(models.Model):
    """
    A saved comparison on the Historie page: the manual-comparison form's
    parameters (bod/od/do/roky/metrika) plus a user comment, shown as a
    marker on the Historie chart. Lifecycle mirrors Note: unpinned pins are
    soft-deleted at 14 days and hard-deleted at 30 by prune_notes; pins with
    is_pinned=True (leader/admin) are exempt and live until unpinned.
    """
    METRIC_TEMP = "t"
    METRIC_PRECIP = "p"
    METRIC_CHOICES = [
        (METRIC_TEMP, "Teplota (°C)"),
        (METRIC_PRECIP, "Srážky (mm)"),
    ]

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="historie_pins",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_pinned = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)

    # Comparison params stored in the exact format of the Historie GET params
    # (bod/od/do/roky/m) — a deep link back is a plain urlencode of these.
    sel = models.CharField(max_length=10)                # "cz" / "sk" / WeatherPoint pk
    od = models.CharField(max_length=10)                 # day.month, e.g. "12.7"
    do = models.CharField(max_length=10)                 # day.month, e.g. "15.11"
    roky = models.PositiveSmallIntegerField(default=5)   # 2–12, validated in the form
    metric = models.CharField(max_length=1, choices=METRIC_CHOICES, default=METRIC_TEMP)

    # Scope: True = also cross-post a summary card into the Aktuality feed
    show_in_feed = models.BooleanField(default=True)
    # The cross-posted feed card. SET_NULL so deleting the card in the feed
    # doesn't take the pin with it; pin deletion removes the card via delete().
    feed_note = models.OneToOneField(
        Note,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="historie_pin",
    )

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["sel", "metric"]),
        ]

    def delete(self, *args, **kwargs):
        # The feed card has no reason to outlive its pin.
        if self.feed_note_id:
            self.feed_note.delete()
        return super().delete(*args, **kwargs)

    def historie_url(self):
        """Deep link opening Historie exactly on this pin's comparison."""
        qs = urllib.parse.urlencode({
            "bod": self.sel, "m": self.metric, "rozsah": "vlastni",
            "od": self.od, "do": self.do, "roky": self.roky,
        })
        return f"{reverse('historie')}?{qs}"

    def selection_label(self):
        """Human label of the pinned bod, matching what Historie shows."""
        if self.sel == "cz":
            return "ČR (národní průměr)"
        if self.sel == "sk":
            return "SR (národní průměr)"
        from ingest.models import WeatherPoint
        macro_labels = dict(WeatherPoint.MACRO_REGION_CHOICES)
        if self.sel in macro_labels:
            return f"{macro_labels[self.sel]} (regionální průměr)"
        point = WeatherPoint.objects.filter(pk=self.sel).first() if self.sel.isdigit() else None
        return f"{point.name} ({point.country})" if point else "neznámý bod"

    @property
    def summary_label(self):
        """Text summary of the comparison params for the Aktuality card."""
        metric_label = "srážky" if self.metric == self.METRIC_PRECIP else "teplota"
        return f"{self.od} – {self.do} · posledních {self.roky} let · {self.selection_label()} · {metric_label}"

    def __str__(self):
        return f"{self.author.username} pin [{self.sel} {self.od}–{self.do}] — {self.created_at:%Y-%m-%d %H:%M}"
