"""Wspólne przygotowanie testów TR-01: konkurs w trybie delegacji, etap przed otwarciem, okno.

Konkurs to Konkurs #1 przestawiony na kraje i delegacje – tak, jak operator przestawi ``iqo``
(ten sam zabieg, co w testach DEL-01). Etap otwiera się za dwa dni, okno tłumaczeń jest otwarte
teraz i zamyka się dzień przed etapem.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.tests.factories import CoordinatorFactory
from apps.accounts.tests.test_delegations import add, leader_for_country, make_delegations_competition
from apps.competitions.services import current_edition
from apps.competitions.tests.factories import ProblemFactory, StageFactory
from apps.problem_translations import services
from apps.problem_translations.models import SharingMode


def pdf_bytes(text: str = "Problem 1", pages: int = 1) -> bytes:
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer)
    for number in range(pages):
        sheet.drawString(72, 720, f"{text} – page {number + 1}")
        sheet.showPage()
    sheet.save()
    return buffer.getvalue()


def pdf_upload(name: str = "translation.pdf", text: str = "Aufgabe 1") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, pdf_bytes(text), content_type="application/pdf")


@pytest.fixture(autouse=True)
def clean_scan(monkeypatch):
    """clamd nie istnieje w testach – skan zwraca „czysty”, chyba że test powie inaczej."""
    monkeypatch.setattr("apps.submissions.antivirus.scan_stream", lambda fileobj, **kwargs: ("CLEAN", ""))


@pytest.fixture
def iqo(competition):
    return make_delegations_competition(competition)


@pytest.fixture
def coordinator():
    return CoordinatorFactory()


@pytest.fixture
def stage(iqo):
    return StageFactory(edition=current_edition(iqo), opens_at=timezone.now() + timedelta(days=2))


@pytest.fixture
def problem(stage):
    return ProblemFactory(
        stage=stage,
        number=1,
        title="Quantum harmonic oscillator",
        statement_pdf=SimpleUploadedFile(
            "official.pdf", pdf_bytes("Official"), content_type="application/pdf"
        ),
    )


@pytest.fixture
def window(stage, coordinator):
    now = timezone.now()
    return services.set_window(
        stage,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(days=1),
        sharing_mode=SharingMode.SEPARATE,
        actor=coordinator,
    )


@pytest.fixture
def leader_de(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "de-lead@example.test", "de")
    services.declare_languages(leader, ["de"], actor=leader.user)
    return leader


@pytest.fixture
def leader_at(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "at-lead@example.test", "at")
    services.declare_languages(leader, ["de"], actor=leader.user)
    return leader


@pytest.fixture
def leader_fr(iqo, coordinator):
    leader = leader_for_country(iqo, coordinator, "fr-lead@example.test", "fr")
    services.declare_languages(leader, ["fr"], actor=leader.user)
    return leader


def activated_student(leader, email="kid@example.test"):
    participant = add(leader, email=email)
    user = participant.user
    user.is_active = True
    user.email_verified_at = timezone.now()
    user.save(update_fields=["is_active", "email_verified_at"])
    return participant


def approve_text(leader, problem, coordinator, language="de", body="Aufgabe: $x^2$", title="Oszillator"):
    services.save_draft(leader, problem, language, title=title, body_md=body, actor=leader.user)
    services.submit(leader, problem, language, actor=leader.user)
    translation = services.find_translation(leader, problem, language)
    services.approve(translation, actor=coordinator)
    translation.refresh_from_db()
    return translation


def open_stage(stage):
    """Etap otwarty: przesuwamy jego początek w przeszłość (okno gaśnie razem z nim)."""
    stage.opens_at = timezone.now() - timedelta(minutes=5)
    stage.save(update_fields=["opens_at"])
    return stage
