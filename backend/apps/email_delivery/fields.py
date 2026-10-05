"""Pole adresu e-mail z podpowiedzią literówek i twardą blokadą martwych domen (MAIL-02 § 1.3).

Podmiana jednej linii w formularzu: ``forms.EmailField(...)`` → ``CheckedEmailField(...)`` (te same
argumenty). Szablony się nie zmieniają – wszystko, co pole dokłada (komunikat, pole wyboru „Użyj …”,
ukryte „zostaw”), rysuje jego widżet w miejscu ``{{ field }}``, więc działa w każdym motywie.

Stan między dwoma wysłaniami formularza jedzie w samym formularzu, nie w sesji:

- ``<pole>__keep`` (ukryte) – adres, którego dotyczyła podpowiedź. Ponowne wysłanie **tego samego**
  adresu przechodzi; inny adres to nowe sprawdzenie,
- ``<pole>__accept`` (pole wyboru) – zaznaczone podmienia wartość na podpowiedź. Pole wyboru,
  a nie przycisk wysyłki: przycisk stałby w DOM przed przyciskiem formularza, więc Enter w dowolnym
  polu (domyślny przycisk = pierwszy w kolejności) po cichu przyjmowałby poprawkę.

Widżet i pole są kopiowane per instancja formularza (``Form.__init__`` robi ``deepcopy`` pól),
więc stan podpowiedzi zapisany na widżecie nie przecieka między żądaniami.
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .dnscheck import domain_accepts_mail
from .typos import suggest

KEEP_SUFFIX = "__keep"
ACCEPT_SUFFIX = "__accept"


class EmailCheckInput(forms.EmailInput):
    """``<input type="email">`` + blok podpowiedzi rysowany po nieudanym sprawdzeniu."""

    template_name = "email_delivery/widgets/email_check.html"

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.suggestion: str | None = None
        self.typed: str = ""
        self.allow_keep = True
        self.kept: str = ""

    def __deepcopy__(self, memo):
        copy = super().__deepcopy__(memo)
        copy.suggestion, copy.typed, copy.allow_keep, copy.kept = None, "", True, ""
        return copy

    def value_from_datadict(self, data, files, name):
        self.kept = (data.get(name + KEEP_SUFFIX) or "").strip()
        accepted = (data.get(name + ACCEPT_SUFFIX) or "").strip()
        if accepted:
            return accepted
        return super().value_from_datadict(data, files, name)

    def value_omitted_from_data(self, data, files, name):
        return super().value_omitted_from_data(data, files, name) and name + ACCEPT_SUFFIX not in data

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        # Umowa z ``static/email_delivery/email-check.js``: skrypt szuka pól po ``data-email-check``
        # i bierze z atrybutów przetłumaczone zdania (``%s`` = adres). Listy domen ma u siebie.
        widget_attrs = context["widget"]["attrs"]
        widget_attrs["data-email-check"] = ""
        widget_attrs["data-email-hint"] = str(_("Czy chodziło Ci o %s?"))
        widget_attrs["data-email-use"] = str(_("Użyj %s"))
        context["widget"].update(
            {
                "suggestion": self.suggestion,
                "typed": self.typed,
                "allow_keep": self.allow_keep,
                "accept_name": name + ACCEPT_SUFFIX,
                "keep_name": name + KEEP_SUFFIX,
            }
        )
        return context


class CheckedEmailField(forms.EmailField):
    """``EmailField`` z podpowiedzią literówki (raz) i blokadą domeny bez poczty (zawsze)."""

    widget = EmailCheckInput
    default_error_messages = {
        "email_typo": _(
            "Sprawdź adres – czy chodziło Ci o %(suggestion)s? Zaznacz poprawkę poniżej albo wyślij "
            "formularz jeszcze raz, żeby zostawić adres bez zmian."
        ),
        "no_mail": _("Domena %(domain)s nie istnieje albo nie przyjmuje poczty. Sprawdź adres."),
        "no_mail_suggestion": _(
            "Domena %(domain)s nie istnieje albo nie przyjmuje poczty. Czy chodziło Ci o %(suggestion)s?"
        ),
    }

    def __init__(self, *, suggest_typos: bool = True, **kwargs):
        # ``suggest_typos=False`` – sama twarda blokada. Dla formularza, którego widok nie rysuje
        # pola ponownie (akcja POST z komunikatem i przekierowaniem): pytanie „czy chodziło Ci o …”
        # bez pola „zostaw” zablokowałoby nietypowy, ale poprawny adres na zawsze.
        self.suggest_typos = suggest_typos
        self.unchanged_value = ""
        super().__init__(**kwargs)

    def get_bound_field(self, form, field_name):
        # Wartość początkowa formularza (adres, który konto już ma – edycja konta przez koordynatora).
        # Pole jest kopią per formularz, więc zapamiętanie jej tutaj nie przecieka między żądaniami.
        initial = form.get_initial_for_field(self, field_name)
        self.unchanged_value = initial.strip() if isinstance(initial, str) else ""
        return super().get_bound_field(form, field_name)

    def clean(self, value):
        value = super().clean(value)
        widget = self.widget
        widget.suggestion, widget.typed, widget.allow_keep = None, "", True
        if not value:
            return value
        if self.unchanged_value and value.lower() == self.unchanged_value.lower():
            # Adres bez zmian (np. koordynator poprawia tylko nazwisko) – nie pytamy o to, co już jest
            # w bazie. Stan doręczalności takiego adresu pokazują odbicia (§ 2), nie formularz.
            return value
        domain = value.rpartition("@")[2]
        suggestion = suggest(value) if self.suggest_typos else None
        if domain_accepts_mail(domain) is False:
            # Twarda blokada: „zostaw” nie przechodzi – na tę domenę żaden list nie dotrze.
            widget.suggestion, widget.typed, widget.allow_keep = suggestion, value, False
            code = "no_mail_suggestion" if suggestion else "no_mail"
            raise ValidationError(
                self.error_messages[code], code=code, params={"domain": domain, "suggestion": suggestion}
            )
        if suggestion and value.lower() != widget.kept.lower():
            widget.suggestion, widget.typed = suggestion, value
            raise ValidationError(
                self.error_messages["email_typo"], code="email_typo", params={"suggestion": suggestion}
            )
        if suggestion:
            # Adres zostawiony świadomie. Gdy formularz wróci z błędem **innego** pola, ukryte „zostaw”
            # musi pojechać dalej – inaczej to samo pytanie wracałoby przy każdym kolejnym wysłaniu.
            widget.typed = value
        return value
