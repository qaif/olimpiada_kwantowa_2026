"""Formularz reguły zaświadczeń z warsztatów na ekranie koordynatora."""

from __future__ import annotations

from django import forms
from django.db.models import Q

from apps.results.models import CertificateKind, CertificateTemplate

from .models import AutoIssuePolicy

#: Pola, których zmiana trafia do audytu – wartości, nie dane osobowe.
AUDITED_FIELDS = ("enabled", "min_workshops", "template_id", "notify_participants")


def template_choices(competition, edition=None):
    """Szablony, które wolno wskazać: aktywne, **tego konkursu**, rodzaju „warsztaty” albo „wszystkie”,
    dla tej edycji albo dla wszystkich edycji.

    Szablon innego rodzaju (np. dyplom laureata) nie ma bloku listy warsztatów w swoim zamyśle,
    szablon innej edycji to winieta innego rocznika, a szablon cudzego konkursu to cudza winieta –
    wszystkie odpadają już na liście, a nie dopiero w składzie.
    """
    editions = (
        Q(edition__isnull=True) | Q(edition=edition) if edition is not None else Q(edition__isnull=True)
    )
    return (
        CertificateTemplate.objects.for_competition(competition)
        .filter(is_active=True)
        .filter(Q(kind=CertificateKind.WARSZTATY) | Q(kind=""))
        .filter(editions)
        .select_related("edition")
    )


class AutoIssuePolicyForm(forms.ModelForm):
    class Meta:
        model = AutoIssuePolicy
        fields = ("enabled", "min_workshops", "template", "notify_participants")
        widgets = {"min_workshops": forms.NumberInput(attrs={"min": 1, "max": 100, "inputmode": "numeric"})}

    def __init__(self, *args, competition=None, edition=None, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["template"]
        # Queryset zawężony do konkursu jest zarazem walidacją: identyfikator szablonu cudzego
        # konkursu podany ręcznie w POST-cie kończy się błędem pola, a nie zapisem.
        field.queryset = template_choices(competition, edition)
        field.required = False
        field.empty_label = "— jak dla każdego dokumentu (dopasowanie rodzaj + edycja) —"
        field.label_from_instance = lambda template: (
            f"{template.name} ({template.kind_label}, {template.edition_label})"
        )
