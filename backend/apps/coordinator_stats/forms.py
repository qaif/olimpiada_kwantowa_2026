"""Filtry lejka (STATS-02 § 1.4). Każda lista wyboru jest zamknięta i policzona w obrębie konkursu.

Pola „sposób liczenia” już nie ma (kroki zawsze niezależne, zmiana z 21.10.2026). Zapisany w zakładce
adres ze starym ``?mode=strict`` dalej działa: formularz Django pomija parametry spoza swoich pól.
"""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.accounts.messaging import school_choices
from apps.accounts.models import Region, Voivodeship
from apps.accounts.services import CUSTOM_REGIONS_FLAG

from . import domains
from .funnel import Filters

ALL = ("", _("wszystkie"))


def region_choices(competition) -> list[tuple[str, str]]:
    """Regiony konkursu (``r<id>``) przy fladze ``custom_regions``, inaczej województwa."""
    if competition is not None and competition.has_feature(CUSTOM_REGIONS_FLAG):
        return [
            (f"r{region.pk}", region.name)
            for region in Region.objects.for_competition(competition).order_by("position", "name", "id")
        ]
    return list(Voivodeship.choices)


class FunnelFilterForm(forms.Form):
    region = forms.ChoiceField(label=_("Województwo / region"), required=False)
    school = forms.ChoiceField(label=_("Szkoła"), required=False)
    domain = forms.ChoiceField(label=_("Domena adresu e-mail"), required=False)
    registered_from = forms.DateField(
        label=_("Rejestracja od"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    registered_to = forms.DateField(
        label=_("Rejestracja do"), required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, data=None, *, competition):
        super().__init__(data)
        self.fields["region"].choices = [ALL, *region_choices(competition)]
        # Szkoły z uczestnikami tego konkursu – ta sama lista, co przy komunikacie „wybrana szkoła”.
        self.fields["school"].choices = [
            ALL,
            *((key, f"{label} ({count})") for key, label, count in school_choices(competition)),
        ]
        self.fields["domain"].choices = [ALL, *domains.CHOICES]

    def clean(self):
        data = super().clean()
        start, end = data.get("registered_from"), data.get("registered_to")
        if start and end and start > end:
            raise forms.ValidationError(_("Początek okresu rejestracji jest późniejszy niż jego koniec."))
        return data

    def filters(self) -> Filters:
        data = self.cleaned_data
        return Filters(
            region=data.get("region") or "",
            school=data.get("school") or "",
            domain=data.get("domain") or "",
            registered_from=data.get("registered_from"),
            registered_to=data.get("registered_to"),
        )

    def description(self) -> list[tuple[str, str]]:
        """Filtry w postaci „pole: wartość” – nagłówek eksportu CSV (tylko ustawione)."""
        data = self.cleaned_data
        rows = []
        for name in ("region", "school", "domain"):
            value = data.get(name)
            if value:
                rows.append(
                    (str(self.fields[name].label), str(dict(self.fields[name].choices).get(value, value)))
                )
        for name in ("registered_from", "registered_to"):
            if data.get(name):
                rows.append((str(self.fields[name].label), data[name].isoformat()))
        return rows
