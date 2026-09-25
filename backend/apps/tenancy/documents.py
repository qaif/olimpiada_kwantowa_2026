"""Tekst dokumentu jako konfiguracja konkursu: ``DocumentTemplate`` i podstawienia (§ 1.1.3).

Skład dokumentu zostaje tam, gdzie był – w ``apps/results/certificates.py`` (ReportLab). Ten moduł
zmienia wyłącznie **źródło napisów**: tytuł na papierze, zdanie pod nazwiskiem, linię podpisu
i autora w metadanych PDF-a. Grafikę opisuje ``results.CertificateTemplate`` i te dwa modele
celowo nie są jednym: winietę wgrywa grafik, a zdanie „uzyskał tytuł laureata” pisze prawnik
organizatora – i zmieniają je w innym rytmie.

Trzy reguły, na których stoi cała reszta (wspólne z ``apps/tenancy/branding.py``, § 1.0):

- **Odwrotem jest dosłowny dzisiejszy napis**, podany przez wołającego, a nie wiersz w bazie
  podstawiony nazwą Konkursu #1. Dopóki flaga ``document_templates`` jest wyłączona, ten moduł
  **nie pyta bazy ani razu** – dokument Olimpiady Kwantowej składa się z tych samych stałych, co
  przed etapem 2, a budżety zapytań (``apps/tenancy/tests/test_invariants.py``) zostają nietknięte.
- **Brak konkursu znaczy „dzisiaj”.** Podgląd szablonu w instalacji bez konkursów i test
  jednostkowy bez świata dostają napis sprzed etapu 2, a nie wyjątek. Dlatego wejściem jest tu
  ``competition`` podany **wprost** (dokument bierze go ze swojej edycji, tak samo jak numer
  i adres weryfikacji), a nie ``require_competition()`` z § 1.0 (b): PDF powstaje w chwili
  pobrania, a „nie wiadomo, czyj to dokument” nie może zamienić pobrania dyplomu w błąd 500.
  Ekran konfiguracji w panelu (T13) jest odczytem **konfiguracji** i to on woła
  ``require_competition()`` u siebie – tam pustka faktycznie jest błędem wołającego.
- **Flaga jest czytana raz, tutaj** – przez ``Competition.has_feature``.

**Wersja jest wskaźnikiem, nie ozdobą.** PDF-a nikt nie przechowuje (docstring
``apps/results/certificates.py``), więc powstaje przy każdym pobraniu. Bez zapamiętanej wersji
poprawka zdania zmieniałaby treść dokumentu, który ktoś trzyma w ręku – dlatego
``results.Certificate.template_version`` jest **kopią napisu** ``DocumentTemplate.version``
(dokładnie tak, jak ``accounts.ConsentRecord.document_version``), a puste pole znaczy „układ
wbudowany”, czyli dzisiejsze stałe.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from string import Formatter

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager
from apps.tenancy.branding import competition_name
from apps.tenancy.branding import substitutions as branding_substitutions

logger = logging.getLogger(__name__)

#: Przełącznik, za którym stoi **cały** ten moduł. Nazwa jest jedna i jest tutaj, żeby literówka
#: w niej wywracała się w jednym miejscu, a nie w każdym wywołaniu ``has_feature``.
DOCUMENTS_FLAG = "document_templates"

#: Wersja wpisana Konkursowi #1 przez migrację ``tenancy.0005_document_templates``. Stała stoi
#: tutaj, a nie w migracji, bo cytuje ją także test porównujący wiersze bazy ze stałymi składu.
INITIAL_VERSION = "1.0 (stan z v0.23.0)"


class DocumentKind(models.TextChoices):
    """Rodzaj dokumentu, którego tekst jest konfiguracją konkursu.

    Pierwsze pięć wartości jest **kopią** ``results.CertificateKind``, a nie importem: tamto
    opisuje wiersz rejestru wystawionych dyplomów, to opisuje szablon tekstu, i obie listy będą
    rosły w innym tempie (faktura nie jest dyplomem, a lista obecności nie ma numeru ani kodu
    weryfikacyjnego). Zgodność pilnuje test ``test_document_kinds_cover_certificate_kinds``.
    """

    LAUREAT = "LAUREAT", "dyplom laureata"
    FINALISTA = "FINALISTA", "dyplom finalisty"
    UCZESTNIK = "UCZESTNIK", "zaświadczenie uczestnika"
    OPIEKUN = "OPIEKUN", "zaświadczenie opiekuna"
    WARSZTATY = "WARSZTATY", "zaświadczenie z warsztatów"
    GUARDIAN_FORM = "GUARDIAN_FORM", "wzór zgody opiekuna"
    #: § 1.5.1, za flagą ``fees`` – dokument rozliczeniowy wpisowego (T47).
    INVOICE = "INVOICE", "faktura / rachunek"
    #: § 1.5.2, za flagą ``onsite_logistics`` – tytuł listy obecności (T48,
    #: ``apps.integrations.exports.logistics_list_title``).
    ATTENDANCE_LIST = "ATTENDANCE_LIST", "lista obecności"
    #: Wzór „Zaświadczenia o statusie ucznia” do podstemplowania w szkole, za flagą
    #: ``student_status_certificate`` (``apps.student_status.pdf``). Rodzaj w tej liście, a nie
    #: osobny model tekstu: organizator poprawia zdanie zaświadczenia tym samym ekranem i z tym
    #: samym wersjonowaniem, co zdanie na dyplomie – to jest ta sama czynność na innym papierze.
    STUDENT_STATUS = "STUDENT_STATUS", "zaświadczenie o statusie ucznia"


#: Znaczniki dozwolone w treści szablonu – **lista zamknięta**, sprawdzana przy zapisie.
#: Zamknięta, bo literówka ``{partcipant_code}`` ma zostać zauważona w panelu, a nie w PDF-ie
#: wydanym tysiącu osób. Nazwy są po angielsku, tak jak nazwy pól modeli, a nie jak napisy dla
#: człowieka: znacznik jest kawałkiem składni, a nie treścią.
ALLOWED_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "competition",  # ``short_name or name``
        "competition_genitive",
        "competition_locative",
        "organizer",  # ``Competition.organizer_name``
        "edition",  # ``Edition.year_label``
        "participant_code",  # ``Participant.public_code``
        "recipient",
        "school",
        "stage",  # ``Stage.display_name``
        "date",
        "number",
        "code",
    }
)

#: Znaczniki dodatkowe **jednego rodzaju** dokumentu. Dziś pusty i taki ma zostać, dopóki nowy
#: rodzaj nie przyniesie własnego pojęcia (kwota i termin płatności na fakturze, § 1.5.1). Seam
#: istnieje po to, żeby dołożenie takiego pojęcia było jednym wierszem danych, a nie gałęzią
#: w walidacji – a zarazem żeby ``{amount}`` nie dało się wpisać na dyplomie laureata.
EXTRA_PLACEHOLDERS_BY_KIND: dict[str, frozenset[str]] = {
    # Dokument rozliczeniowy wpisowego (§ 1.5.1, T47). Kwota, waluta, stawka VAT i termin
    # płatności są pojęciami **należności**, a nie dyplomu: na fakturze muszą dać się wpisać,
    # a na dyplomie laureata nie mają czego znaczyć.
    DocumentKind.INVOICE: frozenset({"amount", "currency", "vat_rate", "due_date"}),
    # Zaświadczenie o statusie ucznia (prośba organizatora z 24.09.2026). Data urodzenia jest
    # pojęciem **tego** dokumentu – szkoła poświadcza tożsamość ucznia, a imię i nazwisko bez daty
    # bywa niejednoznaczne w dużej szkole. Na dyplomie laureata data urodzenia nie ma czego szukać,
    # więc nie może dać się tam wpisać. ``school_year`` to sam rok szkolny („2026/2027”) wyjęty
    # z oznaczenia edycji („I edycja 2026/2027”): zdanie „uczeń w roku szkolnym I edycja 2026/2027”
    # nie jest zdaniem, które szkoła podpisze.
    DocumentKind.STUDENT_STATUS: frozenset({"birth_date", "school_year"}),
}

#: Pola szablonu niosące tekst z podstawieniami. Kolejność jest kolejnością czytania dokumentu.
TEXT_FIELDS = ("title", "statement", "signature_line", "footer_note")


def allowed_placeholders(kind: str) -> frozenset[str]:
    """Znaczniki dozwolone w dokumencie tego rodzaju: wspólne plus własne rodzaju."""
    return ALLOWED_PLACEHOLDERS | EXTRA_PLACEHOLDERS_BY_KIND.get(str(kind), frozenset())


class _SafeSubstitutions(dict):
    """Mapa podstawień, która **nie wywraca składu** na nieznanym znaczniku.

    Znacznik z listy dozwolonych, którego dany dokument nie ma (dyplom opiekuna nie ma
    ``{participant_code}``), jest w mapie z pustą wartością – tak chce § 1.1.3. Znacznik **spoza**
    listy zostaje na papierze widoczny jako ``{tak_jak_go_wpisano}``, zamiast podnieść ``KeyError``
    w chwili pobrania dokumentu. To nie jest pobłażliwość: taki wiersz nie przejdzie przez
    :meth:`DocumentTemplate.clean`, więc do bazy może trafić wyłącznie obok panelu (fikstura,
    import, ręczny ``UPDATE``) – a wtedy lepszy jest dokument z widoczną usterką niż brak
    dokumentu i błąd 500 u uczestnika.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def uses_document_templates(competition=None) -> bool:
    """Czy teksty dokumentów tego konkursu mają iść z bazy, czy ze stałych składu.

    **Jedyne** wejście do flagi ``document_templates``. Brak konkursu to nie jest „flaga
    wyłączona przez organizatora”, tylko „nie wiadomo, czyj to dokument” – a odpowiedź w obu
    wypadkach jest ta sama: napis sprzed etapu 2.
    """
    return competition is not None and competition.has_feature(DOCUMENTS_FLAG)


def substitutions(competition=None, **context: object) -> dict[str, str]:
    """Wartości podstawień dla jednego dokumentu – komplet, z pustkami włącznie.

    Każdy dozwolony znacznik jest w wyniku, także ten, którego wołający nie podał: dokument ma
    wyjść z pustym miejscem, a nie z klamrą na papierze. Trzy formy nazwy konkursu biorą się
    z ``apps/tenancy/branding.py``, bo odmiana jest **danymi** konkursu i ma jedno źródło.
    """
    values: dict[str, str] = dict.fromkeys(ALLOWED_PLACEHOLDERS, "")
    if competition is not None:
        values.update({key: str(value) for key, value in branding_substitutions(competition).items()})
        values["organizer"] = competition.organizer_name
    values.update({key: "" if value is None else str(value) for key, value in context.items()})
    return values


def substitute(text: str, competition=None, **context: object) -> str:
    """Tekst szablonu z podstawionymi wartościami. Bez znaczników zwraca to, co dostał."""
    return _format(text, substitutions(competition, **context))


def _values_for(competition, kind: str, context: dict[str, object]) -> dict[str, str]:
    """Podstawienia dokumentu tego rodzaju – wspólne plus puste miejsca po znacznikach własnych.

    Znacznik własny rodzaju (``{amount}`` na fakturze), którego wołający nie podał, ma zachować
    się tak samo, jak znacznik wspólny bez wartości: puste miejsce na papierze, a nie klamra.
    """
    values = substitutions(competition, **context)
    for name in EXTRA_PLACEHOLDERS_BY_KIND.get(str(kind), frozenset()):
        values.setdefault(name, "")
    return values


def _format(text: str, values: dict[str, str]) -> str:
    """``str.format_map`` z mapą :class:`_SafeSubstitutions`, która nie wywraca składu.

    Wzorzec **składniowo** zepsuty (niedomknięta klamra, ``{0}``, ``{competition.name}``) nie ma
    jak się podstawić i wtedy zwracamy tekst surowy – z ostrzeżeniem w logu, bo to jest usterka
    konfiguracji, a nie zdarzenie dnia codziennego. Dokument wychodzi mimo wszystko; to ta sama
    decyzja, co przy pieczęci elektronicznej, której brak nie wstrzymuje wydania dyplomu.
    """
    if "{" not in text:
        return text
    try:
        return text.format_map(_SafeSubstitutions(values))
    except ValueError, IndexError, AttributeError, TypeError:
        logger.warning("Nie udało się podstawić wartości we wzorcu dokumentu: %r.", text)
        return text


@dataclass(frozen=True)
class RenderedDocument:
    """Napisy jednego dokumentu – gotowe do składu i nic poza tym.

    ``version`` jest pusta dokładnie wtedy, gdy napisy przyszły ze stałych składu („układ
    wbudowany”). To ona idzie do ``results.Certificate.template_version`` i to ona rozstrzyga,
    czy dokument pobrany za rok będzie tym samym dokumentem.
    """

    kind: str
    version: str
    title: str
    statement: str
    signature_line: str
    footer_note: str
    #: Autor w metadanych pliku. Przy włączonej fladze – nazwa konkursu; bez niej dzisiejszy
    #: literał podany przez wołającego.
    author: str


def templates_of(competition=None):
    """Szablony jednego konkursu – wejście do każdego odczytu tej tabeli.

    ``None`` nie widzi niczego (``CompetitionScopedQuerySet.for_competition``): szablon jest
    konfiguracją konkursu, a „pokaż wszystkie” na tej tabeli znaczyłoby cudzy tekst na cudzym
    papierze.
    """
    return DocumentTemplate.objects.for_competition(competition)


def current_template(competition, kind: str) -> DocumentTemplate | None:
    """Obowiązujący szablon tego rodzaju albo ``None`` – wtedy obowiązują stałe składu.

    Przy wyłączonej fladze **nie pada ani jedno zapytanie**: to jest ten warunek z § 5.6, na
    którym stoją budżety zapytań Konkursu #1.
    """
    if not uses_document_templates(competition):
        return None
    return templates_of(competition).filter(kind=str(kind), is_current=True).first()


def template_for_version(competition, kind: str, version: str) -> DocumentTemplate | None:
    """Szablon w **tej** wersji – ten, z którego dokument powstał, a nie ten obowiązujący dziś.

    Pusta wersja znaczy „układ wbudowany” (dokument sprzed etapu 2 albo wystawiony przy wyłączonej
    fladze) i nie sięga do bazy. Wersja, której już nie ma (wiersz skasowany ręcznie, baza
    przeniesiona z innej instalacji), oddaje szablon obowiązujący – dokument ma wyjść, a rozjazd
    widać w rejestrze, bo ``template_version`` dokumentu zostaje niezmieniona.
    """
    if not version or not uses_document_templates(competition):
        return None
    found = templates_of(competition).filter(kind=str(kind), version=version).first()
    if found is not None:
        return found
    logger.warning(
        "Dokument rodzaju %s odwołuje się do wersji %r, której nie ma w bazie konkursu %s.",
        kind,
        version,
        getattr(competition, "pk", None),
    )
    return current_template(competition, kind)


def current_version(competition, kind: str) -> str:
    """Wersja obowiązującego szablonu albo pusty napis. Tyle, ile trzeba zapisać przy wystawieniu."""
    template = current_template(competition, kind)
    return template.version if template is not None else ""


def render_document(
    competition,
    kind: str,
    *,
    version: str | None = None,
    fallback: Mapping[str, str] | None = None,
    **context: object,
) -> RenderedDocument:
    """Napisy dokumentu: z szablonu konkursu albo dzisiejsze literały wołającego.

    To jest **jedyne** wejście dla składu dokumentu – woła je i dyplom (``apps.results``),
    i dokument rozliczeniowy (§ 1.5.1), i lista obecności (§ 1.5.2).

    - ``version=None`` – weź szablon obowiązujący (zwykły przypadek: dokument powstaje teraz),
    - ``version="1.0 …"`` – weź **tę** wersję (dokument wystawiony kiedyś, patrz
      ``Certificate.template_version``),
    - ``version=""`` – układ wbudowany, czyli literały z ``fallback``; bez zapytania.

    ``fallback`` jest mapą dzisiejszych napisów (``title``, ``statement``, ``signature_line``,
    ``footer_note``, ``author``) i podaje ją wołający, a nie ten moduł. Powód stoi w docstringu
    ``apps/tenancy/branding.py``: napis w kodzie jest **treścią**, wiersz w bazie jest
    **konfiguracją**, a odwrót ma być dosłownie tym, co serwis wypisuje dzisiaj – nie wzorcem
    podstawionym nazwą Konkursu #1.

    ``context`` dokłada wartości podstawień (``recipient``, ``number``, ``date`` …). Wołający ma
    prawo policzyć je bez warunku: znaczników nieużytych nikt nie zobaczy, a te, które kosztują
    zapytanie, po prostu nie należą do kontekstu dokumentu (dyplom nie ma na papierze etapu).
    """
    template = (
        current_template(competition, kind)
        if version is None
        else template_for_version(competition, kind, version)
    )
    if template is None:
        return _from_fallback(kind, fallback)
    values = _values_for(competition, kind, context)
    return RenderedDocument(
        kind=str(kind),
        version=template.version,
        title=_format(template.title, values),
        statement=_format(template.statement, values),
        signature_line=_format(template.signature_line, values),
        footer_note=_format(template.footer_note, values),
        # Autor pliku jest marką, a nie zdaniem dokumentu, więc nie ma własnej kolumny: bierze się
        # z nazwy konkursu, tak jak podpis pod listem (``branding.competition_name``).
        author=competition_name(competition) if competition is not None else "",
    )


def _from_fallback(kind: str, fallback: Mapping[str, str] | None) -> RenderedDocument:
    """Dzisiejsze literały wołającego jako dokument. Pusta wersja = układ wbudowany."""
    values = fallback or {}
    return RenderedDocument(
        kind=str(kind),
        version="",
        title=values.get("title", ""),
        statement=values.get("statement", ""),
        signature_line=values.get("signature_line", ""),
        footer_note=values.get("footer_note", ""),
        author=values.get("author", ""),
    )


class DocumentTemplate(models.Model):
    """Tekst dokumentu z podstawieniami – **napisy**, nie skład.

    Skład zostaje w ``apps/results/certificates.py`` (ReportLab): zmienia się źródło napisów,
    a nie rysowanie. Grafikę opisuje ``results.CertificateTemplate`` i te dwa modele celowo nie
    są jednym – powód stoi w docstringu modułu.

    Wersje historyczne **zostają** (``is_current=False``): dokument wydany w zeszłym roku ma dać
    się odtworzyć co do znaku, bo PDF-a nikt nie przechowuje.

    Dlaczego osobny moduł, a nie ``models.py``: podział własności plików etapu 2 (§ 4.1) – katalog
    przełączników w ``models.py`` ma jednego właściciela. Model rejestruje się przez import
    w ``TenancyConfig.ready`` (``apps/tenancy/apps.py``), a etykieta aplikacji jest podana wprost,
    żeby nie zależała od tego, skąd moduł zostanie zaimportowany (ta sama reguła, co
    w ``apps/tenancy/aliases.py``).
    """

    #: ``CASCADE``, a nie ``PROTECT`` – szablon jest **konfiguracją konkursu**, a nie dowodem.
    #: Skasowanie konkursu (kreator ``/setup/``, sprzątanie instalacji testowej) ma zabrać jego
    #: teksty razem z nim; wiersz, który przeżywa własnego właściciela, nie jest niczyją
    #: konfiguracją, tylko wierszem blokującym kasowanie. Dowód zostaje osobno i nie kaskaduje:
    #: ``results.Certificate.template_version`` jest **kopią napisu**, więc dokument wystawiony
    #: kiedyś niesie swoją wersję także wtedy, gdy szablonu już nie ma.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="document_templates",
        verbose_name="konkurs",
    )
    kind = models.CharField("rodzaj", max_length=24, choices=DocumentKind.choices)
    #: Wersja treści jako **napis**, nie numer – ta sama konwencja, co ``ConsentRecord.document_version``.
    #: Numer porządkowy kusiłby, żeby go zwiększać automatycznie; wersję dokumentu ustala człowiek,
    #: który wie, czy poprawka literówki jest nową wersją, czy nie.
    version = models.CharField("wersja", max_length=100)
    title = models.CharField("tytuł na dokumencie", max_length=200)
    statement = models.TextField("zdanie główne")
    signature_line = models.CharField("linia podpisu", max_length=200, blank=True)
    footer_note = models.CharField("dopisek w stopce", max_length=300, blank=True)
    is_current = models.BooleanField("obowiązująca", default=True)
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        app_label = "tenancy"
        verbose_name = "szablon dokumentu"
        verbose_name_plural = "szablony dokumentów"
        ordering = ("competition", "kind", "-created_at", "-id")
        constraints = [
            # Jeden obowiązujący szablon na rodzaj. Dwa znaczyłyby dwa różne dyplomy laureata
            # wystawiane tego samego dnia, zależnie od kolejności wierszy w zapytaniu.
            models.UniqueConstraint(
                fields=["competition", "kind"],
                condition=Q(is_current=True),
                name="tenancy_documenttemplate_single_current",
            ),
            # Wersja jest **wskaźnikiem**: ``Certificate.template_version`` wskazuje nią wiersz.
            # Dwa wiersze tej samej wersji uczyniłyby ten wskaźnik dwuznacznym, a odtworzenie
            # dokumentu sprzed roku – loterią.
            models.UniqueConstraint(
                fields=["competition", "kind", "version"],
                name="tenancy_documenttemplate_version_per_kind",
            ),
            models.CheckConstraint(
                condition=~Q(version=""),
                name="tenancy_documenttemplate_version_not_empty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} – {self.version}"

    def clean(self) -> None:
        """Znaczniki spoza listy dozwolonych, z komunikatem wyliczającym dozwolone.

        Walidacja jest w modelu, a nie w formularzu panelu, bo szablon zakłada tak samo dobrze
        komenda, migracja i import – formularz jest tylko jednym z wołających (ta sama reguła, co
        w ``Competition.clean`` i ``CompetitionSiteAlias.clean``).
        """
        super().clean()
        allowed = allowed_placeholders(self.kind)
        errors: dict[str, str] = {}
        for field in TEXT_FIELDS:
            message = _placeholder_error(getattr(self, field, "") or "", allowed)
            if message:
                errors[field] = message
        if errors:
            raise ValidationError(errors)

    def render(self, competition=None, **context: object) -> RenderedDocument:
        """Napisy **tego** wiersza – z pominięciem wyszukiwania szablonu.

        Dla podglądu w panelu: koordynator ogląda wersję, którą właśnie pisze, także wtedy, gdy
        obowiązująca jest jeszcze poprzednia.
        """
        source = competition if competition is not None else self.competition
        values = _values_for(source, self.kind, context)
        return RenderedDocument(
            kind=str(self.kind),
            version=self.version,
            title=_format(self.title, values),
            statement=_format(self.statement, values),
            signature_line=_format(self.signature_line, values),
            footer_note=_format(self.footer_note, values),
            author=competition_name(source) if source is not None else "",
        )


def _placeholder_error(text: str, allowed: frozenset[str]) -> str:
    """Komunikat o znacznikach nie do przyjęcia albo pusty napis, gdy tekst jest w porządku."""
    if "{" not in text and "}" not in text:
        return ""
    listing = ", ".join(f"{{{name}}}" for name in sorted(allowed))
    try:
        fields = [field for _, field, _, _ in Formatter().parse(text) if field is not None]
    except ValueError:
        return (
            "Nawiasy klamrowe nie są domknięte. Znacznik zapisuje się jako {nazwa}, "
            f"a klamrę na papierze – jako {{{{ i }}}}. Dozwolone znaczniki: {listing}."
        )
    unknown = [field for field in fields if field not in allowed]
    if unknown:
        listed = ", ".join(f"{{{field}}}" for field in unknown)
        return f"Nieznane znaczniki: {listed}. Dozwolone: {listing}."
    return ""


@transaction.atomic
def set_current_template(
    competition,
    kind: str,
    *,
    version: str,
    title: str,
    statement: str,
    signature_line: str = "",
    footer_note: str = "",
    actor=None,
    request=None,
) -> DocumentTemplate:
    """Nowa obowiązująca wersja tekstu dokumentu. Poprzednia **zostaje** jako historia.

    Jedno wejście do zmiany treści dokumentu – woła je ekran konfiguracji w panelu (T13)
    i komenda. Trzy rzeczy dzieją się tu razem i dlatego są w jednej transakcji: poprzedni wiersz
    traci ``is_current`` (inaczej więz unikalności odrzuciłby nowy), nowy przechodzi walidację
    znaczników i powstaje wpis audytowy z **obiema** wersjami. Zmiana zdania na dyplomie jest
    zmianą treści dokumentu urzędowego; bez śladu „kto i kiedy” nie da się na nią odpowiedzieć
    przy pierwszej reklamacji.

    Podniesienie wersji jest **wymagane** przez więz unikalności ``(konkurs, rodzaj, wersja)``:
    poprawka bez nowej wersji byłaby cichą podmianą treści dokumentów już wystawionych, bo one
    wskazują szablon właśnie wersją.
    """
    from apps.core.models import audit

    previous = templates_of(competition).filter(kind=str(kind), is_current=True).first()
    if templates_of(competition).filter(kind=str(kind), version=version).exists():
        raise ValidationError(
            {
                "version": (
                    f"Wersja „{version}” tego dokumentu już istnieje. Wersja wskazuje wiersz, "
                    "z którego powstał dokument wystawiony kiedyś – dwie o tej samej nazwie "
                    "znaczyłyby, że nie wiadomo, którą z nich ktoś trzyma w ręku."
                )
            }
        )
    template = DocumentTemplate(
        competition=competition,
        kind=str(kind),
        version=version,
        title=title,
        statement=statement,
        signature_line=signature_line,
        footer_note=footer_note,
        is_current=True,
    )
    # ``validate_constraints=False``: więz „jeden obowiązujący na rodzaj” jest w tej chwili
    # naruszony celowo – poprzedni wiersz traci ``is_current`` linijkę niżej, a walidacja
    # sprawdziłaby stan sprzed tej zmiany. Pilnuje go baza przy ``save()``.
    template.full_clean(exclude=["competition"], validate_constraints=False)
    if previous is not None:
        DocumentTemplate.objects.filter(pk=previous.pk).update(is_current=False)
    template.save()
    audit(
        actor,
        "document_template.changed",
        template,
        {
            "kind": str(kind),
            "version": version,
            "previous_version": previous.version if previous is not None else "",
        },
        request=request,
    )
    logger.info(
        "Nowa wersja szablonu dokumentu %s w konkursie %s: %r.",
        kind,
        getattr(competition, "pk", None),
        version,
    )
    return template
