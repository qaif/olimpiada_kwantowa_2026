"""Zestaw zgód zbieranych przy rejestracji uczestnika – jedno źródło prawdy dla całej platformy.

Zgoda przestaje być gołym „checkboxem z etykietą”, a staje się **oświadczeniem o znanej treści,
złożonym pod konkretną wersją konkretnego dokumentu**. Dlatego wszystko, co ją opisuje, stoi
w jednym module:

- **rodzaj** (``ConsentKind``) – klucz, po którym poznaje ją model dowodowy ``ConsentRecord``,
  serwis, formularz, serializer i panel uczestnika,
- **treść oświadczenia** – dokładnie ten napis, który uczestnik widzi przy polu wyboru. Napisany
  raz: formularz WWW, rejestracja przez dostawcę zewnętrznego i ``GET /api/auth/consents/``
  pokazują ten sam tekst, więc klient zewnętrzny nie może zebrać zgody o innym brzmieniu,
- **dokument** (slug strony w ``/dokumenty/``) – etykieta linkuje do dokumentu, którego dotyczy.
  Zgoda bez odnośnika do treści, na którą się zgadzamy, nie jest zgodą świadomą,
- **wersja dokumentu** – trafia do ``ConsentRecord.document_version``, czyli do dowodu. Bez niej
  po pierwszej nowelizacji regulaminu nie da się odpowiedzieć na pytanie „na co ta osoba się
  zgodziła”, a to jest jedyne pytanie, które przy zgodzie naprawdę pada,
- **reguła wymagalności** – dwie wartości, bo są dwa rodzaje „wymagane”: zawsze (regulamin,
  RODO) i wyłącznie dla osób niepełnoletnich (zgoda opiekuna).

**Wersje dokumentów są literałami**, a nie odczytem z bazy, i to jest celowe: numer wersji ma
zmieniać się razem z wgraniem nowego dokumentu, czyli razem ze zmianą w repozytorium, a nie
cichaczem po edycji strony w ``/cms/``. Wpis dowodowy ma mówić, co obowiązywało w chwili zgody –
gdyby wersja przychodziła z bazy, redaktor mógłby przepisać historię jednym zapisem strony.

Adres dokumentu jest natomiast liczony **przy renderowaniu** z drzewa stron (po slugu), z awaryjnym
``/dokumenty/<slug>/``: dokument może być przeniesiony w drzewie, a rejestracja nie ma prawa
przestać działać dlatego, że seed treści jeszcze nie przeszedł na tym środowisku.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import models
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

#: Nazwa organizatora używana, gdy nie da się odczytać ``cms.SiteSettings`` (świeża baza, komenda
#: zarządzająca bez witryny Wagtaila). Ta sama wartość, co domyślna w ``SiteSettings``.
DEFAULT_ORGANIZER_NAME = "Fundacja Quantum AI"

#: Sekcja dokumentów w drzewie stron. Awaryjny adres etykiety, gdy strony jeszcze nie ma w bazie.
DOCUMENTS_PATH = "/dokumenty/"

#: Wiek, powyżej którego zgoda opiekuna przestaje być wymagana – liczony **wyłącznie po roczniku**,
#: bo daty dziennej urodzenia nie zbieramy (zasada minimalizacji). Osoba, która w tym roku kończy
#: 18 lat, przez większość roku jest jeszcze małoletnia, więc rocznik „minus 18” traktujemy jako
#: niepełnoletni. Zawyżenie kosztuje jeden zbędny checkbox, zaniżenie – zgodę pobraną od dziecka
#: bez wiedzy opiekuna; te dwa błędy nie są równoważne.
MINOR_MAX_AGE = 18


class ConsentKind(models.TextChoices):
    """Rodzaje zgód. Wartość jest kluczem w bazie, etykieta – nazwą dla człowieka."""

    TERMS = "TERMS", "akceptacja regulaminu"
    PRIVACY = "PRIVACY", "przetwarzanie danych osobowych"
    GUARDIAN = "GUARDIAN", "zgoda rodzica lub opiekuna prawnego"
    PUBLISH_NAME = "PUBLISH_NAME", "publikacja imienia i nazwiska"


class ConsentSource(models.TextChoices):
    """Droga, którą zgoda wpłynęła. Część dowodu: „gdzie i jak” bywa pytaniem po latach."""

    WEB = "web", "formularz WWW"
    API = "api", "API"
    SOCIAL = "social", "logowanie przez dostawcę zewnętrznego"
    PANEL = "panel", "panel uczestnika"


@dataclass(frozen=True)
class Consent:
    """Jedna zgoda: treść oświadczenia, dokument, wersja i reguła wymagalności.

    ``text`` jest szablonem z nazwanymi miejscami ``{link}`` (odnośnik do dokumentu) i
    ``{organizer}`` (nazwa organizatora z ``cms.SiteSettings``). Składa go ``label`` przez
    ``format_html``, więc do HTML-a trafia wyłącznie ten odnośnik – reszta jest escapowana.

    ``field_name`` to nazwa pola w formularzu i w serializerze rejestracji. Trzymamy ją tutaj,
    bo inaczej mapowanie „pole → rodzaj zgody” istniałoby osobno w formularzu, osobno
    w serializerze i osobno w serwisie, a rozjazd któregokolwiek z nich znaczyłby zgodę zapisaną
    pod niewłaściwym rodzajem.
    """

    kind: str
    field_name: str
    text: str
    version: str
    missing_message: str = ""
    link_text: str = ""
    document_slug: str = ""
    required: bool = False
    required_for_minor: bool = False
    help_text: str = ""

    @property
    def is_optional(self) -> bool:
        return not (self.required or self.required_for_minor)


#: Wersja regulaminu. Numer i data pochodzą z tabeli metryki w ``apps/cms/fixtures/regulamin/
#: regulamin-mammoth.html`` (wiersze „Wersja” i „Data dokumentu”), którą wgrywa ``seed_regulamin``.
#: Zmiana dokumentu = zmiana tej stałej: od tego momentu nowe zgody są zapisywane pod nową wersją,
#: a stare wpisy dalej mówią prawdę o tym, co obowiązywało wtedy.
TERMS_VERSION = "1.0 z 2 września 2026"

#: Wersja polityki RODO – z ostatniej sekcji ``apps/cms/fixtures/legacy/rodo.md``
#: („Wersja: 1.0 z 22 lipca 2026 r.”).
PRIVACY_VERSION = "1.0 z 22 lipca 2026"

#: Wzór zgody opiekuna jest **projektem** (``apps/cms/fixtures/legacy/zgoda-opiekuna.md``) –
#: czeka na akceptację organizatora i prawnika, stąd numer 0.x. Wpisy dowodowe zebrane pod tym
#: numerem będą więc odróżnialne od zebranych pod wersją zatwierdzoną.
GUARDIAN_VERSION = "0.1 (projekt) z 10 września 2026"

#: Zgoda na publikację nazwiska nie ma osobnego dokumentu: jej zakres opisuje § 15 Regulaminu
#: i polityka RODO. Wersję numerujemy własną, bo to ona identyfikuje brzmienie oświadczenia.
PUBLISH_NAME_VERSION = "1.0"

CONSENTS: tuple[Consent, ...] = (
    Consent(
        kind=ConsentKind.TERMS,
        field_name="terms_consent",
        text="Zapoznałem/-am się z {link} i akceptuję jego postanowienia.",
        link_text="Regulaminem Olimpiady Kwantowej",
        document_slug="regulamin",
        version=TERMS_VERSION,
        required=True,
        missing_message="Akceptacja Regulaminu Olimpiady Kwantowej jest wymagana.",
    ),
    Consent(
        kind=ConsentKind.PRIVACY,
        field_name="gdpr_consent",
        # Nazwa organizatora stoi w apozycji („przez organizatora – Fundacja Quantum AI – w celu”),
        # a nie wprost po przyimku „przez”. Powód jest gramatyczny: ``SiteSettings.organizer_name``
        # trzyma nazwę w mianowniku, a „przez” rządzi biernikiem – wstawiona wprost dałaby
        # „przez Fundacja Quantum AI”. Odmieniać cudzej nazwy własnej w kodzie nie będziemy
        # (regułka „-a → -ę” przewraca się na pierwszym „Instytut” i na każdym skrótowcu).
        text=(
            "Wyrażam zgodę na przetwarzanie moich danych osobowych przez organizatora – "
            "{organizer} – w celu organizacji i przeprowadzenia Olimpiady Kwantowej oraz "
            "oświadczam, że zapoznałem/-am się z {link}."
        ),
        link_text="Polityką RODO (klauzulą informacyjną)",
        document_slug="rodo",
        version=PRIVACY_VERSION,
        required=True,
        # Brzmienie sprzed wprowadzenia zestawu zgód – ten komunikat widzi uczestnik i zna go API.
        missing_message="Zgoda na przetwarzanie danych osobowych jest wymagana.",
    ),
    Consent(
        kind=ConsentKind.GUARDIAN,
        field_name="guardian_consent",
        text=(
            "Oświadczam, że mój rodzic / opiekun prawny zapoznał się z {link} i wyraża zgodę "
            "na mój udział w Olimpiadzie oraz na przetwarzanie moich danych osobowych."
        ),
        link_text="Zgodą rodzica lub opiekuna prawnego",
        document_slug="zgoda-opiekuna",
        version=GUARDIAN_VERSION,
        required_for_minor=True,
        help_text="wymagane dla osób niepełnoletnich",
        missing_message=(
            "Zgoda rodzica lub opiekuna prawnego jest wymagana dla uczestnika niepełnoletniego."
        ),
    ),
    Consent(
        kind=ConsentKind.PUBLISH_NAME,
        field_name="publish_name_consent",
        text=(
            "Wyrażam zgodę na publikację mojego imienia i nazwiska (wraz ze szkołą) na listach "
            "wyników i laureatów."
        ),
        version=PUBLISH_NAME_VERSION,
        help_text="dobrowolne – bez tej zgody w tabelach wyników zostaje sam kod uczestnika",
    ),
)

BY_KIND: dict[str, Consent] = {consent.kind: consent for consent in CONSENTS}
BY_FIELD: dict[str, Consent] = {consent.field_name: consent for consent in CONSENTS}

#: Nazwy pól zgód w kolejności, w jakiej mają stać w formularzu i w API.
CONSENT_FIELD_NAMES: tuple[str, ...] = tuple(consent.field_name for consent in CONSENTS)


def is_minor(birth_year: int | None, *, today: date | None = None) -> bool:
    """Czy rocznik oznacza osobę, od której wymagamy zgody opiekuna.

    Reguła jest zachowawcza z premedytacją: ``rok bieżący - rocznik <= 18``. Osoba urodzona
    osiemnaście lat temu może mieć jeszcze 17 lat (urodziny dopiero przed nią), a daty dziennej
    nie znamy. Brak rocznika też jest traktowany jak niepełnoletność – nie zgadujemy na korzyść
    pominięcia zgody.
    """
    if not birth_year:
        return True
    current_year = (today or timezone.localdate()).year
    return current_year - int(birth_year) <= MINOR_MAX_AGE


def required_kinds(birth_year: int | None, *, today: date | None = None) -> tuple[str, ...]:
    """Rodzaje zgód wymaganych od uczestnika o tym roczniku."""
    minor = is_minor(birth_year, today=today)
    return tuple(
        consent.kind for consent in CONSENTS if consent.required or (consent.required_for_minor and minor)
    )


def organizer_name() -> str:
    """Nazwa organizatora z ``cms.SiteSettings`` – czytana przy renderowaniu, nie przy imporcie.

    Zmiana nazwy fundacji w ``/cms/`` ma od razu zmieniać treść zgody, bez wydania aplikacji.
    Wyjątek jest szeroki świadomie: brak witryny Wagtaila albo brak tabeli (pierwsza migracja)
    nie może wywrócić formularza rejestracji – zostaje wtedy wartość domyślna, ta sama, którą
    ``SiteSettings`` ma w definicji pola.
    """
    try:
        from apps.cms.models import SiteSettings

        settings_row = SiteSettings.objects.first()
    except Exception:  # noqa: BLE001 - brak tabeli/witryny nie może zablokować rejestracji
        return DEFAULT_ORGANIZER_NAME
    if settings_row is None:
        return DEFAULT_ORGANIZER_NAME
    return settings_row.organizer_name or DEFAULT_ORGANIZER_NAME


def document_url(slug: str) -> str:
    """Adres strony dokumentu o tym slugu. Pusty slug = zgoda bez dokumentu.

    Adres bierzemy z drzewa stron, bo dokument wolno przenieść w ``/cms/``, a etykieta zgody nie
    może wtedy prowadzić donikąd. Gdy strony nie ma (świeże środowisko przed ``seed_legacy_content``),
    zostaje adres kanoniczny ``/dokumenty/<slug>/``: link „na wyrost” jest lepszy niż zgoda bez
    odnośnika, a po zaseedowaniu treści prowadzi dokładnie tam, gdzie ma prowadzić.
    """
    if not slug:
        return ""
    fallback = f"{DOCUMENTS_PATH}{slug}/"
    try:
        from apps.cms.models import DocumentPage

        page = DocumentPage.objects.live().filter(slug=slug).first()
    except Exception:  # noqa: BLE001 - brak tabeli stron nie może zablokować rejestracji
        return fallback
    if page is None:
        return fallback
    return page.get_url() or fallback


def label(consent: Consent, *, organizer: str | None = None) -> SafeString:
    """Treść oświadczenia jako HTML: tekst z odnośnikiem do dokumentu.

    Odnośnik otwiera się w nowej karcie (``target="_blank"``) z ``rel="noopener"``: przeczytanie
    regulaminu nie może kosztować utraty wypełnionego formularza rejestracji.
    """
    url = document_url(consent.document_slug)
    link = (
        format_html('<a href="{}" target="_blank" rel="noopener">{}</a>', url, consent.link_text)
        if url and consent.link_text
        else ""
    )
    return format_html(consent.text, link=link, organizer=organizer or organizer_name())


def labels(*, organizer: str | None = None) -> dict[str, SafeString]:
    """Etykiety wszystkich zgód, po rodzaju. Jeden odczyt nazwy organizatora na formularz."""
    organizer = organizer or organizer_name()
    return {consent.kind: label(consent, organizer=organizer) for consent in CONSENTS}


def plain_text(consent: Consent, *, organizer: str | None = None) -> str:
    """Treść oświadczenia bez znaczników – dla klientów, które renderują własny interfejs."""
    return consent.text.format(link=consent.link_text, organizer=organizer or organizer_name())


def descriptions() -> list[dict]:
    """Opis zestawu zgód dla ``GET /api/auth/consents/``.

    Klient zewnętrzny dostaje komplet: brzmienie (w HTML i czystym tekstem), adres dokumentu,
    wersję i regułę wymagalności. Dzięki temu może pokazać dokładnie tę samą zgodę, którą pokazuje
    formularz WWW – a nie własną parafrazę, która nie broni się jako dowód.
    """
    organizer = organizer_name()
    return [
        {
            "kind": consent.kind,
            "field": consent.field_name,
            "label": str(label(consent, organizer=organizer)),
            "text": plain_text(consent, organizer=organizer),
            "document_slug": consent.document_slug,
            "document_url": document_url(consent.document_slug),
            "version": consent.version,
            "required": consent.required,
            "required_for_minor": consent.required_for_minor,
            "help_text": consent.help_text,
        }
        for consent in CONSENTS
    ]


def given_from_fields(values: dict) -> dict[str, bool]:
    """Mapuje kwargi/pola formularza (``terms_consent`` …) na słownik ``{rodzaj: bool}``.

    Jedno miejsce dla wszystkich trzech dróg rejestracji – formularza, API i logowania
    społecznościowego – żeby nazwa pola i rodzaj zgody nie mogły się rozjechać.
    """
    return {consent.kind: bool(values.get(consent.field_name)) for consent in CONSENTS}
