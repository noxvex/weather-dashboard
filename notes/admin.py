from django.contrib import admin
from .models import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("author", "note_type", "is_pinned", "created_at")
    list_filter = ("note_type", "is_pinned")
    list_editable = ("is_pinned",)
    date_hierarchy = "created_at"
