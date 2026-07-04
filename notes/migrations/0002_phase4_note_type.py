from django.db import migrations, models


def migrate_source_to_note_type(apps, schema_editor):
    """Convert old source='system' rows to note_type='system_change' before source is removed."""
    Note = apps.get_model("notes", "Note")
    Note.objects.filter(source="system").update(note_type="system_change")


class Migration(migrations.Migration):

    dependencies = [
        ("notes", "0001_initial"),
    ]

    operations = [
        # 1. Add note_type with default='human' so existing rows get a safe value
        migrations.AddField(
            model_name="note",
            name="note_type",
            field=models.CharField(
                choices=[
                    ("human", "Člověk"),
                    ("system_change", "Systém – změna"),
                    ("system_outlook", "Systém – výhled"),
                ],
                default="human",
                max_length=20,
            ),
        ),
        # 2. Data migration: map source='system' → note_type='system_change'
        migrations.RunPython(migrate_source_to_note_type, migrations.RunPython.noop),
        # 3. Drop the old source column
        migrations.RemoveField(
            model_name="note",
            name="source",
        ),
    ]
