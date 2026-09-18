"""Ekran „Słownik placówek” ``/coordinator/institutions/`` – wykaz własny organizatora (§ 1.3.3).

Słownik własny jest **listą kontrahentów organizatora**: uczelnie partnerskie, ośrodki, kluby,
szkoły zagraniczne z jego programu. Ten ekran jest jedynym miejscem, w którym widać go w całości,
i jedyną drogą, którą wchodzi plik z tym wykazem.

**Dlaczego ekran jest za flagą ``custom_school_directory``.** Dopóki flaga jest wyłączona,
``CustomInstitution`` nie jest pytana ani razu – ani przez wyszukiwarkę rejestracji (§ 5.6), ani
przez import grupowy. Ekran pozwalający wtedy wgrać wykaz obiecywałby podpowiedzi, których nikt nie
zobaczy. Adres przy wyłączonej fladze daje **404**, a nie 403 (§ 2.1); rolę sprawdza wcześniej
i osobno ``CoordinatorRequiredMixin``, więc uczestnik dostaje 403 niezależnie od stanu flagi.

**Flaga to nie wszystko – i dlatego na tym ekranie stoi karta profilu rejestracji.** O tym, czy
wykaz naprawdę karmi formularz, rozstrzyga **koniunkcja**: flaga konkursu **i** pole
``RegistrationProfile.allow_custom_directory`` (``apps.accounts.services.custom_directory_enabled``).
Wgrany wykaz przy odznaczonym polu jest tabelą, do której nikt nie zagląda – karta mówi to wprost
jednym zdaniem, zamiast zostawiać koordynatora ze zgadywaniem, czemu podpowiedzi milczą.

**Import ma dwa kroki i drugi z nich wgrywa ten sam plik jeszcze raz.** Podgląd liczy wszystko
(``dry_run=True``) i nie zapisuje ani jednego wiersza; zatwierdzenie jest osobnym żądaniem z tym
samym plikiem. Przenoszenie wierszy między krokami przez koszyk w sesji byłoby tu gorsze, a nie
lepsze: pięć tysięcy wierszy nie mieści się w ciasteczku, wykaz placówek to dane organizatora,
a podpisany koszyk w sesji znaczyłby ich kopię w drugim magazynie – przy pliku, który i tak leży
na dysku koordynatora i wgrywa się raz na sezon.

**Audyt pisze ten widok**, bo słownik nie ma pod sobą serwisu domenowego poza samym importem
(ten pisze ``custom_directory.imported`` sam, ``apps.schools.custom``). Do wpisu idą
**identyfikatory i nazwy pól** – ani jednej nazwy placówki, ani jednej miejscowości: lista
kontrahentów organizatora nie ma czego robić w tabeli, którą czyta operator platformy.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.generic import View

from apps.accounts.models import Participant, RegistrationProfile
from apps.accounts.services import (
    CUSTOM_DIRECTORY_FLAG,
    REGISTRATION_PROFILE_FLAG,
    custom_directory_enabled,
    registration_profile,
)
from apps.core.api import DomainError
from apps.core.exports import Dataset, csv_response
from apps.core.models import audit
from apps.core.text import fold
from apps.schools.custom import (
    COLUMNS,
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    CustomInstitution,
    import_custom_institutions,
)
from apps.schools.models import InstitutionType
from apps.web.coordinator_forms import (
    CustomInstitutionForm,
    CustomInstitutionImportForm,
    RegistrationProfileForm,
)
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/institutions.html"
FORM_TEMPLATE = "web/coordinator/institutions_form.html"
IMPORT_TEMPLATE = "web/coordinator/institutions_import.html"
PROFILE_TEMPLATE = "web/coordinator/registration_profile.html"

#: Przełącznik, który włącza ekran słownika. Ta sama nazwa, którą czyta wyszukiwarka
#: (``apps.accounts.services.custom_directory_enabled``) i import (``apps.schools.custom``) –
#: napis powtórzony tutaj dałby ekran włączany flagą, której nikt poza nim nie czyta.
FEATURE = CUSTOM_DIRECTORY_FLAG

#: Przełącznik ekranu profilu rejestracji. Osobna flaga i osobny ekran, bo to osobna decyzja:
#: konkurs może mieć własny wykaz placówek, nie zmieniając ani jednego pytania formularza.
PROFILE_FEATURE = REGISTRATION_PROFILE_FLAG

#: Ile wierszy słownika na stronę. Wykaz bywa pięciotysięczny (``MAX_ROWS``), a ekran służy do
#: odnalezienia **jednej** placówki wyszukiwarką, nie do przeglądania bazy po kolei – ta sama
#: reguła i ten sam rząd wielkości, co na liście kont.
PAGE_SIZE = 50

#: Filtr stanu w adresie. Pusty znaczy „wszystkie” i jest wartością domyślną: wykaz z wygaszonymi
#: wierszami jest stanem faktycznym, a ekran diagnostyczny ma pokazywać stan faktyczny.
STATE_CHOICES = (("", "wszystkie"), ("active", "aktywne"), ("inactive", "wyłączone"))


class InstitutionScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów słownika: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma własny słownik placówek. Inaczej 404.

        Konkurs bierzemy z ``request.competition``, a nie z identyfikatora w adresie – dzięki temu
        nie ma tu adresu, pod którym dałoby się obejrzeć cudzy wykaz. Sprawdzenie stoi w metodzie
        widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej ``RoleRequiredMixin.dispatch``:
        anonim i uczestnik mają dostać 302 albo 403, **zanim** odpowiedź zdradzi stan przełącznika
        tego konkursu.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie ma własnego słownika placówek – ekran jest wyłączony.")
        return competition

    def institution_or_404(self, competition, pk: int) -> CustomInstitution:
        """Placówka **tego konkursu** albo 404 – zakres wychodzi z querysetu, nie z widoku."""
        return get_object_or_404(CustomInstitution.objects.for_competition(competition), pk=pk)


# --- lista ----------------------------------------------------------------------------------


def _filters(request) -> dict:
    """Zawężenie listy z adresu: fraza, rodzaj placówki, stan. Śmieci nie są błędem człowieka."""
    kind = request.GET.get("type") or ""
    state = request.GET.get("state") or ""
    return {
        "query": (request.GET.get("q") or "").strip(),
        "kind": kind if kind in InstitutionType.values else "",
        "state": state if state in {value for value, _ in STATE_CHOICES} else "",
    }


def _queryset(competition, filters: dict):
    """Wiersze słownika po zawężeniu – jedno zapytanie, niezależnie od liczby filtrów.

    Fraza szuka po ``search_text``, czyli po **tej samej** kolumnie i tą samą regułą składania
    znaków, co wyszukiwarka rejestracji (``apps.schools.custom.search_custom_institutions``).
    Dwie reguły znaczyłyby ekran, na którym koordynator nie znajduje placówki, którą uczestnik
    znajduje w formularzu – albo odwrotnie, i to jest gorszy wariant: wiersz niewidoczny tutaj
    jest wierszem, którego nie da się poprawić.
    """
    rows = CustomInstitution.objects.for_competition(competition)
    if filters["kind"]:
        rows = rows.filter(institution_type=filters["kind"])
    if filters["state"] == "active":
        rows = rows.filter(is_active=True)
    elif filters["state"] == "inactive":
        rows = rows.filter(is_active=False)
    for token in fold(filters["query"]).split():
        # ``contains``, nie ``icontains`` – obie strony porównania są już złożone.
        rows = rows.filter(search_text__contains=token)
    return rows.order_by("name", "id")


def _participant_counts(competition) -> dict[int, int]:
    """Ilu uczestników wskazuje każdą placówkę – **jedno** zapytanie grupujące na cały ekran.

    Liczymy po ``Participant.custom_institution_ref``, czyli po tej samej kolumnie, którą trzyma
    ``PROTECT``: liczba w wierszu jest odpowiedzią na pytanie „czy ten wiersz wolno ruszyć”,
    a nie ozdobą. Zapytanie jest jedno i nie rośnie z liczbą wierszy na stronie – pętla po
    placówkach dawałaby pięćdziesiąt zapytań na odsłonę.

    Bez zawężenia do edycji, tak samo jak przy komitecie na ekranie regionów i z tego samego
    powodu: profil należy do **konkursu**, a nie do rocznika, i przeżywa kolejne edycje.
    """
    rows = (
        Participant.objects.for_competition(competition)
        .filter(custom_institution_ref__isnull=False)
        .values("custom_institution_ref")
        .annotate(total=Count("id"))
    )
    return {row["custom_institution_ref"]: row["total"] for row in rows}


def _profile_card(competition) -> dict:
    """Karta „Profil rejestracji” – co ten konkurs pyta i czy w ogóle korzysta ze słownika.

    Czytamy przez ``registration_profile`` (jedyne wejście, § 1.3.4), więc karta pokazuje reguły
    **obowiązujące**, a nie zawartość wiersza: przy wyłączonej fladze ``institution_types``
    formularz rejestracji chodzi dzisiejszymi regułami niezależnie od tego, co ktoś zapisał
    w bazie, i karta ma mówić to samo, co formularz.

    ``directory_active`` jest osobną wartością i osobnym zdaniem na ekranie, bo pochodzi z innej
    koniunkcji (``custom_directory_enabled``): wykaz wgrany przy odznaczonym polu profilu jest
    tabelą, do której nie zagląda ani wyszukiwarka, ani import grupowy.
    """
    profile = registration_profile(competition)
    labels = dict(InstitutionType.choices)
    low, high = profile.grade_range()
    return {
        "profile": profile,
        "types": [labels.get(value, value) for value in profile.institution_types()],
        "grade_min": low,
        "grade_max": high,
        "directory_active": custom_directory_enabled(competition),
        "profile_editable": competition.has_feature(PROFILE_FEATURE),
    }


def _render_list(request, competition, form: CustomInstitutionForm, *, status: int = 200):
    """Ekran słownika. Wspólny dla wejścia i dla każdego nieudanego dopisania."""
    filters = _filters(request)
    rows = _queryset(competition, filters)
    paginator = Paginator(rows, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    counts = _participant_counts(competition)
    query = {
        name: value
        for name, value in (("q", filters["query"]), ("type", filters["kind"]), ("state", filters["state"]))
        if value
    }
    context = {
        "rows": [{"institution": row, "participants": counts.get(row.pk, 0)} for row in page.object_list],
        "page_obj": page,
        "paginator": paginator,
        "query": filters["query"],
        "kind": filters["kind"],
        "state": filters["state"],
        "state_choices": STATE_CHOICES,
        "type_choices": InstitutionType.choices,
        "filter_query": urlencode(query),
        "total": CustomInstitution.objects.for_competition(competition).count(),
        "form": form,
        "card": _profile_card(competition),
        "max_rows": MAX_ROWS,
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


class InstitutionListView(InstitutionScreenMixin, View):
    """``GET /coordinator/institutions/`` – wykaz, wyszukiwarka, liczby dowiązań, karta profilu."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return _render_list(request, competition, CustomInstitutionForm(competition=competition))


class InstitutionCreateView(InstitutionScreenMixin, View):
    """``POST /coordinator/institutions/new/`` – jedna placówka wpisana ręcznie.

    Ręczny wpis jest tu dlatego, że wykaz bywa jedną pozycją: konkurs z trzema uczelniami
    partnerskimi nie ma po co układać pliku CSV. Reguły są te same, co przy imporcie – sprawdza
    je ``full_clean()`` modelu, wołane przez ``ModelForm``, a nie osobna kopia warunków.
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        form = CustomInstitutionForm(request.POST, competition=competition)
        if not form.is_valid():
            return _render_list(request, competition, form, status=400)
        institution = form.save(commit=False)
        institution.competition = competition
        institution.save()
        audit(
            request.user, "custom_institution.created", institution, {"id": institution.pk}, request=request
        )
        messages.success(request, "Placówka została dodana do słownika.")
        return redirect(reverse("web:coordinator-institutions"))


class InstitutionEditView(InstitutionScreenMixin, View):
    """``GET|POST /coordinator/institutions/<id>/`` – zmiana jednej placówki.

    Osobna strona, a nie formularz w wierszu tabeli: pól jest dziewięć, a przy dziewięciu polach
    w wierszu nie da się powiedzieć, co dokładnie zapisuje przycisk (ta sama reguła, co na liście
    kont).

    Do audytu idą **nazwy zmienionych pól**, bez wartości: nazwa placówki i jej adres są danymi
    kontrahenta organizatora, a wpis audytowy ma odpowiadać na pytanie „kto i co ruszył”, a nie
    trzymać drugiej kopii wykazu.
    """

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        institution = self.institution_or_404(competition, pk)
        form = CustomInstitutionForm(instance=institution, competition=competition)
        return self._render(request, competition, institution, form)

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        institution = self.institution_or_404(competition, pk)
        form = CustomInstitutionForm(request.POST, instance=institution, competition=competition)
        if not form.is_valid():
            return self._render(request, competition, institution, form, status=400)
        changed = sorted(form.changed_data)
        saved = form.save()
        audit(
            request.user,
            "custom_institution.updated",
            saved,
            {"id": saved.pk, "fields": changed},
            request=request,
        )
        messages.success(request, "Placówka została zapisana.")
        return redirect(reverse("web:coordinator-institutions"))

    def _render(self, request, competition, institution, form, *, status: int = 200):
        context = {
            "institution": institution,
            "form": form,
            "participants": _participant_counts(competition).get(institution.pk, 0),
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class InstitutionStateView(InstitutionScreenMixin, View):
    """Wspólne ciało dwóch czynności: wiersz znika z podpowiedzi albo do nich wraca.

    Dwa adresy i dwie klasy, a nie jeden adres z polem „stan”: rozróżnianie po nazwie wciśniętego
    przycisku zależy od tego, czy przeglądarka ją przyśle, a przy wysyłce klawiaturą nie zawsze
    przysyła (ta sama reguła, co na ekranie regionów i integracji). Ciało jest jedno, bo różnica
    sprowadza się do wartości kolumny i do zdania w komunikacie.

    Zapis i wpis audytowy padają **tylko przy faktycznej zmianie**: powtórzone kliknięcie tego
    samego przycisku nie ma czego zapisać, a drugi wpis o niczym zaśmiecałby ślad, po którym
    później szuka się jednej zmiany.

    Do audytu idzie **sam identyfikator**. Nazwa placówki jest daną kontrahenta organizatora,
    a zdarzeniem jest „wiersz o tym numerze przestał być aktywny”, nie „zniknął Uniwersytet X”.
    """

    #: Docelowy stan kolumny ``is_active``. Klasa pochodna podaje go i komunikat – i to jest cała
    #: różnica między wyłączeniem a włączeniem.
    target_state: bool = False
    message = ""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        institution = self.institution_or_404(competition, pk)
        if institution.is_active is not self.target_state:
            institution.is_active = self.target_state
            institution.save(update_fields=["is_active"])
            audit(
                request.user,
                "custom_institution.updated",
                institution,
                {"id": institution.pk},
                request=request,
            )
        messages.success(request, self.message)
        return redirect(reverse("web:coordinator-institutions"))


class InstitutionDeactivateView(InstitutionStateView):
    """``POST /coordinator/institutions/<id>/deactivate/`` – placówka znika z podpowiedzi.

    Droga wycofania wiersza, którym ktoś już się zarejestrował. Wiersz **zostaje**: profile
    z poprzednich lat mają dalej wskazywać placówkę, w której wtedy startowały, a ``PROTECT``
    na ``Participant.custom_institution_ref`` i tak nie pozwoliłby go skasować.
    """

    target_state = False
    message = "Placówka nie pojawi się już w podpowiedziach. Profile, które ją mają, zostają bez zmian."


class InstitutionActivateView(InstitutionStateView):
    """``POST /coordinator/institutions/<id>/activate/`` – powrót placówki do podpowiedzi."""

    target_state = True
    message = "Placówka wróciła do podpowiedzi."


# --- import ---------------------------------------------------------------------------------


def _source_label(upload, given: str) -> str:
    """Napis „skąd ten wiersz” – wpisany przez koordynatora albo nazwa pliku z dzisiejszą datą.

    Wartość domyślna jest odpowiedzią na pytanie, które koordynator zadaje sobie za rok („skąd
    się tu wziął ten ośrodek?”), a nie ozdobą. Napis, a nie klucz obcy do tabeli importów –
    historia wgrań jest w audycie (``custom_directory.imported``).
    """
    text = (given or "").strip()
    if text:
        return text[:120]
    return f"{getattr(upload, 'name', '') or 'plik.csv'}, {timezone.localdate():%Y-%m-%d}"[:120]


class InstitutionImportView(InstitutionScreenMixin, View):
    """``GET|POST /coordinator/institutions/import/`` – wgranie wykazu z podglądem przed zapisem.

    Trzy stany i wszystkie trzy są tym samym adresem:

    - **wejście** (``GET``) – formularz, lista kolumn i limity,
    - **podgląd** (``POST`` bez potwierdzenia) – ``dry_run=True``: policzone „założę / poprawię /
      bez zmian / wygaszę” i lista wierszy do poprawienia **z numerami linii**. Nie zapisuje
      niczego i nie pisze wpisu audytowego, bo nic się nie stało,
    - **zatwierdzenie** (``POST`` z potwierdzeniem) – ten sam plik wgrany drugi raz, tym razem
      z zapisem. Audyt (``custom_directory.imported``) pisze serwis, a nie ten widok.

    Ponowne wgranie pliku zamiast koszyka w sesji jest decyzją, nie uproszczeniem – uzasadnienie
    stoi w docstringu modułu. Podgląd liczy stan bazy **na moment podglądu**, więc równoległa
    zmiana wykazu przez drugiego koordynatora między krokami zmieni liczniki; zatwierdzenie
    pokazuje wtedy własne, prawdziwe liczby i to one idą do audytu.
    """

    def get(self, request):
        competition = self.competition_or_404(request)
        return self._render(request, competition, CustomInstitutionImportForm())

    def post(self, request):
        competition = self.competition_or_404(request)
        form = CustomInstitutionImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return self._render(request, competition, form, status=400)
        upload = form.cleaned_data["file"]
        confirmed = form.cleaned_data["confirm"]
        try:
            report = import_custom_institutions(
                competition,
                upload,
                actor=request.user,
                request=request,
                dry_run=not confirmed,
                deactivate_missing=form.cleaned_data["deactivate_missing"],
                source_label=_source_label(upload, form.cleaned_data["source_label"]),
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, competition, form, status=exc.status_code)
        if not confirmed:
            return self._render(request, competition, self._confirm_form(form), report=report)
        messages.success(
            request,
            f"Wykaz wgrany: nowych {report.created}, poprawionych {report.updated}, "
            f"bez zmian {report.unchanged}, wygaszonych {report.deactivated}, "
            f"pominiętych {report.skipped}.",
        )
        return redirect(reverse("web:coordinator-institutions"))

    @staticmethod
    def _confirm_form(form: CustomInstitutionImportForm) -> CustomInstitutionImportForm:
        """Formularz kroku drugiego: te same ustawienia, plik do wskazania jeszcze raz.

        Formularz jest **niezwiązany**, bo pole pliku nie ma jak zapamiętać wgranego pliku –
        przeglądarka nie pozwala wstawić go z powrotem i jest to zabezpieczenie, a nie
        niedoróbka. Reszta ustawień jedzie wartościami początkowymi, żeby kratka „wygaś
        nieobecne” nie odznaczyła się po drodze do zatwierdzenia.
        """
        return CustomInstitutionImportForm(
            initial={
                "deactivate_missing": form.cleaned_data["deactivate_missing"],
                "source_label": form.cleaned_data["source_label"],
                "confirm": True,
            }
        )

    def _render(self, request, competition, form, *, report=None, status: int = 200):
        context = {
            "form": form,
            "report": report,
            "columns": COLUMNS,
            "types": InstitutionType.choices,
            "max_rows": MAX_ROWS,
            "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            "total": CustomInstitution.objects.for_competition(competition).count(),
        }
        return TemplateResponse(request, IMPORT_TEMPLATE, context, status=status)


# --- pliki ----------------------------------------------------------------------------------


def _export_row(row: CustomInstitution) -> list:
    """Wiersz pliku: mapa „kolumna → wartość”, ułożona w kolejności ``COLUMNS``.

    Mapa, a nie lista wartości wpisana w kolejności kolumn: kolejność opisuje ``COLUMNS``
    (``apps.schools.custom``) i to ona ma rozstrzygać, żeby wyeksportowany plik dał się wgrać
    z powrotem bez ani jednej poprawki nagłówka.
    """
    values = {
        "name": row.name,
        "external_id": row.external_id,
        "institution_type": row.institution_type,
        "country": row.country,
        "region": row.region_code,
        "city": row.city,
        "postal_code": row.postal_code,
        "address": row.address,
    }
    return [values[column.key] for column in COLUMNS]


class InstitutionExportView(InstitutionScreenMixin, View):
    """``GET /coordinator/institutions/export.csv`` – dzisiejszy wykaz w formacie importu.

    Eksport bierze **to samo zawężenie**, co lista: fraza, rodzaj i stan jadą tymi samymi
    parametrami adresu. Plik „wszystko” jest więc jednym kliknięciem, a plik „same uczelnie
    z Krakowa” – tym samym kliknięciem po zawężeniu ekranu, bez drugiego formularza.

    Plik wolno poprawić w arkuszu i wgrać z powrotem: nagłówki są nagłówkami importu, separatorem
    jest średnik (``apps.core.exports``), a upsert rozpozna wiersze po identyfikatorze albo po
    parze nazwa + miejscowość. Jedna rzecz wymaga uwagi i mówi o niej ekran: wiersz **wygaszony**
    też jest w pliku, a obecność w pliku przywraca go do aktywnych.
    """

    def get(self, request):
        competition = self.competition_or_404(request)
        rows = _queryset(competition, _filters(request))
        dataset = Dataset(
            header=[column.label for column in COLUMNS],
            rows=(_export_row(row) for row in rows.iterator()),
            count=rows.count(),
            title="Słownik placówek",
            filename="placowki",
        )
        return csv_response(dataset)


class InstitutionTemplateView(InstitutionScreenMixin, View):
    """``GET /coordinator/institutions/template.csv`` – sam nagłówek, do wypełnienia w arkuszu.

    Bez wiersza przykładowego i to jest decyzja: przykład zostawiony w pliku przez nieuwagę
    wjechałby do wykazu jako placówka, której nikt nie zamawiał. Przykład stoi na ekranie importu,
    gdzie niczego nie da się nim zepsuć.
    """

    def get(self, request):
        self.competition_or_404(request)
        dataset = Dataset(
            header=[column.label for column in COLUMNS],
            rows=iter(()),
            count=0,
            title="Wzór pliku",
            filename="placowki-wzor",
        )
        return csv_response(dataset)


# --- profil rejestracji ---------------------------------------------------------------------


def _profile_row(competition) -> RegistrationProfile:
    """Wiersz profilu tego konkursu albo **niezapisany** wiersz z domyślnymi.

    Brak wiersza znaczy „jak dziś” (§ 1.3.4) i migracja nie zakłada go nikomu, więc ekran musi
    umieć pokazać konfigurację, której w bazie jeszcze nie ma. Wiersz powstaje dopiero przy
    pierwszym zapisie – i dopiero wtedy konkurs przestaje być „jak dziś”.
    """
    return RegistrationProfile.objects.for_competition(competition).first() or RegistrationProfile(
        competition=competition
    )


class RegistrationProfileView(CoordinatorRequiredMixin, View):
    """``GET|POST /coordinator/registration-profile/`` – co konkurs pyta przy rejestracji.

    Ekran jest za flagą ``institution_types``, tak samo jak cały ``RegistrationProfile``: przy
    wyłączonej fladze ``registration_profile()`` oddaje wartości domyślne **niezależnie od
    zawartości wiersza**, więc formularz pozwalający coś zmienić obiecywałby zmianę, której
    ``/register/`` nie zobaczy.

    Do audytu (``registration_profile.updated``) idą **same nazwy zmienionych pól**. Wartości nie,
    i nie jest to przesada: profil rejestracji mówi, o co konkurs pyta uczestników, a nie co
    którykolwiek z nich odpowiedział – ale wpis audytowy ma być czytelny dla operatora platformy,
    który o konfigurację cudzego konkursu pytać nie musi.
    """

    def competition_or_404(self, request):
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(PROFILE_FEATURE):
            raise Http404("Ten konkurs nie ma własnego profilu rejestracji – ekran jest wyłączony.")
        return competition

    def get(self, request):
        competition = self.competition_or_404(request)
        profile = _profile_row(competition)
        return self._render(request, competition, RegistrationProfileForm(instance=profile))

    def post(self, request):
        competition = self.competition_or_404(request)
        profile = _profile_row(competition)
        form = RegistrationProfileForm(request.POST, instance=profile)
        if not form.is_valid():
            return self._render(request, competition, form, status=400)
        changed = sorted(form.changed_data)
        saved = form.save(commit=False)
        saved.competition = competition
        saved.save()
        audit(request.user, "registration_profile.updated", saved, {"fields": changed}, request=request)
        messages.success(request, "Profil rejestracji został zapisany.")
        return redirect(reverse("web:coordinator-registration-profile"))

    def _render(self, request, competition, form, *, status: int = 200):
        context = {
            "form": form,
            "card": _profile_card(competition),
            "directory_screen": competition.has_feature(FEATURE),
        }
        return TemplateResponse(request, PROFILE_TEMPLATE, context, status=status)
