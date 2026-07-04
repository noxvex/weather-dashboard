from django.conf import settings
from django.db import models


class Note(models.Model):
    TYPE_HUMAN = "human"
    TYPE_SYSTEM_CHANGE = "system_change"
    TYPE_SYSTEM_OUTLOOK = "system_outlook"
    NOTE_TYPE_CHOICES = [
        (TYPE_HUMAN, "Člověk"),
        (TYPE_SYSTEM_CHANGE, "Systém – změna"),
        (TYPE_SYSTEM_OUTLOOK, "Systém – výhled"),
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

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def save(self, *args, **kwargs):
        # System notes are always pinned — no manual step needed
        if self.note_type.startswith("system_"):
            self.is_pinned = True
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.author.username} [{self.note_type}] — {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def is_system(self):
        return self.note_type != self.TYPE_HUMAN
