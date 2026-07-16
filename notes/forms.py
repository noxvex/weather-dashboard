from django import forms

from ingest.models import WeatherPoint
from .models import HistoriePin, Note
from .utils import parse_dm


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


class HistoriePinForm(forms.ModelForm):
    """
    Pin-creation form on Historie. The comparison params (sel/od/do/roky/
    metric) come as hidden inputs prefilled from the currently displayed
    comparison — validation re-checks them because hidden inputs are still
    user-editable.
    """
    class Meta:
        model = HistoriePin
        fields = ["sel", "od", "do", "roky", "metric", "body", "show_in_feed"]
        widgets = {
            "sel": forms.HiddenInput(),
            "od": forms.HiddenInput(),
            "do": forms.HiddenInput(),
            "roky": forms.HiddenInput(),
            "metric": forms.HiddenInput(),
            "body": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_od(self):
        val = self.cleaned_data["od"].strip()
        if parse_dm(val) is None:
            raise forms.ValidationError("Neplatné datum, očekává se D.M (např. 12.7).")
        return val

    def clean_do(self):
        val = self.cleaned_data["do"].strip()
        if parse_dm(val) is None:
            raise forms.ValidationError("Neplatné datum, očekává se D.M (např. 15.11).")
        return val

    def clean_roky(self):
        roky = self.cleaned_data["roky"]
        if not 2 <= roky <= 12:
            raise forms.ValidationError("Počet let musí být 2–12.")
        return roky

    def clean_sel(self):
        sel = self.cleaned_data["sel"].strip()
        if sel in ("cz", "sk") or sel in WeatherPoint.MACRO_REGION_COUNTRY:
            return sel
        if sel.isdigit() and WeatherPoint.objects.filter(pk=sel).exists():
            return sel
        raise forms.ValidationError("Neplatný bod.")


class PinEditForm(forms.ModelForm):
    """Editing an existing pin only touches the comment — the comparison
    params are the pin's identity."""
    class Meta:
        model = HistoriePin
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "body": "Text pinu",
        }
