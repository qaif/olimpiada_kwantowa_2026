"""Pole ``CheckedEmailField`` (MAIL-02 § 1.3): jedno pytanie o literówkę, „zostaw”, „użyj”, blokada."""

from __future__ import annotations

import pytest
from django import forms

from apps.email_delivery import dnscheck
from apps.email_delivery.fields import ACCEPT_SUFFIX, KEEP_SUFFIX, CheckedEmailField


class SampleForm(forms.Form):
    email = CheckedEmailField(label="Adres e-mail", max_length=254)


class QuietForm(forms.Form):
    email = CheckedEmailField(label="Adres", suggest_typos=False)


@pytest.fixture
def dead_domains(monkeypatch):
    """Domeny, które „DNS” uznaje za martwe – bez sieci i bez cache'u."""
    dead = {"o2.plo", "nie-ma-takiej.pl"}
    monkeypatch.setattr(
        "apps.email_delivery.fields.domain_accepts_mail", lambda domain: False if domain in dead else None
    )
    return dead


def test_typo_stops_the_form_once_with_a_suggestion():
    form = SampleForm({"email": "jan@gmial.com"})

    assert not form.is_valid()
    assert form.errors["email"][0].startswith("Sprawdź adres – czy chodziło Ci o jan@gmail.com?")
    html = str(form["email"])
    assert f'name="email{ACCEPT_SUFFIX}" value="jan@gmail.com"' in html
    assert f'name="email{KEEP_SUFFIX}" value="jan@gmial.com"' in html
    assert 'id="id_email_suggestion"' in html


def test_resubmitting_the_same_address_keeps_it():
    form = SampleForm({"email": "jan@gmial.com", f"email{KEEP_SUFFIX}": "jan@gmial.com"})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "jan@gmial.com"
    # Formularz wracający z błędem innego pola niesie „zostaw” dalej – bez drugiego pytania.
    assert f'name="email{KEEP_SUFFIX}" value="jan@gmial.com"' in str(form["email"])


def test_a_different_address_is_checked_again():
    form = SampleForm({"email": "jan@hotmial.com", f"email{KEEP_SUFFIX}": "jan@gmial.com"})

    assert not form.is_valid()
    assert "jan@hotmail.com" in form.errors["email"][0]


def test_accepting_the_suggestion_replaces_the_value():
    form = SampleForm({"email": "jan@gmial.com", f"email{ACCEPT_SUFFIX}": "jan@gmail.com"})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "jan@gmail.com"


def test_accepted_value_is_validated_like_any_other():
    form = SampleForm({"email": "jan@gmial.com", f"email{ACCEPT_SUFFIX}": "to nie jest adres"})

    assert not form.is_valid()


def test_correct_address_passes_without_any_extra_markup():
    form = SampleForm({"email": "jan+olimp@gmail.com"})

    assert form.is_valid()
    html = str(form["email"])
    assert "data-email-check" in html
    assert ACCEPT_SUFFIX not in html and KEEP_SUFFIX not in html


def test_dead_domain_is_blocked_even_when_kept(dead_domains):
    form = SampleForm({"email": "x@nie-ma-takiej.pl", f"email{KEEP_SUFFIX}": "x@nie-ma-takiej.pl"})

    assert not form.is_valid()
    assert (
        form.errors["email"][0]
        == "Domena nie-ma-takiej.pl nie istnieje albo nie przyjmuje poczty. Sprawdź adres."
    )
    assert KEEP_SUFFIX not in str(form["email"])


def test_dead_domain_with_a_typo_offers_the_fix(dead_domains):
    form = SampleForm({"email": "kcadera@o2.plo"})

    assert not form.is_valid()
    assert "Czy chodziło Ci o kcadera@o2.pl?" in form.errors["email"][0]
    html = str(form["email"])
    assert 'value="kcadera@o2.pl"' in html
    assert KEEP_SUFFIX not in html  # „zostaw” nie ma sensu – na tę domenę nic nie dotrze

    fixed = SampleForm({"email": "kcadera@o2.plo", f"email{ACCEPT_SUFFIX}": "kcadera@o2.pl"})
    assert fixed.is_valid(), fixed.errors


def test_unknown_dns_answer_lets_the_address_through(monkeypatch):
    monkeypatch.setattr(dnscheck, "lookup", lambda domain, client=None: None)

    assert SampleForm({"email": "kto@nieznana-domena.pl"}).is_valid()


def test_dns_switch_is_honoured_by_the_field(settings, monkeypatch):
    settings.EMAIL_DOMAIN_DNS_CHECK = True
    monkeypatch.setattr(dnscheck, "lookup", lambda domain, client=None: False)

    form = SampleForm({"email": "kto@martwa-domena.pl"})
    assert not form.is_valid()
    assert form.errors["email"][0].startswith("Domena martwa-domena.pl")


def test_idn_and_plus_addressing():
    assert SampleForm({"email": "jan+test@żółw.pl"}).is_valid()
    form = SampleForm({"email": "jan+test@żółw.plo"})
    assert not form.is_valid()
    assert "jan+test@żółw.pl" in form.errors["email"][0]


def test_quiet_variant_never_asks_about_typos(dead_domains):
    assert QuietForm({"email": "jan@gmial.com"}).is_valid()
    assert not QuietForm({"email": "x@nie-ma-takiej.pl"}).is_valid()


def test_state_does_not_leak_between_form_instances():
    first = SampleForm({"email": "jan@gmial.com"})
    assert not first.is_valid()
    second = SampleForm()
    assert ACCEPT_SUFFIX not in str(second["email"])
    assert SampleForm.base_fields["email"].widget.suggestion is None


def test_unchanged_initial_address_is_not_questioned(dead_domains):
    # Koordynator poprawia nazwisko konta z nietypowym adresem – bez pytania o adres, którego nie zmieniał.
    form = SampleForm({"email": "jan@gmial.com"}, initial={"email": "jan@gmial.com"})
    assert form.is_valid(), form.errors

    changed = SampleForm({"email": "ola@gmial.com"}, initial={"email": "jan@gmial.com"})
    assert not changed.is_valid()


def test_widget_carries_translated_hints_for_the_script():
    html = str(SampleForm()["email"])
    assert 'data-email-hint="Czy chodziło Ci o %s?"' in html
    assert 'data-email-use="Użyj %s"' in html
