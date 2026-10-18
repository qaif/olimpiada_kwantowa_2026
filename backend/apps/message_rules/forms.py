"""Formularz reguły przypomnienia – ``BroadcastForm`` z polami reguły zamiast „Wyślij później”.

Dziedziczenie, a nie kopia: grupa odbiorców reguły ma być **dokładnie** grupą z ekranu
komunikatów – te same listy wyboru (policzone w obrębie konkursu żądania), ta sama wymagalność
parametru, ten sam opis do historii (``target``) i te same argumenty ``resolve_recipients``
(``recipient_kwargs``). Druga implementacja grup rozjechałaby się z pierwszą przy pierwszej nowej
grupie. Odpadają tylko wklejona lista adresów (jej adresów nie przechowujemy, więc nie da się jej
wysyłać cyklicznie) i pole terminu – termin wynika z reguły.
"""

from __future__ import annotations

import datetime as dt
import re

from django import forms
from django.utils.translation import gettext, gettext_lazy

from apps.accounts.messaging import _stored_parameters
from apps.accounts.models import BroadcastGroup
from apps.web.coordinator_forms import BroadcastForm

from . import planning
from .models import MessageRule, RuleKind, RuleTiming

#: Najwięcej dni przed wydarzeniem. Warsztat – dwa tygodnie (przypomnienie wcześniejsze to już
#: zaproszenie, nie przypomnienie); termin etapu – 90 dni, granica planowania komunikatów.
MAX_DAYS = {RuleKind.WORKSHOP: 14, RuleKind.STAGE_DEADLINE: 90}
MAX_HOURS_BEFORE = 72


def parse_days(text: str) -> list[int] | None:
    """„30, 7, 1” → ``[30, 7, 1]`` (malejąco, bez powtórzeń); ``None`` przy czymkolwiek innym niż liczby."""
    parts = [part for part in re.split(r"[\s,;]+", text or "") if part]
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return sorted({int(part) for part in parts}, reverse=True)


class RuleForm(BroadcastForm):
    """Reguła jednego rodzaju (``kind`` – warsztaty albo termin etapu); rodzaj nie zmienia się po zapisie."""

    name = forms.CharField(label=gettext_lazy("Nazwa reguły"), max_length=120)
    enabled = forms.BooleanField(label=gettext_lazy("Reguła włączona"), required=False, initial=True)
    timing = forms.ChoiceField(
        label=gettext_lazy("Moment wysyłki"), choices=RuleTiming.choices, initial=RuleTiming.DAY_AT
    )
    days_before = forms.CharField(
        label=gettext_lazy("Ile dni przed"),
        required=False,
        initial="0",
        help_text=gettext_lazy("0 = w dniu warsztatu. Kilka liczb rozdziel przecinkami, np. 30, 7, 1."),
    )
    send_time = forms.TimeField(
        label=gettext_lazy("Godzina wysyłki (czas polski)"),
        initial=dt.time(6, 0),
        widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
    )
    hours_before = forms.IntegerField(
        label=gettext_lazy("Ile godzin przed początkiem warsztatu"),
        required=False,
        min_value=1,
        max_value=MAX_HOURS_BEFORE,
        help_text=gettext_lazy(
            "Tylko przy „N godzin przed początkiem”; początek to pierwsza godzina z kolumny „godziny”."
        ),
    )
    link = forms.URLField(
        label=gettext_lazy("Link (pole {link})"),
        required=False,
        max_length=500,
        assume_scheme="https",
        help_text=gettext_lazy("Np. stały link spotkania Google Meet albo adres strony etapu."),
    )

    field_order = (
        "name",
        "enabled",
        "group",
        "stage",
        "district",
        "region",
        "school",
        "grade",
        "workshop",
        "include_past_editions",
        "timing",
        "days_before",
        "send_time",
        "hours_before",
        "link",
        "subject",
        "body",
    )

    def __init__(self, *args, kind: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        # Termin wynika z reguły, a wklejonej listy adresów nie da się wysyłać cyklicznie.
        del self.fields["send_at"]
        del self.fields["addresses"]
        self.fields["group"].choices = [
            (value, label) for value, label in BroadcastGroup.choices if value != BroadcastGroup.CUSTOM
        ]
        names = ", ".join("{" + name + "}" for name in planning.PLACEHOLDERS[kind])
        self.fields["subject"].label = gettext("Temat (szablon)")
        self.fields["body"].label = gettext("Treść (szablon)")
        self.fields["body"].help_text = gettext("Pola do wstawienia: %(names)s.") % {"names": names}
        if kind == RuleKind.STAGE_DEADLINE:
            # Etap jest tu **terminem** reguły, a przy grupach etapowych także parametrem grupy –
            # jedno pole, żeby przypomnienie o Etapie I nie poszło do „bez pracy w Etapie II”.
            self.fields["stage"].required = True
            self.fields["stage"].label = gettext("Etap (termin zgłaszania)")
            self.fields["days_before"].initial = "30, 7, 1"
            self.fields["days_before"].help_text = gettext(
                "Dni przed terminem etapu, rozdzielone przecinkami; 0 = w dniu terminu."
            )
            self.fields["send_time"].initial = dt.time(9, 0)
            del self.fields["timing"]
            del self.fields["hours_before"]

    def visible_parameter_fields(self) -> list[str]:
        """Pola, które skrypt ``broadcast-groups.js`` chowa przy grupach bez nich.

        Przy regule „termin etapu” etap jest polem reguły, a nie tylko parametrem grupy – nie wolno
        go chować przy grupie „wszyscy uczestnicy”.
        """
        fields = sorted({field for fields in self.parameter_map().values() for field in fields})
        if self.kind == RuleKind.STAGE_DEADLINE:
            fields = [field for field in fields if field != "stage"]
        return [field for field in fields if field in self.fields]

    def clean(self) -> dict:
        cleaned = super().clean()
        timing = cleaned.get("timing", RuleTiming.DAY_AT)
        if timing == RuleTiming.HOURS_BEFORE:
            if not cleaned.get("hours_before"):
                self.add_error("hours_before", gettext("Podaj liczbę godzin przed początkiem warsztatu."))
        else:
            days = parse_days(cleaned.get("days_before", ""))
            limit = MAX_DAYS[self.kind]
            if days is None:
                self.add_error(
                    "days_before", gettext("Wpisz liczby dni rozdzielone przecinkami, np. 30, 7, 1.")
                )
            elif days[0] > limit:
                self.add_error("days_before", gettext("Najwyżej %(days)s dni przed.") % {"days": limit})
            else:
                cleaned["days_list"] = days
        used: list[str] = []
        for field in ("subject", "body"):
            text = cleaned.get(field) or ""
            used += planning.placeholder_names(text)
            if unknown := planning.unknown_placeholders(self.kind, text):
                self.add_error(field, planning.unknown_error(self.kind, unknown))
        if "link" in used and not cleaned.get("link") and "link" not in self.errors:
            self.add_error("link", planning.link_missing_error())
        return cleaned

    def rule_fields(self) -> dict:
        """Pola reguły do ``services.create_rule`` / ``update_rule`` – grupa jak w komunikacie planowanym."""
        cleaned = self.cleaned_data
        timing = cleaned.get("timing", RuleTiming.DAY_AT)
        return {
            "name": cleaned["name"],
            "enabled": bool(cleaned.get("enabled")),
            "group": cleaned["group"],
            "parameters": _stored_parameters(self.recipient_kwargs()),
            "target": self.target(),
            "stage": cleaned.get("stage") if self.kind == RuleKind.STAGE_DEADLINE else None,
            "subject_template": cleaned["subject"],
            "body_template": cleaned["body"],
            "link": cleaned.get("link", ""),
            "timing": timing,
            "days_before": cleaned.get("days_list", []) if timing == RuleTiming.DAY_AT else [],
            "send_time": cleaned["send_time"],
            "hours_before": cleaned.get("hours_before") if timing == RuleTiming.HOURS_BEFORE else None,
        }

    def unsaved_rule(self, competition, *, instance: MessageRule | None = None) -> MessageRule:
        """Reguła z danych formularza **bez zapisu** – do przycisku „Podgląd”."""
        rule = MessageRule(competition=competition, kind=self.kind)
        if instance is not None:
            rule.pk = instance.pk
            rule.created_by_id = instance.created_by_id
        for name, value in self.rule_fields().items():
            setattr(rule, name, value)
        return rule


def initial_for(rule: MessageRule) -> dict:
    """Wartości początkowe formularza edycji z zapisanej reguły (parametry grupy – identyfikatorami)."""
    parameters = rule.parameters or {}
    initial = {
        "name": rule.name,
        "enabled": rule.enabled,
        "group": rule.group,
        "subject": rule.subject_template,
        "body": rule.body_template,
        "link": rule.link,
        "timing": rule.timing,
        "days_before": ", ".join(str(days) for days in rule.days_before or []) or "0",
        "send_time": rule.send_time,
        "hours_before": rule.hours_before,
        "include_past_editions": bool(parameters.get("include_past_editions")),
    }
    for key in ("stage", "district", "region", "school", "grade", "workshop"):
        if key in parameters:
            initial[key] = parameters[key]
    if rule.kind == RuleKind.STAGE_DEADLINE and rule.stage_id:
        initial["stage"] = rule.stage_id
    return initial
