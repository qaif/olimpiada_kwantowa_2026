"""Klucz API: szyfrowanie w bazie, maskowanie w pamięci i odrzucanie wartości, które kluczem nie są."""

from __future__ import annotations

import pickle

import pytest

from apps.ai_grading import crypto

from .conftest import FAKE_KEY

# --- klucz ----------------------------------------------------------------------------------------


def test_key_round_trips_through_encryption():
    token = crypto.encrypt_key(FAKE_KEY)

    assert FAKE_KEY not in token
    assert crypto.decrypt_key(token).reveal() == FAKE_KEY


def test_changed_secret_key_makes_the_key_unreadable_instead_of_crashing(settings):
    token = crypto.encrypt_key(FAKE_KEY)
    settings.SECRET_KEY = "zupełnie-inny-klucz-serwera-" * 3

    assert crypto.decrypt_key(token) is None


def test_the_key_uses_its_own_derivation_label(settings):
    """Ten sam ``SECRET_KEY``, inny cel – token z 2FA nie odszyfrowuje się jako klucz API."""
    from apps.accounts.twofactor import encrypt_secret

    assert crypto.decrypt_key(encrypt_secret("ABCDEFGHIJKLMNOP")) is None


def test_decrypted_key_never_shows_itself_in_repr_or_str():
    key = crypto.ApiKey(FAKE_KEY)

    assert FAKE_KEY not in repr(key)
    assert FAKE_KEY not in str(key)
    assert f"{key}".endswith("WXYZ)")
    assert FAKE_KEY not in repr({"klucz": key})


def test_decrypted_key_refuses_to_be_pickled():
    with pytest.raises(TypeError):
        pickle.dumps(crypto.ApiKey(FAKE_KEY))


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("", "Wklej"),
        ("haslo123", "sk-ant-"),
        ("sk-ant-admin01-" + "x" * 40, "administracyjny"),
        ("sk-ant-krótki", "w całości"),
    ],
)
def test_obviously_wrong_values_are_refused_without_echoing_them(raw, fragment):
    with pytest.raises(crypto.InvalidApiKey) as info:
        crypto.normalise_key(raw)

    assert fragment in str(info.value)
    if raw:
        assert raw not in str(info.value)


def test_whitespace_around_a_pasted_key_is_removed():
    assert crypto.normalise_key(f"  {FAKE_KEY}\n") == FAKE_KEY
