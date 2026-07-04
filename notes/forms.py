from django import forms
from .models import Note


class NoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={
                "rows": 4,
                "placeholder": "Napište poznámku…",
            }),
        }
        labels = {
            "body": "Text poznámky",
        }
