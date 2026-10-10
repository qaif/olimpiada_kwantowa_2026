"""Access log gunicorna bez tokenów z adresów (``config.gunicorn_logging``, audyt 10.10.2026)."""

import datetime
from types import SimpleNamespace

import pytest

from config.gunicorn_logging import TokenMaskingLogger, mask_secrets


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("/activate/AbC123xyz/", "/activate/***/"),
        ("/fizyczna/activate/AbC123xyz/", "/fizyczna/activate/***/"),
        ("/reset/MTIz/c4k2-0123456789abcdef/", "/reset/***/***/"),
        ("/zaproszenie/9f8e7d6c5b4a/", "/zaproszenie/***/"),
        ("/zaproszenie/wideo/klucz-pokoju-123/", "/zaproszenie/wideo/***/"),
        ("/zgoda/tok3n/", "/zgoda/***/"),
        ("/opiekun/zgoda/tok3n/", "/opiekun/zgoda/***/"),
        ("/account/email/confirm/tok3n/", "/account/email/confirm/***/"),
        ("/forum/unsubscribe/tok3n/", "/forum/unsubscribe/***/"),
        ("/delegation/accept/tok3n/logout/", "/delegation/accept/***/logout/"),
        ("/me/messages/new/tok3n/", "/me/messages/new/***/"),
        ("/login/?next=/me/&token=sekret&x=1", "/login/?next=/me/&token=***&x=1"),
        ("https://olimpiada.example/reset/MTIz/tok/", "https://olimpiada.example/reset/***/***/"),
    ],
)
def test_secret_segments_are_masked(raw, masked):
    assert mask_secrets(raw) == masked


@pytest.mark.parametrize(
    "path",
    [
        "/activate/resend/",
        "/reset/done/",
        "/zgoda/dziekujemy/",
        "/zaproszenie/dziekujemy/",
        "/me/zgloszenia/",
        "/static/css/app.css",
        "-",
        "",
    ],
)
def test_addresses_without_secrets_stay_readable(path):
    assert mask_secrets(path) == path


def test_access_log_atoms_mask_request_line_path_query_and_referer():
    # Bez konfiguracji gunicorna – atoms() jej nie czyta.
    logger = TokenMaskingLogger.__new__(TokenMaskingLogger)
    environ = {
        "REQUEST_METHOD": "GET",
        "RAW_URI": "/activate/AbC123xyz/?code=999",
        "PATH_INFO": "/activate/AbC123xyz/",
        "QUERY_STRING": "code=999",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_REFERER": "https://olimpiada.example/reset/MTIz/tok/",
        "REMOTE_ADDR": "172.30.1.250",
    }
    req = SimpleNamespace(headers=[("REFERER", "https://olimpiada.example/reset/MTIz/tok/")])
    resp = SimpleNamespace(status="200 OK", headers=[], sent=10, response_length=10)
    atoms = logger.atoms(resp, req, environ, datetime.timedelta(seconds=1))

    assert atoms["r"] == "GET /activate/***/?code=*** HTTP/1.1"
    assert atoms["U"] == "/activate/***/"
    assert atoms["q"] == "code=***"
    assert atoms["f"] == "https://olimpiada.example/reset/***/***/"
    assert atoms["{referer}i"] == "https://olimpiada.example/reset/***/***/"
    # Dokładnie tak, jak gunicorn składa linijkę z --access-logformat (formatowanie %).
    line = "%(r)s %(f)s %({referer}i)s" % atoms  # noqa: UP031
    assert "AbC123xyz" not in line and "MTIz" not in line and "999" not in line
