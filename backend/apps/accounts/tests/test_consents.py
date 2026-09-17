"""Zgody rejestracyjne: zestaw, reguła niepełnoletności, dowody i API.

Testy pilnują czterech rzeczy, na których stoi „zgoda jako dowód”, a nie „zgoda jako ptaszek”:

- **etykieta prowadzi do właściwego dokumentu.** Zgoda bez odnośnika do treści, na którą się
  zgadzamy, nie jest zgodą świadomą – a odnośnik pod złym adresem jest gorszy od jego braku,
- **reguła wieku jest zachowawcza.** Liczymy po roczniku, bo daty dziennej nie zbieramy; rocznik
  „minus 18” to jeszcze osoba, która może mieć 17 lat,
- **brak zgody blokuje rejestrację przed zapisem czegokolwiek** – żadnego konta, żadnego profilu,
- **zgoda zostawia ślad**: wiersz ``ConsentRecord`` z wersją dokumentu i jeden wpis audytowy.
"""

from datetime import date

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import consents
from apps.accounts.consents import BY_KIND, CONSENTS, ConsentKind, ConsentSource
from apps.accounts.models import ConsentRecord, Participant, User
from apps.accounts.services import (
    record_consents,
    set_publish_name_consent,
    validate_consents,
)
from apps.core.models import AuditLog

from .factories import ParticipantFactory, activate

REGISTER_URL = "/api/auth/register/participant/"
CONSENTS_URL = "/api/auth/consents/"
PASSWORD = "Poprawne-Haslo-2026"


def payload(**overrides) -> dict:
    data = {
        "email": "zgody@example.test",
        "password": PASSWORD,
        "first_name": "Anna",
        "last_name": "Nowak",
        "school": "LO nr 3",
        "district": "mazowieckie",
        "grade": 2,
        "birth_year": 1990,
        "phone": "600 100 200",
        "terms_consent": True,
        "gdpr_consent": True,
    }
    data.update(overrides)
    return data


@pytest.fixture
def api():
    return APIClient()


def minor_year() -> int:
    """Rocznik osoby, od której wymagamy zgody opiekuna – dokładnie na granicy reguły."""
    return timezone.localdate().year - consents.MINOR_MAX_AGE


def adult_year() -> int:
    """Pierwszy rocznik, dla którego zgoda opiekuna przestaje być wymagana."""
    return timezone.localdate().year - consents.MINOR_MAX_AGE - 1


# --- zestaw zgód ------------------------------------------------------------------------------


def test_consent_set_covers_the_four_declarations():
    assert [consent.kind for consent in CONSENTS] == [
        ConsentKind.TERMS,
        ConsentKind.PRIVACY,
        ConsentKind.GUARDIAN,
        ConsentKind.PUBLISH_NAME,
    ]
    assert [consent.field_name for consent in CONSENTS] == [
        "terms_consent",
        "gdpr_consent",
        "guardian_consent",
        "publish_name_consent",
    ]


@pytest.mark.parametrize(
    ("kind", "slug"),
    [
        (ConsentKind.TERMS, "regulamin"),
        (ConsentKind.PRIVACY, "rodo"),
        (ConsentKind.GUARDIAN, "zgoda-opiekuna"),
    ],
)
def test_label_links_to_the_document_it_is_about(db, kind, slug):
    """Etykieta niesie odnośnik do dokumentu – w nowej karcie i bez dostępu do ``window.opener``."""
    html = str(consents.label(BY_KIND[kind]))

    assert f'href="/dokumenty/{slug}/"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html


def test_publish_name_label_has_no_document_link(db):
    """Zgoda na publikację nazwiska nie ma osobnego dokumentu – i nie udajemy, że ma."""
    html = str(consents.label(BY_KIND[ConsentKind.PUBLISH_NAME]))

    assert "<a " not in html
    assert "imienia i nazwiska" in html


def test_privacy_label_names_the_organizer_from_site_settings(db):
    """Nazwa organizatora pochodzi z ``/cms/``, nie z literału w kodzie."""
    from apps.cms.models import SiteSettings

    SiteSettings.objects.update(organizer_name="Fundacja Testowa")

    html = str(consents.label(BY_KIND[ConsentKind.PRIVACY]))

    assert "Fundacja Testowa" in html


def test_label_falls_back_to_the_canonical_document_path_without_the_page(db):
    """Przed ``seed_legacy_content`` strony nie ma – etykieta prowadzi pod adres docelowy."""
    assert consents.document_url("zgoda-opiekuna") == "/dokumenty/zgoda-opiekuna/"


# --- odnośnik prowadzi do PDF-a, nie do podstrony ----------------------------------------------


def publish_document_page(slug: str):
    """Opublikowana strona dokumentu o tym slugu – bez seedów, które wgrywają całą treść.

    Sekcję ``/dokumenty/`` zakłada ``seed_legacy_content``, a nie migracja drzewa stron, więc
    w bazie testowej trzeba ją najpierw postawić. Uruchomienie całego seeda byłoby tu kosztem
    bez wartości: przedmiotem testu jest wybór adresu, a nie import treści.
    """
    from apps.cms.models import DocumentIndexPage, DocumentPage, HomePage

    index = DocumentIndexPage.objects.first()
    if index is None:
        index = DocumentIndexPage(title="Dokumenty", slug="dokumenty")
        HomePage.objects.get().add_child(instance=index)
        index.save_revision().publish()
    page = DocumentPage(title=slug.capitalize(), slug=slug)
    index.add_child(instance=page)
    page.save_revision().publish()
    return page


def attach_file(page, *, filename: str, label: str = ""):
    """Przypina do strony plik o tej nazwie (treść jest nieistotna – liczy się rozszerzenie)."""
    from django.core.files.base import ContentFile
    from wagtail.documents import get_document_model

    from apps.cms.models import DocumentPageAttachment

    document = get_document_model()(title=f"{page.title} – {filename}")
    document.file.save(filename, ContentFile(b"tresc-testowa"), save=True)
    DocumentPageAttachment.objects.create(page=page, document=document, label=label)
    return document


def test_document_link_points_at_the_pdf_when_the_page_has_one(db):
    """Uwaga organizatora z 16.09: „linki do regulaminów powinny prowadzić do PDF-ów”.

    Zgoda jest oświadczeniem złożonym pod konkretną wersją dokumentu, a wersją podpisaną przez
    organizatora jest PDF – strona CMS-a jest jego czytelną transkrypcją z kotwicami.
    """
    page = publish_document_page("regulamin")
    pdf = attach_file(page, filename="regulamin.pdf", label="PDF do druku")

    assert consents.document_link("regulamin") == pdf.url
    assert f'href="{pdf.url}"' in str(consents.label(BY_KIND[ConsentKind.TERMS]))


def test_document_link_skips_attachments_that_are_not_pdfs(db):
    """Plik źródłowy .docx jest materiałem redakcyjnym, a nie dokumentem do podpisania."""
    page = publish_document_page("regulamin")
    attach_file(page, filename="regulamin.docx", label="Wersja źródłowa (DOCX)")

    assert consents.document_link("regulamin") == page.get_url()


def test_document_link_falls_back_to_the_page_without_any_pdf(db):
    """Dokument bywa opublikowany, zanim organizator wgra plik – zgoda nie może zostać bez linku."""
    publish_document_page("rodo")

    assert consents.document_link("rodo") == "/dokumenty/rodo/"
    assert consents.document_link("zgoda-opiekuna") == "/dokumenty/zgoda-opiekuna/"
    assert consents.document_link("") == ""


def test_consent_description_carries_both_the_page_and_the_file(db):
    """API oddaje dwa adresy, bo to dwie różne rzeczy: adres do zacytowania i plik do pobrania."""
    page = publish_document_page("regulamin")
    pdf = attach_file(page, filename="regulamin.pdf")

    terms = next(row for row in consents.descriptions() if row["kind"] == ConsentKind.TERMS)

    assert terms["document_url"] == page.get_url()
    assert terms["document_link"] == pdf.url


def test_versions_are_recorded_per_document():
    assert BY_KIND[ConsentKind.TERMS].version == consents.TERMS_VERSION
    assert BY_KIND[ConsentKind.PRIVACY].version == consents.PRIVACY_VERSION
    assert "projekt" in BY_KIND[ConsentKind.GUARDIAN].version


# --- reguła niepełnoletności ------------------------------------------------------------------


def test_person_born_eighteen_years_ago_still_needs_the_guardian_consent():
    """Rocznik „minus 18” może mieć jeszcze 17 lat – zachowawczo traktujemy go jak małoletni."""
    assert consents.is_minor(minor_year()) is True
    assert ConsentKind.GUARDIAN in consents.required_kinds(minor_year())


def test_person_born_nineteen_years_ago_does_not_need_it():
    assert consents.is_minor(adult_year()) is False
    assert ConsentKind.GUARDIAN not in consents.required_kinds(adult_year())


def test_missing_birth_year_is_treated_as_a_minor():
    """Brak rocznika nie może być furtką: nie zgadujemy na korzyść pominięcia zgody."""
    assert consents.is_minor(None) is True


def test_minor_rule_is_computed_against_the_given_day():
    assert consents.is_minor(2008, today=date(2026, 1, 1)) is True
    assert consents.is_minor(2007, today=date(2026, 1, 1)) is False


# --- walidacja w serwisie ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("missing", "fragment"),
    [
        ("terms_consent", "Regulaminu"),
        ("gdpr_consent", "danych osobowych"),
    ],
)
def test_registration_without_a_required_consent_returns_consent_required(
    api, open_registration, missing, fragment
):
    resp = api.post(REGISTER_URL, payload(**{missing: False}), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "CONSENT_REQUIRED"
    assert fragment in resp.json()["detail"]
    # Nic nie powstało: sprawdzenie zgód stoi przed utworzeniem konta.
    assert not User.objects.filter(email="zgody@example.test").exists()
    assert not Participant.objects.exists()
    assert not ConsentRecord.objects.exists()


def test_minor_without_the_guardian_consent_is_refused(api, open_registration):
    resp = api.post(REGISTER_URL, payload(birth_year=minor_year()), format="json")

    assert resp.status_code == 400
    assert resp.json()["code"] == "CONSENT_REQUIRED"
    assert "opiekuna prawnego" in resp.json()["detail"]
    assert not Participant.objects.exists()


def test_adult_without_the_guardian_consent_registers(api, open_registration):
    resp = api.post(REGISTER_URL, payload(birth_year=adult_year()), format="json")

    assert resp.status_code == 201, resp.data
    assert Participant.objects.get().guardian_consent is False


def test_validate_consents_is_the_single_rule(db):
    """Ta sama reguła obowiązuje wszystkie drogi rejestracji – dlatego mieszka w serwisie."""
    given = {ConsentKind.TERMS: True, ConsentKind.PRIVACY: True}

    validate_consents(given, birth_year=adult_year())  # nie podnosi

    with pytest.raises(Exception) as exc:
        validate_consents(given, birth_year=minor_year())
    assert exc.value.machine_code == "CONSENT_REQUIRED"


# --- dowody -----------------------------------------------------------------------------------


def test_registration_writes_one_record_per_granted_consent(api, open_registration):
    resp = api.post(
        REGISTER_URL,
        payload(birth_year=minor_year(), guardian_consent=True, publish_name_consent=True),
        format="json",
    )

    assert resp.status_code == 201, resp.data
    participant = Participant.objects.get()
    records = {record.kind: record for record in participant.consents.all()}
    assert set(records) == {
        ConsentKind.TERMS,
        ConsentKind.PRIVACY,
        ConsentKind.GUARDIAN,
        ConsentKind.PUBLISH_NAME,
    }
    assert records[ConsentKind.TERMS].document_version == consents.TERMS_VERSION
    assert records[ConsentKind.PRIVACY].document_version == consents.PRIVACY_VERSION
    assert all(record.source == ConsentSource.API for record in records.values())
    assert all(record.withdrawn_at is None for record in records.values())


def test_consent_not_given_leaves_no_record(api, open_registration):
    """Brak dowodu jest poprawnym stanem – wiersz „nie zgodził się” niczego by nie dowodził."""
    api.post(REGISTER_URL, payload(birth_year=adult_year()), format="json")

    kinds = set(Participant.objects.get().consents.values_list("kind", flat=True))
    assert kinds == {ConsentKind.TERMS, ConsentKind.PRIVACY}


def test_registration_projects_consents_onto_the_profile(api, open_registration):
    api.post(
        REGISTER_URL,
        payload(birth_year=minor_year(), guardian_consent=True, publish_name_consent=True),
        format="json",
    )

    participant = Participant.objects.get()
    assert participant.terms_accepted_at is not None
    assert participant.gdpr_consent_at is not None
    assert participant.guardian_consent is True
    assert participant.publish_full_name is True


def test_registration_leaves_exactly_one_audit_entry_for_the_consents(api, open_registration):
    """Zdarzeniem jest wypełnienie formularza, nie każdy checkbox z osobna."""
    api.post(REGISTER_URL, payload(birth_year=adult_year()), format="json")

    entries = AuditLog.objects.filter(action="participant.consents_recorded")
    assert entries.count() == 1
    diff = entries.get().diff
    assert diff["source"] == ConsentSource.API
    assert diff[ConsentKind.TERMS] == {"given": True, "version": consents.TERMS_VERSION}
    assert diff[ConsentKind.GUARDIAN]["given"] is False


def test_record_consents_can_be_called_for_an_existing_profile(db):
    participant = ParticipantFactory(birth_year=adult_year(), publish_full_name=False)

    record_consents(
        participant,
        {ConsentKind.TERMS: True, ConsentKind.PRIVACY: True, ConsentKind.PUBLISH_NAME: True},
        source=ConsentSource.WEB,
    )

    participant.refresh_from_db()
    assert participant.publish_full_name is True
    assert participant.consents.count() == 3


# --- zgoda odwracalna -------------------------------------------------------------------------


def test_publish_name_consent_can_be_withdrawn_and_given_again(db):
    participant = ParticipantFactory(birth_year=adult_year(), publish_full_name=False)

    set_publish_name_consent(participant, given=True)
    set_publish_name_consent(participant, given=False)
    set_publish_name_consent(participant, given=True)

    participant.refresh_from_db()
    assert participant.publish_full_name is True
    records = list(participant.consents.filter(kind=ConsentKind.PUBLISH_NAME))
    # Historia zostaje: pierwsza zgoda wycofana, druga obowiązuje.
    assert len(records) == 2
    assert sum(1 for record in records if record.withdrawn_at is not None) == 1
    assert sum(1 for record in records if record.withdrawn_at is None) == 1


def test_publish_name_consent_given_twice_does_not_duplicate_the_record(db):
    participant = ParticipantFactory(birth_year=adult_year(), publish_full_name=False)

    set_publish_name_consent(participant, given=True)
    set_publish_name_consent(participant, given=True)

    assert participant.consents.filter(kind=ConsentKind.PUBLISH_NAME).count() == 1


# --- API --------------------------------------------------------------------------------------


def test_consent_set_endpoint_is_public_and_describes_every_consent(api, db):
    resp = api.get(CONSENTS_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert [row["kind"] for row in body] == [consent.kind for consent in CONSENTS]
    terms = body[0]
    assert terms["required"] is True
    assert terms["version"] == consents.TERMS_VERSION
    assert terms["document_url"] == "/dokumenty/regulamin/"
    assert "/dokumenty/regulamin/" in terms["label"]
    # Czysty tekst jest dla klientów, które budują własny interfejs – bez znaczników.
    assert "<a " not in terms["text"]
    guardian = next(row for row in body if row["kind"] == ConsentKind.GUARDIAN)
    assert guardian["required"] is False
    assert guardian["required_for_minor"] is True


def test_profile_exposes_the_consent_history(api, open_registration):
    api.post(REGISTER_URL, payload(birth_year=adult_year()), format="json")
    # Rejestracja zostawia konto nieaktywne; przedmiotem tego testu jest historia zgód w profilu,
    # więc aktywację przechodzimy helperem, a nie wyłączamy reguły.
    activate(User.objects.get(email="zgody@example.test"))
    api.post("/api/auth/login/", {"email": "zgody@example.test", "password": PASSWORD}, format="json")

    body = api.get("/api/auth/me/").json()

    rows = body["participant"]["consents"]
    assert {row["kind"] for row in rows} == {ConsentKind.TERMS, ConsentKind.PRIVACY}
    assert all(row["withdrawn_at"] is None for row in rows)
    assert body["participant"]["terms_accepted_at"] is not None
