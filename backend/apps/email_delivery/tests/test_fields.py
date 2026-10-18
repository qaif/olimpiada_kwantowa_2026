"""Pole ``CheckedEmailField`` (MAIL-02 § 1.3, REG-UX-01 § 1.2): literówka, „Popraw”, „zostaw”, blokada."""

from __future__ import annotations

import pytest
from django import forms

from apps.email_delivery import dnscheck
from apps.email_delivery.fields import (
    ACCEPT_SUFFIX,
    KEEP_SUFFIX,
    CheckedEmailField,
    EmailDomainCheckMixin,
)


class SampleForm(EmailDomainCheckMixin, forms.Form):
    email = CheckedEmailField(label="Adres e-mail", max_length=254)


class QuietForm(EmailDomainCheckMixin, forms.Form):
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
    # REG-UX-01 § 1.2: „Tak, adres jest poprawny” jest pierwszym przyciskiem wysyłki (Enter = zostaw).
    assert html.index('id="id_email_keep"') < html.index('id="id_email_accept"')
    assert "Tak, adres jan@gmial.com jest poprawny" in html
    assert "Popraw na jan@gmail.com" in html
    assert 'type="checkbox"' not in html


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


def test_dead_domain_with_a_typo_asks_first_then_blocks_with_the_fix(dead_domains):
    # Najpierw (bez DNS) pytanie o literówkę; zostawiony adres trafia na DNS i dostaje blokadę z poprawką.
    first = SampleForm({"email": "kcadera@o2.plo"})
    assert not first.is_valid()
    assert "czy chodziło Ci o kcadera@o2.pl?" in first.errors["email"][0]

    kept = SampleForm({"email": "kcadera@o2.plo", f"email{KEEP_SUFFIX}": "kcadera@o2.plo"})
    assert not kept.is_valid()
    assert "Czy chodziło Ci o kcadera@o2.pl?" in kept.errors["email"][0]
    assert kept.errors["email"][0].startswith("Domena o2.plo")
    html = str(kept["email"])
    assert 'value="kcadera@o2.pl"' in html
    assert KEEP_SUFFIX not in html  # „zostaw” nie ma sensu – na tę domenę nic nie dotrze

    fixed = SampleForm({"email": "kcadera@o2.plo", f"email{ACCEPT_SUFFIX}": "kcadera@o2.pl"})
    assert fixed.is_valid(), fixed.errors


class WithOtherField(EmailDomainCheckMixin, forms.Form):
    email = CheckedEmailField()
    code = forms.CharField()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("code") == "bot":
            raise forms.ValidationError("antyspam")
        return cleaned


def test_dns_is_asked_only_after_all_other_validation_passes(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr("apps.email_delivery.fields.domain_accepts_mail", lambda domain: asked.append(domain))

    assert not WithOtherField({"email": "x@szkola.pl"}).is_valid()  # brak pola – bez DNS
    assert not WithOtherField({"email": "x@szkola.pl", "code": "bot"}).is_valid()  # clean() – bez DNS
    assert not WithOtherField({"email": "x@gmial.com", "code": "ok"}).is_valid()  # literówka – bez DNS
    assert asked == []

    assert WithOtherField({"email": "x@szkola.pl", "code": "ok"}).is_valid()
    assert asked == ["szkola.pl"]


def test_field_without_the_mixin_never_asks_dns(monkeypatch):
    class Plain(forms.Form):
        email = CheckedEmailField()

    monkeypatch.setattr("apps.email_delivery.fields.domain_accepts_mail", lambda domain: False)
    assert Plain({"email": "x@szkola.pl"}).is_valid()


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


# --- REG-UX-01 § 1.2: „Popraw” należy do adresu, dla którego powstał ---------------------------


def test_fix_button_is_ignored_when_the_field_was_corrected_by_hand():
    # Uczeń poprawił adres ręcznie i nacisnął Enter – stara poprawka nie może go nadpisać.
    form = SampleForm({"email": "ola@wp.pl", f"email{ACCEPT_SUFFIX}": "jan@gmail.com"})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "ola@wp.pl"


def test_fix_button_with_a_value_that_is_not_the_suggestion_is_ignored():
    form = SampleForm({"email": "jan@gmial.com", f"email{ACCEPT_SUFFIX}": "atak@zly.example"})

    assert not form.is_valid()
    assert "jan@gmail.com" in form.errors["email"][0]


def test_dead_domain_offers_only_the_fix(dead_domains):
    kept = SampleForm({"email": "kcadera@o2.plo", f"email{KEEP_SUFFIX}": "kcadera@o2.plo"})

    assert not kept.is_valid()
    html = str(kept["email"])
    assert 'id="id_email_accept"' in html
    assert 'id="id_email_keep"' not in html


@pytest.mark.parametrize(
    ("typed", "fixed"),
    [("jan@gmailcom", "jan@gmail.com"), ("jan@gmail", "jan@gmail.com"), ("ola@wppl", "ola@wp.pl")],
)
def test_address_without_a_dot_in_the_domain_gets_the_fix_in_the_syntax_error(typed, fixed):
    form = SampleForm({"email": typed})

    assert not form.is_valid()
    assert form.errors["email"][0] == f"Adres e-mail jest niepełny – czy chodziło Ci o {fixed}?"
    html = str(form["email"])
    assert f'value="{fixed}"' in html
    assert 'id="id_email_keep"' not in html  # takiego adresu nie da się zostawić

    accepted = SampleForm({"email": typed, f"email{ACCEPT_SUFFIX}": fixed})
    assert accepted.is_valid(), accepted.errors
    assert accepted.cleaned_data["email"] == fixed


def test_invalid_address_without_an_obvious_fix_keeps_the_plain_error():
    form = SampleForm({"email": "jan@szkola"})

    assert not form.is_valid()
    assert "niepełny" not in form.errors["email"][0]
    assert ACCEPT_SUFFIX not in str(form["email"])


def test_quiet_variant_has_no_fix_for_invalid_syntax():
    form = QuietForm({"email": "jan@gmailcom"})

    assert not form.is_valid()
    assert "niepełny" not in form.errors["email"][0]


@pytest.mark.parametrize(
    ("address", "fixed"),
    [
        ("jan@gmail.con", "jan@gmail.com"),
        ("jan@gmial.com", "jan@gmail.com"),
        ("jan@szkola.edu.pl.pl", "jan@szkola.edu.pl"),
        # Jednoliterowy TLD odrzuca już składnia – poprawka idzie w komunikacie o błędzie składni.
        ("jan@wp.p", "jan@wp.pl"),
        ("jan@onet.pll", "jan@onet.pl"),
    ],
)
def test_production_typos_stop_the_form(address, fixed):
    # Literówki z produkcji (zgłoszenie organizatora 18.10.2026), które dały duplikaty i odbicia.
    form = SampleForm({"email": address})

    assert not form.is_valid()
    assert f"czy chodziło Ci o {fixed}?" in form.errors["email"][0]
    assert SampleForm({"email": address, f"email{ACCEPT_SUFFIX}": fixed}).is_valid()


def test_school_domain_is_never_questioned(dead_domains):
    for address in ("dyrektor@lo1.edu.pl", "jan@sp1.pl", "jan@op.edu.pl", "jan@int.lodz.pl"):
        assert SampleForm({"email": address}).is_valid(), address
