"""Kafel „Formularz przyjazdu” na pulpicie uczestnika i jego zapis.

Osobny moduł, a nie kilka metod w ``apps.web.views.participant`` – z tych samych dwóch powodów, co
przy wpisowym (``apps.web.views.participant_fees``): pulpit jest plikiem wspólnym kilku zadań
etapu 2, a cały ten kod istnieje wyłącznie w konkursie z flagą ``onsite_logistics`` i przy
wyłączonej fladze ma kosztować **zero zapytań** (:func:`arrival_card_context` wychodzi na pierwszym
warunku).

**Decyzja D21 w jednym zdaniu.** Formularz pyta o dietę, dostępność i uwagę tekstową **wyłącznie**
wtedy, gdy organizator świadomie włączył zbieranie potrzeb szczególnych – a lista pytań przychodzi
z ``available_needs``, nie z tego modułu. Gdy zbieranie jest wyłączone, pól nie ma na ekranie
w ogóle; nie są „ignorowane przy zapisie”, tylko nie istnieją. Drugą, niezależną bramkę stawia
serwis (``save_arrival_form`` odfiltrowuje potrzeby szczególne i czyści uwagę), więc żądanie
złożone ręcznie niczego nie przemyci.

**Etykieta pola uwag mówi wprost, czego nie wpisywać** (``SPECIAL_NEEDS_WARNING``) – to jest warunek
wdrożenia z decyzji D21, a nie ozdoba. Napis należy do obszaru
(``apps.competitions.logistics``), bo ma się zmieniać razem z regułą, którą opisuje.

**Kafel pojawia się dopiero wtedy, gdy jest dokąd przyjechać**: konkurs ma choć jedno miejsce
zawodów, uczestnik ma wpis do bieżącego etapu, a flaga jest włączona. Formularz przyjazdu do
etapu, którego miejsca organizator jeszcze nie wpisał, byłby pytaniem bez odpowiedzi.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import View

from apps.competitions.logistics import (
    NOTE_MAX_LENGTH,
    SPECIAL_NEEDS_WARNING,
    arrival_form_for,
    available_needs,
    collects_special_needs,
    onsite_logistics_enabled,
    save_arrival_form,
    venues_for,
)
from apps.competitions.models import StageEntry
from apps.core.api import DomainError
from apps.web.mixins import ParticipantRequiredMixin


class ArrivalDeclarationForm(forms.Form):
    """Deklaracja przyjazdu: miejsce, daty i potrzeby. Lista pytań jest **konfiguracją konkursu**.

    Pola ``needs`` i ``note`` powstają w ``__init__``, a nie jako atrybuty klasy, bo ich kształt
    zależy od decyzji organizatora (D21) i od listy miejsc tego konkursu. Klasa formularza żyje
    tyle, co proces; konkursy w jednej instalacji mają różne odpowiedzi – więc wybór musi powstać
    przy żądaniu, a nie przy imporcie modułu.
    """

    #: ``type="date"`` daje przeglądarce natywny wybór daty – bez skryptu, więc bez kolizji z CSP.
    arrives_on = forms.DateField(
        label="Przyjazd", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    departs_on = forms.DateField(
        label="Wyjazd", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, competition=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Miejsce **z tego konkursu**: ``ModelChoiceField`` jest tu realną bramką zapisu, a nie
        # ozdobą – bez zawężenia dałoby się jednym POST-em zadeklarować przyjazd pod cudzy adres.
        self.fields["venue"] = forms.ModelChoiceField(
            queryset=venues_for(competition).order_by("name", "id"),
            label="Miejsce",
            required=False,
            empty_label="— wybierz miejsce —",
        )
        self.fields["needs"] = forms.MultipleChoiceField(
            label="Czego potrzebujesz",
            choices=available_needs(competition),
            required=False,
            widget=forms.CheckboxSelectMultiple,
        )
        self.order_fields(["venue", "arrives_on", "departs_on", "needs"])
        if collects_special_needs(competition):
            self.fields["note"] = forms.CharField(
                label="Uwagi",
                required=False,
                max_length=NOTE_MAX_LENGTH,
                widget=forms.Textarea(attrs={"rows": 3}),
                help_text=SPECIAL_NEEDS_WARNING,
            )

    def clean(self):
        """Wyjazd nie może być przed przyjazdem – ten sam komunikat, co więz w bazie."""
        data = super().clean()
        arrives_on = data.get("arrives_on")
        departs_on = data.get("departs_on")
        if arrives_on and departs_on and departs_on < arrives_on:
            self.add_error("departs_on", "Wyjazd nie może być przed przyjazdem.")
        return data


def arrival_card_context(competition, stage, entry) -> dict:
    """Kontekst kafla „Formularz przyjazdu” albo **pusty słownik**.

    Pusto (bez ani jednego zapytania) w trzech wypadkach: konkurs nie prowadzi logistyki, nie ma
    bieżącego etapu, uczestnik nie jest do niego zapisany. Czwarty warunek – brak miejsc zawodów –
    kosztuje jedno zapytanie i jest sprawdzany na końcu.

    Profilu uczestnika **nie ma w argumentach**, choć kafel jest jego: wpis do etapu (``entry``)
    jest już w kontekście pulpitu i niesie wszystko, czego deklaracja potrzebuje. Sięgnięcie po
    ``self.participant`` kosztuje zapytanie, a konkurs przychodzi argumentem z tego samego powodu
    – ``participant.competition`` byłoby kluczem obcym, czyli drugim zapytaniem na wejście
    do panelu, także tam, gdzie logistyki nie ma.
    """
    if stage is None or entry is None or not onsite_logistics_enabled(competition):
        return {}
    if not venues_for(competition).exists():
        return {}
    declaration = arrival_form_for(entry)
    initial = {}
    if declaration is not None:
        initial = {
            "venue": declaration.venue_id,
            "arrives_on": declaration.arrives_on,
            "departs_on": declaration.departs_on,
            "needs": list(declaration.needs or []),
            "note": declaration.note,
        }
    return {
        "arrival": declaration,
        "arrival_form": ArrivalDeclarationForm(competition=competition, initial=initial),
        "arrival_url": reverse("web:participant-arrival", args=[stage.pk]),
    }


class ArrivalFormView(ParticipantRequiredMixin, View):
    """``POST /me/stages/<id>/arrival/`` – złożenie albo poprawienie deklaracji przyjazdu.

    Wpis do etapu bierzemy z **zawężonego** querysetu i po swoim profilu: cudzej deklaracji nie da
    się tędy złożyć nawet z podmienionym identyfikatorem w adresie (404 z querysetu, § 3.6).
    Powrót jest na pulpit – deklaracja jest kafelkiem pulpitu, a nie własnym ekranem.
    """

    def post(self, request, stage_id: int):
        entry = get_object_or_404(
            StageEntry.objects.for_competition(self.competition).select_related("stage"),
            stage_id=stage_id,
            participant=self.participant,
        )
        form = ArrivalDeclarationForm(request.POST, competition=self.competition)
        if not form.is_valid():
            messages.error(
                request,
                "Deklaracji nie zapisano: "
                + "; ".join(text for errors in form.errors.values() for text in errors),
            )
            return redirect(reverse("web:me"))
        data = form.cleaned_data
        try:
            save_arrival_form(
                entry,
                venue=data.get("venue"),
                arrives_on=data.get("arrives_on"),
                departs_on=data.get("departs_on"),
                needs=data.get("needs") or (),
                note=data.get("note") or "",
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:me"))
        messages.success(request, "Deklaracja przyjazdu zapisana.")
        return redirect(reverse("web:me"))


#: Pulpit uczestnika potrzebuje z tego modułu wyłącznie funkcji kontekstu – reszta jest jego
#: wnętrzem i tak ma zostać.
__all__ = ["ArrivalDeclarationForm", "ArrivalFormView", "arrival_card_context"]
