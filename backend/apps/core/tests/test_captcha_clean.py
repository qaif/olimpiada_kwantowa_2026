"""Sprzątanie wygasłych wyzwań CAPTCHA (``apps.core.tasks.captcha_clean``).

``django-simple-captcha`` zapisuje jeden wiersz ``CaptchaStore`` na każde wyrenderowane wyzwanie
i sam nigdy ich nie kasuje – bez zadania z ``CELERY_BEAT_SCHEDULE`` tabela rośnie proporcjonalnie
do ruchu na formularzach rejestracji, nie do liczby kont, które faktycznie powstały.
"""

from datetime import timedelta

import pytest
from captcha.models import CaptchaStore
from django.conf import settings
from django.utils import timezone

from apps.core.tasks import captcha_clean

pytestmark = pytest.mark.django_db


def expired_challenge() -> str:
    """Zakłada wyzwanie i cofa mu termin ważności do przeszłości."""
    hashkey = CaptchaStore.generate_key()
    CaptchaStore.objects.filter(hashkey=hashkey).update(expiration=timezone.now() - timedelta(minutes=1))
    return hashkey


def test_captcha_clean_removes_only_expired_challenges():
    expired = expired_challenge()
    fresh_key = CaptchaStore.generate_key()  # domyślny termin ważności – jeszcze nie minął

    removed = captcha_clean()

    assert removed == 1
    assert not CaptchaStore.objects.filter(hashkey=expired).exists()
    assert CaptchaStore.objects.filter(hashkey=fresh_key).exists()


def test_captcha_clean_is_a_no_op_without_expired_rows():
    CaptchaStore.generate_key()

    assert captcha_clean() == 0


def test_beat_runs_captcha_clean_hourly():
    entry = settings.CELERY_BEAT_SCHEDULE["captcha-clean"]

    assert entry["task"] == "apps.core.tasks.captcha_clean"
    assert entry["schedule"] == 3600.0
