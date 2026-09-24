"""Ekran „Szablony dokumentów” ``/coordinator/documents/``.

Jedyne miejsce, w którym organizator zmienia **tekst dokumentu**: tytuł na papierze, zdanie pod
nazwiskiem, linię podpisu i dopisek w stopce – osobno dla każdego rodzaju (dyplom laureata,
zaświadczenie opiekuna, wzór zgody, faktura, lista obecności). Skład zostaje tam, gdzie był
(``apps.results.certificates``, ReportLab): ten ekran zmienia **źródło napisów**, a nie rysowanie.

**Dlaczego ekran jest za flagą ``document_templates``.** Dopóki flaga jest wyłączona, napisy idą
ze stałych składu (``DOCUMENT_TITLES``, ``DOCUMENT_STATEMENTS``, ``SIGNATURE_LINE``), a wiersze
``tenancy.DocumentTemplate`` leżą w bazie nieużywane – wpisała je migracja jako kopię tych stałych.
Ekran, który pozwalałby je wtedy zmieniać, obiecywałby poprawkę, której nikt nigdy nie zobaczy na
papierze. Adres przy wyłączonej fladze daje **404**, a nie 403 (``docs/UNIWERSALNY-ETAP-2.md``
§ 2.1): adresu, którego w tej instalacji nie ma, nie ma tak samo jak adresu cudzego konkursu. Rola
sprawdza się wcześniej i osobno (``CoordinatorRequiredMixin``), więc uczestnik dostaje 403
niezależnie od flagi i nie dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Konkurs bierzemy przez ``require_competition``** (§ 1.0 b), a nie przez miękkie
``resolve_competition``: szablon dokumentu jest **konfiguracją konkursu**, więc „nie wiadomo, o który
konkurs chodzi” ma tu być słyszalne. To jest ta sama granica, którą opisuje docstring
``apps.tenancy.documents``: skład dokumentu (PDF powstający przy pobraniu) ma odwrót miękki, bo
pustka nie może zamienić pobrania dyplomu w błąd; odczyt konfiguracji odwrotu miękkiego nie ma.

**Trzy kroki jednej zmiany.** Nowa wersja tekstu przechodzi przez formularz → podgląd → ekran
potwierdzenia, i dopiero stamtąd przez ``set_current_template``:

- **podgląd** składa się po stronie serwera (``DocumentTemplate.render`` na niezapisanym wierszu)
  z podstawieniami przykładowymi. Bez JavaScriptu, bo panel chodzi pod ścisłą polityką CSP – i bez
  czytania bazy, bo przykładowy uczestnik ma być przykładowy, a nie pierwszy z brzegu z wyników;
- **potwierdzenie nazywa wersję** („od tej chwili dokumenty będą wystawiane w wersji X”), bo skutku
  nie widać na ekranie, na którym się go wywołuje: granica przesuwa się w
  ``results.Certificate.template_version`` dokumentów wystawianych **od tej chwili**, a dokumenty
  wystawione wcześniej mają dalej wychodzić ze swojej wersji;
- **zapisu nie ma tutaj.** Demotowanie poprzedniego wiersza, walidacja znaczników i wpis audytowy
  ``document_template.changed`` dzieją się w jednej transakcji w ``set_current_template`` – widok
  wyłącznie orkiestruje (§ 2.1).

**Czego tu nie ma i dlaczego.** Kasowania wersji: wersja jest wskaźnikiem, którym dokument
wystawiony kiedyś pokazuje swój własny tekst, a skasowanie wiersza zamieniłoby odtworzenie takiego
dokumentu w zgadywanie. Edycji wersji obowiązującej „w miejscu”: poprawka bez podniesienia wersji
byłaby cichą podmianą treści dokumentów już wydanych – dlatego jedyną drogą zmiany jest **nowa
wersja**, a więz ``(konkurs, rodzaj, wersja)`` pilnuje, żeby nie dało się jej obejść.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.scoping import require_competition
from apps.tenancy.documents import (
    DOCUMENTS_FLAG,
    DocumentKind,
    DocumentTemplate,
    allowed_placeholders,
    set_current_template,
    templates_of,
)
from apps.web.coordinator_forms import DocumentTemplateForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/documents.html"
FORM_TEMPLATE = "web/coordinator/document_form.html"
CONFIRM_TEMPLATE = "web/coordinator/document_version_confirm.html"

#: Przełącznik, który włącza ten ekran – ta sama stała, którą czyta ``render_document``. Napis
#: w dwóch miejscach dałby ekran włączony flagą, której nikt nie czyta przy składzie dokumentu.
FEATURE = DOCUMENTS_FLAG

#: Nazwa pola, po którym poznajemy prośbę o podgląd. Sam przycisk, bez wartości: podgląd jest
#: czynnością („pokaż, jak to wyjdzie”), a nie wartością formularza.
PREVIEW_FIELD = "preview"

#: Nazwa pola, po którym poznajemy drugi krok publikacji. Jak wyżej: potwierdzenie ma być
#: czynnością, a nie napisem przepisanym z pierwszego kroku.
CONFIRM_FIELD = "confirm"

#: Podstawienia podglądu. Literały, nie wiersze z bazy – i to jest cała decyzja tego słownika.
#: Przykładowy dyplom ma pokazać **kształt zdania**, a nie nazwisko pierwszego uczestnika
#: z wyników: podgląd tekstu nie jest powodem, żeby czyjeś dane szły na ekran (ani żeby ekran
#: konfiguracji kosztował zapytania o uczestników). Trzy formy nazwy konkursu i nazwa organizatora
#: **nie** są tu wypisane: te bierze ``substitutions`` z konkursu, bo są prawdziwe.
SAMPLE_CONTEXT: dict[str, str] = {
    "edition": "2026/2027",
    "recipient": "Anna Przykładowska",
    "school": "I Liceum Ogólnokształcące w Krakowie",
    "participant_code": "OLM-2026-0001",
    "stage": "Etap II – okręgowy",
    "date": "12.05.2027",
    "number": "OK/2027/0001",
    "code": "7F3A-2B9C-5E1D",
    # Znaczniki własne faktury (§ 1.5.1). Wpisane bezwarunkowo, bo ``render`` i tak poda pustkę
    # rodzajowi, który ich nie zna – a rozgałęzienie po rodzaju znaczyłoby drugie miejsce, które
    # musi wiedzieć to samo, co ``EXTRA_PLACEHOLDERS_BY_KIND``.
    "amount": "120,00",
    "currency": "PLN",
    "vat_rate": "23%",
    "due_date": "26.05.2027",
    # Znacznik własny zaświadczenia o statusie ucznia (``EXTRA_PLACEHOLDERS_BY_KIND``) – z tego
    # samego powodu wpisany bezwarunkowo, co znaczniki faktury wyżej.
    "birth_date": "14.03.2009",
    "school_year": "2026/2027",
}


class DocumentScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka dwóch widoków: rola koordynatora, konkurs żądania, flaga konkursu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma włączone własne teksty dokumentów. Inaczej 404.

        Konkurs bierzemy z kontekstu żądania (``CompetitionMiddleware``), a nie z identyfikatora
        w adresie – dzięki temu nie ma tu adresu, pod którym dałoby się wskazać cudze szablony.
        Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej
        ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo 403, **zanim**
        odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = require_competition(getattr(request, "competition", None))
        if not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs składa dokumenty ze stałych – ekran jest wyłączony.")
        return competition

    def kind_or_404(self, kind: str) -> DocumentKind:
        """Rodzaj dokumentu z adresu albo 404 – lista jest zamknięta i jest w kodzie.

        Rodzaju nie da się dołożyć z panelu: po rodzaju poznaje dokument rejestr dyplomów
        (``results.CertificateKind``) i skład, więc wartość spoza listy nie jest „jeszcze
        nieskonfigurowanym” rodzajem, tylko literówką w adresie.
        """
        if kind not in DocumentKind.values:
            raise Http404(f"Nie ma rodzaju dokumentu {kind!r}.")
        return DocumentKind(kind)


def _versions(competition, kind: str) -> list[DocumentTemplate]:
    """Wszystkie wersje tego rodzaju, od najnowszej (``Meta.ordering``).

    Komplet, a więc razem z wycofanymi: dokument wystawiony w zeszłym roku wskazuje swoją wersję
    napisem i musi dać się odtworzyć co do znaku – historia nie jest tu ozdobą, tylko jedynym
    miejscem, w którym widać, co wtedy było na papierze.
    """
    return list(templates_of(competition).filter(kind=str(kind)))


def _draft(competition, kind: str, current: DocumentTemplate | None) -> DocumentTemplateForm:
    """Formularz nowej wersji wypełniony treścią obowiązującą – bez numeru wersji.

    Treść jest przepisana, bo zmiana zdania zwykle jest poprawką jednego słowa, a nie pisaniem
    dokumentu od nowa. Numer wersji zostaje **pusty** z premedytacją: podpowiedziany numer
    kusiłby, żeby go zatwierdzić bez czytania, a to on odpowiada po latach na pytanie „który to
    był tekst”.
    """
    initial = {}
    if current is not None:
        initial = {
            "title": current.title,
            "statement": current.statement,
            "signature_line": current.signature_line,
            "footer_note": current.footer_note,
        }
    return DocumentTemplateForm(
        initial=initial, instance=DocumentTemplate(competition=competition, kind=str(kind))
    )


def _preview(form: DocumentTemplateForm, competition):
    """Podgląd wersji pisanej **teraz** albo ``None``, gdy nie ma czego pokazać.

    ``form.instance`` po udanej walidacji jest kompletnym, niezapisanym wierszem – i to jego
    ``render`` składa podgląd. Dzięki temu koordynator ogląda dokładnie ten tekst, który zaraz
    trafi do ``set_current_template``, a nie jego kopię złożoną drugą drogą.
    """
    if not form.is_bound or not form.is_valid():
        return None
    return form.instance.render(competition, **SAMPLE_CONTEXT)


def _page_context(competition, kind: DocumentKind, form, *, preview=None) -> dict:
    """Jeden kontekst ekranu rodzaju – ten sam dla ``GET`` i dla nieudanego ``POST``."""
    versions = _versions(competition, kind)
    current = next((row for row in versions if row.is_current), None)
    allowed = sorted(allowed_placeholders(str(kind)))
    return {
        "kind": kind,
        "kind_label": kind.label,
        "current": current,
        # Podgląd obowiązującego tekstu tymi samymi podstawieniami, co podgląd wersji pisanej –
        # inaczej porównanie „było → będzie” byłoby porównaniem wzorca ze złożonym zdaniem.
        "current_preview": current.render(competition, **SAMPLE_CONTEXT) if current else None,
        "versions": versions,
        "form": form,
        "preview": preview,
        # Znaczniki podajemy **w klamrach**, bo w klamrach się je wpisuje. Klamra dokładana
        # w szablonie znaczyłaby, że lista dozwolonych wygląda inaczej niż to, co trzeba napisać.
        "placeholders": [f"{{{name}}}" for name in allowed],
        "preview_field": PREVIEW_FIELD,
        # Wartości podglądu **tego** rodzaju: ``{amount}`` nie ma czego znaczyć na dyplomie
        # laureata, więc nie ma go też na liście podpowiedzi – ekran ma pokazywać to, co wolno
        # wpisać, a nie wszystko, co system kiedykolwiek podstawia.
        "sample": [
            (f"{{{name}}}", value) for name, value in sorted(SAMPLE_CONTEXT.items()) if name in allowed
        ],
    }


class DocumentTemplateListView(DocumentScreenMixin, View):
    """``GET /coordinator/documents/`` – rodzaje dokumentów: wersja obowiązująca i liczba wersji."""

    def get(self, request):
        competition = self.competition_or_404(request)
        # Jedno zapytanie na cały ekran: rodzajów jest osiem, a wersji każdego – kilka. Zapytanie
        # per rodzaj byłoby ośmioma zapytaniami na odsłonę listy, która niczego więcej nie robi.
        by_kind: dict[str, list[DocumentTemplate]] = {}
        for row in templates_of(competition):
            by_kind.setdefault(row.kind, []).append(row)
        rows = []
        for kind in DocumentKind:
            versions = by_kind.get(str(kind), [])
            rows.append(
                {
                    "kind": kind,
                    "label": kind.label,
                    "current": next((row for row in versions if row.is_current), None),
                    "count": len(versions),
                }
            )
        return TemplateResponse(request, LIST_TEMPLATE, {"rows": rows})


class DocumentTemplateDetailView(DocumentScreenMixin, View):
    """``GET|POST /coordinator/documents/<kind>/`` – wersja obowiązująca, historia, nowa wersja.

    Jeden adres, trzy czynności rozstrzygane nazwą przycisku: podgląd (``preview``) nic nie
    zapisuje i oddaje tę samą stronę ze złożonym tekstem, wysłanie bez przycisku prowadzi na ekran
    potwierdzenia, a potwierdzenie (``confirm``) woła serwis. Osobny adres na podgląd znaczyłby
    trzeci szablon pokazujący to samo, a osobny adres na potwierdzenie – przepisywanie pięciu pól
    przez sesję albo przez adres.
    """

    def get(self, request, kind: str):
        competition = self.competition_or_404(request)
        document_kind = self.kind_or_404(kind)
        versions = _versions(competition, document_kind)
        current = next((row for row in versions if row.is_current), None)
        form = _draft(competition, document_kind, current)
        return TemplateResponse(request, FORM_TEMPLATE, _page_context(competition, document_kind, form))

    def post(self, request, kind: str):
        competition = self.competition_or_404(request)
        document_kind = self.kind_or_404(kind)
        form = DocumentTemplateForm(
            request.POST,
            instance=DocumentTemplate(competition=competition, kind=str(document_kind)),
        )
        if not form.is_valid():
            return self._render(request, competition, document_kind, form, status=400)
        if PREVIEW_FIELD in request.POST:
            # Podgląd jest **odczytem**: ta sama strona, ten sam formularz, plus złożony tekst.
            return self._render(request, competition, document_kind, form)
        if CONFIRM_FIELD not in request.POST:
            context = {
                "kind": document_kind,
                "kind_label": document_kind.label,
                "current": next(
                    (row for row in _versions(competition, document_kind) if row.is_current), None
                ),
                "values": form.cleaned_data,
                "preview": _preview(form, competition),
                "confirm_field": CONFIRM_FIELD,
            }
            return TemplateResponse(request, CONFIRM_TEMPLATE, context)
        try:
            set_current_template(
                competition,
                str(document_kind),
                actor=request.user,
                request=request,
                **form.cleaned_data,
            )
        except ValidationError as error:
            # Jedyna droga tutaj prowadzi przez wersję, która w międzyczasie powstała drugą ręką
            # (dwoje koordynatorów, ta sama nazwa wersji). Komunikat serwisu jest wtedy jedyną
            # informacją, co poprawić – i wraca razem z tym, co koordynator zdążył napisać.
            messages.error(request, "; ".join(error.messages))
            return self._render(request, competition, document_kind, form, status=400)
        version = form.cleaned_data["version"]
        messages.success(
            request,
            f"Od tej chwili dokumenty będą wystawiane w wersji {version}. "
            "Dokumenty wydane wcześniej zostały bez zmian.",
        )
        return redirect(reverse("web:coordinator-document", args=[str(document_kind)]))

    def _render(self, request, competition, kind: DocumentKind, form, *, status: int = 200):
        """Pełne przerenderowanie ekranu rodzaju – wspólne dla podglądu i dla nieudanego zapisu."""
        context = _page_context(competition, kind, form, preview=_preview(form, competition))
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)
