"""Podpowiedź literówek w imporcie uczniów (REG-UX-01 § 1.3) – bez bazy: same wiersze po oczyszczeniu."""

from __future__ import annotations

from apps.accounts.bulk_registration import (
    ACTION_CREATE,
    ACTION_SKIP,
    ImportRow,
    apply_email_fixes,
    suggest_email_fixes,
)
from apps.accounts.bulk_registration import _clean_email as clean_email


def _row(number: int, email: str, action: str = ACTION_CREATE) -> ImportRow:
    return ImportRow(number=number, first_name="Kasia", last_name="Nowak", email=email, action=action)


def test_preview_suggests_fixes_only_for_importable_rows():
    rows = [_row(2, "kasia@gmial.com"), _row(3, "ola@wp.pl"), _row(4, "jan@gmail.con", ACTION_SKIP)]

    suggest_email_fixes(rows)

    assert [row.email_suggestion for row in rows] == ["kasia@gmail.com", "", ""]


def test_production_typos_are_suggested():
    rows = [
        _row(2, "a@gmail.con"),
        _row(3, "b@gmial.com"),
        _row(4, "c@szkola.edu.pl.pl"),
        _row(5, "d@onet.pll"),
        _row(6, "e@lo1.edu.pl"),
    ]

    suggest_email_fixes(rows)

    assert [row.email_suggestion for row in rows] == [
        "a@gmail.com",
        "b@gmail.com",
        "c@szkola.edu.pl",
        "d@onet.pl",
        "",  # domena szkoły – bez podpowiedzi
    ]


def test_fix_is_recomputed_from_the_basket_and_applied_only_to_chosen_rows():
    rows = [_row(2, "kasia@gmial.com"), _row(3, "ola@gmial.com"), _row(4, "jan@wp.pl")]

    fixed = apply_email_fixes(rows, {2, 4, 99})

    assert fixed == 1
    assert [row.email for row in rows] == ["kasia@gmail.com", "ola@gmial.com", "jan@wp.pl"]


def test_invalid_address_with_an_obvious_fix_says_so_in_the_error():
    row = ImportRow(number=2)

    assert clean_email("jan@gmailcom", row) == ""
    assert row.errors == ["adres e-mail jest nieprawidłowy – czy chodziło o jan@gmail.com?"]

    other = ImportRow(number=3)
    clean_email("to nie adres", other)
    assert other.errors == ["adres e-mail jest nieprawidłowy"]
