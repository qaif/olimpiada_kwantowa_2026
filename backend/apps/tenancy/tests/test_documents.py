"""Szablony tekstu dokumentów: odwrót na stałe, podstawienia, wersjonowanie i izolacja (§ 1.1.3).

Dwie rzeczy są tu ważniejsze od reszty i to one wyznaczają kolejność testów.

Po pierwsze **Konkurs #1 nie zmienia się ani o znak**: dopóki flaga ``document_templates`` jest
wyłączona, dokument składa się z dzisiejszych stałych ``apps/results/certificates.py`` i nie pada
ani jedno zapytanie do tabeli szablonów (§ 5.6 – budżety zapytań są bramką, nie zaleceniem).

Po drugie **wersja jest wskaźnikiem**: dokument wystawiony w zeszłym roku ma wyjść z drukarki tak
samo, jak wtedy, także wtedy, gdy tekst szablonu już poprawiono. Bez tego cała tabela byłaby
wygodniejszym sposobem na cichą zmianę treści dokumentów, które ktoś trzyma w ręku.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from apps.core.models import AuditLog
from apps.results.certificates import DOCUMENT_STATEMENTS, DOCUMENT_TITLES, SIGNATURE_LINE
from apps.results.models import CertificateKind
from apps.tenancy.documents import (
    ALLOWED_PLACEHOLDERS,
    INITIAL_VERSION,
    DocumentKind,
    DocumentTemplate,
    current_template,
    current_version,
    render_document,
    set_current_template,
    substitute,
    templates_of,
    uses_document_templates,
)

pytestmark = pytest.mark.django_db

#: Odwrót, który w produkcji podaje ``apps.results.certificates.document_fallback``. Powtórzony
#: tutaj **z nazwami pól**, a nie zaimportowany razem z funkcją, bo test ma pokazywać kształt
#: umowy między modułami: wołający podaje literały, moduł rozstrzyga, który napis wyjdzie.
FALLBACK = {
    "title": "Dyplom laureata",
    "statement": "uzyskał(a) tytuł laureata Olimpiady Kwantowej",
    "signature_line": SIGNATURE_LINE,
    "author": "Olimpiada Kwantowa",
}


def enable_templates(competition):
    """Włącza flagę ``document_templates`` – jedyne wejście do niej w tym pliku."""
    competition.feature_flags = {**(competition.feature_flags or {}), "document_templates": True}
    competition.save(update_fields=["feature_flags"])
    return competition


def write_template(competition, **values):
    """Szablon wprost w bazie – dla testów, które sprawdzają odczyt, a nie serwis zapisu.

    Poprzednio obowiązujący wiersz tego rodzaju (ten z migracji) traci ``is_current`` – inaczej
    więz „jeden obowiązujący na rodzaj” odrzuciłby zapis, i słusznie.
    """
    defaults = {
        "kind": DocumentKind.LAUREAT,
        "version": "2.0",
        "title": "Dyplom laureata {competition_genitive}",
        "statement": "{recipient} uzyskał(a) tytuł laureata w edycji {edition}",
        "signature_line": "Przewodniczący komitetu",
        "footer_note": "",
        "is_current": True,
    }
    merged = {**defaults, **values}
    if merged["is_current"]:
        DocumentTemplate.objects.filter(competition=competition, kind=str(merged["kind"])).update(
            is_current=False
        )
    return DocumentTemplate.objects.create(competition=competition, **merged)


# --- 1. zgodność z rejestrem dyplomów ------------------------------------------------------------


def test_document_kinds_cover_certificate_kinds():
    """Każdy rodzaj dokumentu z rejestru ma gdzie mieć tekst.

    Obie listy są kopiami, a nie importem (patrz ``DocumentKind``), więc dopisanie rodzaju dyplomu
    bez dopisania rodzaju szablonu znaczyłoby dokument, którego treści nie da się skonfigurować –
    i nikt by się o tym nie dowiedział, bo dokument wychodziłby ze stałej.
    """
    assert set(CertificateKind.values) <= set(DocumentKind.values)


# --- 2. migracja: dzisiejsze napisy jako wiersze --------------------------------------------------


def test_document_templates_match_the_constants(competition):
    """Wiersze Konkursu #1 są **kopią** stałych składu – co do znaku i co do wielkości liter.

    To jest ten test, o którym mówi § 4.4 T12. Gdyby migracja wpisała choćby inny nawias, dzień
    włączenia flagi byłby dniem, w którym dyplomy Olimpiady Kwantowej zaczynają mówić coś innego
    niż wczoraj – i nie byłoby tego widać w żadnym przeglądzie kodu.
    """
    rows = {row.kind: row for row in templates_of(competition)}

    assert set(rows) == {str(kind) for kind in DOCUMENT_TITLES}
    for kind, title in DOCUMENT_TITLES.items():
        row = rows[str(kind)]
        assert row.title == title
        assert row.statement == DOCUMENT_STATEMENTS[kind]
        assert row.signature_line == SIGNATURE_LINE
        assert row.footer_note == ""
        assert row.version == INITIAL_VERSION
        assert row.is_current is True


def test_second_competition_has_no_templates_from_the_migration(other_competition):
    """Konkurs założony obok nie dostaje cudzych zdań.

    Zdania Konkursu #1 mówią „Olimpiady Kwantowej” w dopełniaczu; wpisanie ich każdemu konkursowi
    w bazie byłoby wpisaniem cudzej marki do konfiguracji drugiego organizatora.
    """
    assert not templates_of(other_competition).exists()


# --- 3. odwrót na stałe: Konkurs #1 bez zmiany ----------------------------------------------------


def test_flag_off_returns_the_fallback_without_a_single_query(competition, django_assert_num_queries):
    """Bez flagi – dzisiejsze napisy i **zero** zapytań, choć wiersze w bazie już są."""
    assert uses_document_templates(competition) is False
    with django_assert_num_queries(0):
        document = render_document(competition, CertificateKind.LAUREAT, fallback=FALLBACK)

    assert document.title == FALLBACK["title"]
    assert document.statement == FALLBACK["statement"]
    assert document.signature_line == SIGNATURE_LINE
    assert document.author == "Olimpiada Kwantowa"
    # Pusta wersja znaczy „układ wbudowany” – i to ona trafia do ``Certificate.template_version``.
    assert document.version == ""


def test_no_competition_means_today(django_assert_num_queries):
    """Brak konkursu to nie „flaga wyłączona”, tylko „nie wiadomo, czyj to dokument” – ten sam wynik."""
    with django_assert_num_queries(0):
        document = render_document(None, CertificateKind.LAUREAT, fallback=FALLBACK)

    assert document.title == FALLBACK["title"]
    assert current_template(None, CertificateKind.LAUREAT) is None
    assert current_version(None, CertificateKind.LAUREAT) == ""


def test_flag_off_ignores_a_template_that_already_exists(competition):
    """Wiersz w bazie przy wyłączonej fladze jest niewidoczny – przełącznik jest jeden."""
    write_template(competition, kind=DocumentKind.LAUREAT, version="3.0", title="Coś zupełnie innego")

    document = render_document(competition, CertificateKind.LAUREAT, fallback=FALLBACK)

    assert document.title == FALLBACK["title"]


# --- 4. szablon z podstawieniami ------------------------------------------------------------------


def test_flag_on_renders_the_template_with_substitutions(competition):
    """Z flagą – tekst z bazy, a znaczniki podstawione danymi dokumentu."""
    enable_templates(competition)
    write_template(competition, version="2.0")

    document = render_document(
        competition,
        CertificateKind.LAUREAT,
        fallback=FALLBACK,
        recipient="Łucja Śniadecka",
        edition="2026/2027",
    )

    assert document.version == "2.0"
    assert document.title == f"Dyplom laureata {competition.genitive}"
    assert document.statement == "Łucja Śniadecka uzyskał(a) tytuł laureata w edycji 2026/2027"
    assert document.signature_line == "Przewodniczący komitetu"
    # Autor pliku jest marką konkursu, a nie kolumną szablonu – tak samo jak podpis pod listem.
    assert document.author == (competition.short_name or competition.name)


def test_kind_without_a_template_falls_back(competition):
    """Rodzaj, którego organizator nie skonfigurował, składa się ze stałych – a nie z pustki."""
    enable_templates(competition)
    templates_of(competition).filter(kind=str(CertificateKind.FINALISTA)).delete()

    document = render_document(competition, CertificateKind.FINALISTA, fallback=FALLBACK)

    assert document.title == FALLBACK["title"]
    assert document.version == ""


def test_allowed_placeholder_without_a_value_renders_empty(competition):
    """Dyplom opiekuna nie ma kodu uczestnika – i ma wyjść z pustym miejscem, a nie z klamrą."""
    enable_templates(competition)
    write_template(
        competition, kind=DocumentKind.OPIEKUN, version="2.0", statement="kod: {participant_code}."
    )

    document = render_document(competition, DocumentKind.OPIEKUN, fallback=FALLBACK)

    assert document.statement == "kod: ."


def test_unknown_placeholder_stays_visible_instead_of_raising():
    """Znacznik spoza listy widać na papierze – dokument wychodzi, a usterka jest do zauważenia.

    Do bazy taki wiersz może trafić wyłącznie obok panelu (fikstura, import, ręczny ``UPDATE``),
    bo ``clean()`` go nie przepuszcza. Wtedy lepszy jest dokument z widoczną usterką niż błąd 500
    u uczestnika, który akurat pobiera swój dyplom.
    """
    assert substitute("kod: {partcipant_code}") == "kod: {partcipant_code}"


def test_broken_pattern_returns_the_raw_text():
    """Niedomknięta klamra nie ma jak się podstawić – i nie ma prawa wywrócić składu."""
    assert substitute("{competition", None) == "{competition"


# --- 5. walidacja znaczników ----------------------------------------------------------------------


def test_clean_rejects_an_unknown_placeholder(competition):
    """Literówka ma być zauważona w panelu, a nie w PDF-ie wydanym tysiącu osób."""
    template = DocumentTemplate(
        competition=competition,
        kind=DocumentKind.LAUREAT,
        version="9.0",
        title="Dyplom",
        statement="kod: {partcipant_code}",
    )

    with pytest.raises(ValidationError) as error:
        template.clean()

    message = error.value.message_dict["statement"][0]
    assert "{partcipant_code}" in message
    # Komunikat wylicza dozwolone – inaczej autor tekstu musiałby zgadywać, co wolno wpisać.
    assert "{participant_code}" in message


@pytest.mark.parametrize("placeholder", sorted(ALLOWED_PLACEHOLDERS))
def test_clean_accepts_every_allowed_placeholder(competition, placeholder):
    """Lista dozwolonych jest listą **działającą**, a nie opisem w dokumentacji."""
    template = DocumentTemplate(
        competition=competition,
        kind=DocumentKind.LAUREAT,
        version="9.0",
        title="Dyplom",
        statement=f"treść {{{placeholder}}}",
    )

    template.clean()


def test_the_invoice_may_use_its_own_placeholders(competition):
    """Kwota jest pojęciem należności, więc wolno ją wpisać na fakturze – i tylko tam (§ 1.5.1)."""
    invoice = DocumentTemplate(
        competition=competition,
        kind=DocumentKind.INVOICE,
        version="1.0",
        title="Rachunek",
        statement="do zapłaty {amount} {currency} do {due_date}",
    )
    invoice.clean()

    diploma = DocumentTemplate(
        competition=competition,
        kind=DocumentKind.LAUREAT,
        version="9.0",
        title="Dyplom",
        statement="do zapłaty {amount}",
    )
    with pytest.raises(ValidationError):
        diploma.clean()


def test_an_invoice_placeholder_without_a_value_renders_empty(competition):
    """Znacznik własny rodzaju bez wartości zachowuje się jak wspólny: puste miejsce, nie klamra."""
    enable_templates(competition)
    write_template(
        competition,
        kind=DocumentKind.INVOICE,
        version="1.0",
        title="Rachunek",
        statement="do zapłaty {amount} {currency}",
    )

    document = render_document(competition, DocumentKind.INVOICE, fallback=FALLBACK, amount="120,00")

    assert document.statement == "do zapłaty 120,00 "
    assert document.version == "1.0"


def test_clean_reports_unbalanced_braces(competition):
    """Zepsuta składnia dostaje własny komunikat, a nie „nieznany znacznik: None”."""
    template = DocumentTemplate(
        competition=competition,
        kind=DocumentKind.LAUREAT,
        version="9.0",
        title="Dyplom {competition",
        statement="treść",
    )

    with pytest.raises(ValidationError) as error:
        template.clean()

    assert "title" in error.value.message_dict


# --- 6. zmiana wersji -----------------------------------------------------------------------------


def test_set_current_template_keeps_the_previous_version_and_audits(competition):
    """Nowa wersja obowiązuje, stara **zostaje** jako historia, a zmiana ma ślad w audycie."""
    enable_templates(competition)
    previous = templates_of(competition).get(kind=str(DocumentKind.LAUREAT))

    template = set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="2.0",
        title="Dyplom laureata",
        statement="uzyskał(a) tytuł laureata {competition_genitive}",
        signature_line=SIGNATURE_LINE,
    )

    previous.refresh_from_db()
    assert previous.is_current is False
    assert template.is_current is True
    assert current_version(competition, DocumentKind.LAUREAT) == "2.0"
    # Dwa wiersze, nie jeden: dokument wystawiony wczoraj ma z czego powstać.
    assert templates_of(competition).filter(kind=str(DocumentKind.LAUREAT)).count() == 2

    entry = AuditLog.objects.filter(action="document_template.changed").latest("at")
    assert entry.diff["version"] == "2.0"
    assert entry.diff["previous_version"] == INITIAL_VERSION


def test_set_current_template_rejects_a_repeated_version(competition):
    """Ta sama wersja dwa razy uczyniłaby ``Certificate.template_version`` wskaźnikiem dwuznacznym."""
    enable_templates(competition)

    with pytest.raises(ValidationError) as error:
        set_current_template(
            competition,
            DocumentKind.LAUREAT,
            version=INITIAL_VERSION,
            title="Dyplom laureata",
            statement="cokolwiek",
        )

    assert "version" in error.value.message_dict


def test_set_current_template_validates_placeholders(competition):
    """Serwis zapisu nie przepuszcza tego, czego nie przepuszcza model – walidacja jest jedna."""
    enable_templates(competition)

    with pytest.raises(ValidationError):
        set_current_template(
            competition,
            DocumentKind.LAUREAT,
            version="2.0",
            title="Dyplom",
            statement="kod: {partcipant_code}",
        )


def test_the_pinned_version_wins_over_the_current_one(competition):
    """Dokument wystawiony kiedyś składa się z **tamtego** tekstu, a nie z dzisiejszego."""
    enable_templates(competition)
    set_current_template(
        competition,
        DocumentKind.LAUREAT,
        version="2.0",
        title="Tytuł z wersji 2.0",
        statement="zdanie z wersji 2.0",
    )

    document = render_document(
        competition, CertificateKind.LAUREAT, version=INITIAL_VERSION, fallback=FALLBACK
    )

    assert document.version == INITIAL_VERSION
    assert document.title == DOCUMENT_TITLES[CertificateKind.LAUREAT]


def test_a_version_that_is_gone_falls_back_to_the_current_one(competition):
    """Wersja, której nie ma, nie wstrzymuje wydania dokumentu – ma wyjść mimo wszystko."""
    enable_templates(competition)

    document = render_document(
        competition, CertificateKind.LAUREAT, version="nie ma takiej", fallback=FALLBACK
    )

    assert document.version == INITIAL_VERSION


# --- 7. izolacja ----------------------------------------------------------------------------------


def test_templates_of_one_competition_are_invisible_to_the_other(competition, other_competition):
    """Reguła etapu 1 § 7.2 na nowym modelu: cudzy tekst nie jest widoczny nawet po ``pk``."""
    mine = templates_of(competition).first()

    assert not templates_of(other_competition).filter(pk=mine.pk).exists()
    assert not templates_of(other_competition).filter(competition=competition).exists()
    # ``None`` nie widzi niczego – domyślnie zamknięte, a nie „wszystko”.
    assert not templates_of(None).exists()


def test_templates_do_not_block_deleting_their_competition():
    """``CASCADE``, nie ``PROTECT``: szablon jest konfiguracją konkursu, a nie dowodem.

    Odstępstwo od § 1.1.3 opisane przy polu i wymuszone przez kreator ``/setup/`` oraz sprzątanie
    instalacji testowej: wiersz, który przeżywa własnego właściciela, nie jest niczyją
    konfiguracją, tylko wierszem blokującym kasowanie. Dowód zostaje osobno i nie kaskaduje –
    ``results.Certificate.template_version`` jest kopią napisu, więc dokument wystawiony kiedyś
    niesie swoją wersję także wtedy, gdy szablonu już nie ma.

    Sprawdzamy **deklarację**, a nie przebieg kasowania: kolektor ``Competition.delete()``
    zagląda do tabeli każdego modelu wskazującego konkurs w całej instalacji, więc test kasujący
    konkurs mierzyłby stan wszystkich pozostałych aplikacji, a nie zachowanie tego pola.
    """
    from django.db import models as django_models

    field = DocumentTemplate._meta.get_field("competition")

    assert field.remote_field.on_delete is django_models.CASCADE


def test_the_flag_of_one_competition_does_not_open_the_other(competition, other_competition):
    """Flaga jest własnością konkursu, a nie instalacji."""
    enable_templates(other_competition)

    assert uses_document_templates(competition) is False
    assert current_template(competition, CertificateKind.LAUREAT) is None
