"""Ekran „Kategorie” ``/coordinator/categories/`` – podział startujących na osobne rankingi.

Kategoria jest **danymi konkursu**, a nie edycji: „szkoła podstawowa / ponadpodstawowa” ma przeżyć
rocznik. Przypisanie jest natomiast per **wpis do etapu** (``StageEntry.category``), bo uczeń
zmienia klasę między edycjami, a wiersz sprzed roku ma pokazywać kategorię, w której faktycznie
startował. Stąd kształt tego ekranu: definicje na górze są konkursu, przycisk „przypisz
automatycznie” na dole dotyczy **bieżącej edycji** i niczego poza nią nie rusza.

**Dlaczego ekran jest za flagą ``categories``.** Dopóki flaga jest wyłączona, ranking jest jeden,
wiersz tabeli wyników nie ma klucza ``category``, a snapshot nie ma nowego pola
(``apps.results.services``). Ekran, który pozwalałby wtedy zakładać kategorie i przypisywać je
wpisom, obiecywałby podział, którego nikt by nie zobaczył. Adres przy wyłączonej fladze daje
**404**, a nie 403 (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1); rolę sprawdza wcześniej i osobno
``CoordinatorRequiredMixin``, więc uczestnik dostaje 403 niezależnie od stanu flagi.

**Czego tu nie ma.** Przypisania kategorii pojedynczemu uczestnikowi: to jest pole wpisu do etapu
i ma je karta uczestnika, a nie ekran słownika. I nie ma „przypisz ponownie wszystkim”: działanie
automatu **uzupełnia puste**, a nie nadpisuje – kategoria wpisana ręcznie jest decyzją komitetu
(odwołanie, przeniesienie), a automat liczący z klasy nic o niej nie wie.

Audyt pisze ten widok, bo kategorie nie mają pod sobą serwisu domenowego (są konfiguracją, a nie
czynnością na danych uczestników). Do wpisu idą **kody i liczby** – ani jednego nazwiska, ani
jednego pseudonimu, także przy przypisaniu masowym, gdzie kuszące byłoby wypisanie, komu co
przypadło.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.models import Category, StageEntry
from apps.competitions.services import current_edition
from apps.core.models import audit
from apps.results.services import CATEGORIES_FLAG
from apps.web.coordinator_forms import CategoryForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/categories.html"
FORM_TEMPLATE = "web/coordinator/categories_form.html"

#: Przełącznik, który włącza ten ekran – ta sama stała, którą czyta ranking i snapshot
#: (``apps.results.services``). Napis w dwóch miejscach dałby ekran włączony flagą, której nikt
#: nie czyta przy liczeniu tabeli wyników.
FEATURE = CATEGORIES_FLAG


class CategoryScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów kategorii: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma włączone kategorie. Inaczej 404.

        Konkurs bierzemy z ``request.competition``, a nie z identyfikatora w adresie – dzięki temu
        nie ma tu adresu, pod którym dałoby się wskazać cudzy słownik. Sprawdzenie stoi
        w metodzie widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej
        ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo 403, **zanim**
        odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie dzieli startujących na kategorie – ekran jest wyłączony.")
        return competition

    def category_or_404(self, competition, pk: int) -> Category:
        """Kategoria **tego konkursu** albo 404 – zakres wychodzi z querysetu, nie z widoku."""
        return get_object_or_404(Category.objects.for_competition(competition), pk=pk)


def _apply_model_errors(form, error: ValidationError) -> None:
    """Przenosi komunikaty ``full_clean()`` pod pola formularza.

    Reguły domenowe (kolejność klas, unikalność kodu w konkursie) stoją w modelu i nie są
    powtórzone w formularzu – ta sama decyzja, co przy ``ConsentDefinitionForm``. Komunikat musi
    jednak stanąć pod właściwym polem; komunikat o polu, którego formularz nie ma (``competition``),
    idzie do błędów ogólnych.
    """
    for field, texts in error.message_dict.items():
        form.add_error(field if field in form.fields else None, texts)


def _counts(competition, edition) -> dict[int, int]:
    """Ile wpisów bieżącej edycji ma każdą kategorię – **jedno** zapytanie na całą tabelę.

    Liczba jest częścią odpowiedzi, a nie ozdobą: kategoria z zerem wpisów po przypisaniu
    automatycznym znaczy, że jej zakres klas nie łapie nikogo – a to jest literówka, której
    inaczej nie widać do chwili ogłoszenia wyników.
    """
    if edition is None:
        return {}
    rows = (
        StageEntry.objects.for_competition(competition)
        .filter(stage__edition=edition, category__isnull=False)
        .values("category_id")
        .annotate(total=Count("id"))
    )
    return {row["category_id"]: row["total"] for row in rows}


def _render_list(request, competition, form: CategoryForm, *, status: int = 200):
    """Lista kategorii konkursu. Wspólna dla wejścia i dla nieudanego dopisania."""
    edition = current_edition(competition)
    counts = _counts(competition, edition)
    categories = list(Category.objects.for_competition(competition))
    context = {
        "edition": edition,
        "rows": [{"category": row, "entries": counts.get(row.pk, 0)} for row in categories],
        "form": form,
        # Bez ani jednej reguły zakresu klas automat nie ma czego policzyć – przycisk byłby wtedy
        # obietnicą bez pokrycia, więc ekran mówi o tym wprost zamiast go chować.
        "has_grade_rules": any(row.has_grade_rule for row in categories),
    }
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


def _category_diff(category: Category) -> dict:
    """Komplet pól kategorii do audytu – kody i liczby, ani jednego wiersza o człowieku."""
    return {
        "code": category.code,
        "name": category.name,
        "position": category.position,
        "grade_min": category.grade_min,
        "grade_max": category.grade_max,
        "is_active": category.is_active,
    }


class CategoryListView(CategoryScreenMixin, View):
    """``GET /coordinator/categories/`` – kategorie konkursu i liczba wpisów bieżącej edycji."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return _render_list(request, competition, CategoryForm())


class CategoryCreateView(CategoryScreenMixin, View):
    """``POST /coordinator/categories/`` – nowa kategoria konkursu."""

    def post(self, request):
        competition = self.competition_or_404(request)
        form = CategoryForm(request.POST)
        if not form.is_valid():
            return _render_list(request, competition, form, status=400)
        category = form.save(commit=False)
        category.competition = competition
        try:
            category.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return _render_list(request, competition, form, status=400)
        category.save()
        audit(request.user, "category.created", category, _category_diff(category), request=request)
        messages.success(request, f"Kategoria „{category.name}” została dodana.")
        return redirect(reverse("web:coordinator-categories"))


class CategoryEditView(CategoryScreenMixin, View):
    """``GET|POST /coordinator/categories/<id>/`` – zmiana jednej kategorii.

    Kod wolno zmienić, choć jest tożsamością: kategoria nie jest jeszcze nigdzie zapisana
    napisem – wpisy wskazują ją kluczem obcym, a reguły przejścia też. Zmiana kodu jest więc
    poprawką w regulaminie, a nie podmianą obiektu; więz unikalności w konkursie zostaje.
    """

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        category = self.category_or_404(competition, pk)
        return self._render(request, category, CategoryForm(instance=category))

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        category = self.category_or_404(competition, pk)
        before = _category_diff(category)
        form = CategoryForm(request.POST, instance=category)
        if not form.is_valid():
            return self._render(request, category, form, status=400)
        saved = form.save(commit=False)
        try:
            saved.full_clean()
        except ValidationError as error:
            _apply_model_errors(form, error)
            return self._render(request, category, form, status=400)
        saved.save()
        audit(
            request.user,
            "category.updated",
            saved,
            {"before": before, "after": _category_diff(saved)},
            request=request,
        )
        messages.success(request, f"Kategoria „{saved.name}” została zapisana.")
        return redirect(reverse("web:coordinator-categories"))

    @staticmethod
    def _render(request, category, form, *, status: int = 200):
        return TemplateResponse(request, FORM_TEMPLATE, {"category": category, "form": form}, status=status)


class CategoryDeleteView(CategoryScreenMixin, View):
    """``POST /coordinator/categories/<id>/delete/`` – usunięcie kategorii bez ani jednego wpisu.

    Kategoria z wpisami jest chroniona przez bazę (``PROTECT`` przy ``StageEntry.category``)
    i tak ma zostać: skasowanie jej zabrałoby dokumentację odbytych zawodów. Drogą wycofania jest
    odznaczenie „aktywna”, więc odmowa mówi o niej wprost zamiast kończyć się pięciuset.
    """

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        category = self.category_or_404(competition, pk)
        diff = _category_diff(category)
        try:
            category.delete()
        except ProtectedError:
            messages.error(
                request,
                f"Kategoria „{category.name}” ma przypisane wpisy do etapów i nie da się jej "
                "usunąć. Odznacz „aktywna”, żeby zniknęła z list – wyniki zostaną nietknięte.",
            )
            return redirect(reverse("web:coordinator-categories"))
        audit(request.user, "category.deleted", competition, diff, request=request)
        messages.success(request, f"Kategoria „{diff['name']}” została usunięta.")
        return redirect(reverse("web:coordinator-categories"))


class CategoryAssignView(CategoryScreenMixin, View):
    """``POST /coordinator/categories/assign/`` – kategoria z klasy ucznia dla bieżącej edycji.

    **Uzupełnia puste, nie nadpisuje.** Kategoria wpisana ręcznie jest decyzją (przeniesienie,
    odwołanie), a automat liczący z ``Participant.grade`` nic o niej nie wie – gdyby nadpisywał,
    jedno kliknięcie cofałoby ustalenia komitetu bez śladu na ekranie.

    Regułę „która kategoria dla tej klasy” czyta ``Category.auto_for_grade`` – ta sama funkcja,
    którą woła rejestracja i import grupowy, żeby wszystkie trzy drogi dawały tę samą odpowiedź.
    Wynik pamiętamy per klasa, bo klas jest kilkanaście, a wpisów bywa kilka tysięcy.

    Wpisy drużynowe (``participant`` puste) automat pomija: klasa jest cechą ucznia, a nie
    drużyny, i zgadywanie jej z pierwszego z brzegu członka byłoby wpisaniem kategorii za komisję.

    Zapis idzie wpis po wpisie przez ``full_clean()``, a nie ``bulk_update``: to jest czynność
    wykonywana raz na sezon i lepiej, żeby kosztowała kilka zapytań więcej, niż żeby była jedynym
    miejscem w panelu, w którym wiersz trafia do bazy bez walidacji.
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        edition = current_edition(competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – nie ma czego przypisywać.")
            return redirect(reverse("web:coordinator-categories"))
        entries = (
            StageEntry.objects.for_competition(competition)
            .filter(stage__edition=edition, category__isnull=True, participant__isnull=False)
            .select_related("participant")
            .order_by("id")
        )
        resolved: dict[int | None, Category | None] = {}
        assigned: dict[str, int] = {}
        skipped = 0
        with transaction.atomic():
            for entry in entries:
                grade = entry.participant.grade
                if grade not in resolved:
                    resolved[grade] = Category.auto_for_grade(competition, grade)
                category = resolved[grade]
                if category is None:
                    skipped += 1
                    continue
                entry.category = category
                entry.full_clean(validate_unique=False)
                entry.save(update_fields=["category"])
                assigned[category.code] = assigned.get(category.code, 0) + 1
        total = sum(assigned.values())
        audit(
            request.user,
            "category.auto_assigned",
            edition,
            {"edition_id": edition.pk, "assigned": total, "skipped": skipped, "by_code": assigned},
            request=request,
        )
        if total:
            messages.success(
                request,
                f"Przypisano kategorię {total} wpisom bieżącej edycji. "
                f"Bez kategorii zostało {skipped} wpisów – dla ich klasy żaden zakres nie pasuje.",
            )
        else:
            messages.info(
                request,
                "Nie przypisano ani jednej kategorii: wszystkie wpisy mają już kategorię albo "
                "żaden zakres klas nie pasuje do klas startujących.",
            )
        return redirect(reverse("web:coordinator-categories"))
