from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notes", "0003_note_country_note_horizon"),
    ]

    operations = [
        migrations.AddField(
            model_name="note",
            name="is_hidden",
            field=models.BooleanField(default=False),
        ),
    ]
