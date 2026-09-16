"""Szablony komentarzy recenzenckich: wspólne (koordynatora) i prywatne (recenzenta).

Po co ten moduł istnieje (prośba organizatora): ocena trzydziestu prac z jednego zadania to
w praktyce trzydzieści razy te same trzy zdania. Szablon jest gotowym akapitem **do wstawienia
i poprawienia** – system nigdy nie wstawia go sam i nigdy nie zmienia tego, co recenzent już
napisał. Dzięki temu narzędzie oszczędza pisania, a nie zastępuje oceny.

Dwie drogi zapisu, jeden model (``grading.CommentSnippet``):

- **koordynator** wpisuje szablony przy zadaniu, jednym polem tekstowym, po jednym w wierszu
  (``tytuł;treść``) – dokładnie tak, jak rubrykę (``apps.grading.rubric.parse_criteria_lines``).
  Textarea zamiast formsetu z tego samego powodu, co tam: dodawanie i kasowanie wierszy formsetu
  kosztowałoby wyspę JavaScriptu, a strict CSP nie ma na nią miejsca dla czynności, którą da się
  zrobić jednym polem,
- **recenzent** dopisuje i kasuje własne szablony po jednym, z ekranu oceny – bo pisze je w trakcie
  pracy nad konkretną pracą, a nie przy planowaniu zadania.

Widoczność jest cała w ``snippets_for``: recenzent widzi wspólne i swoje, nigdy cudze prywatne.
"""

from __future__ import annotations

from django.db.models import F, Q
from rest_framework import status as http

from apps.core.api import DomainError
from apps.core.models import audit

from .models import MAX_SNIPPET_LENGTH, CommentSnippet

#: Górna granica liczby szablonów w jednej liście (zadania albo recenzenta). Lista jest czytana
#: z ekranu oceny, obok formularza – pięćdziesiąt pozycji to już nie podpowiedź, tylko druga
#: strona do przewinięcia.
MAX_SNIPPETS = 50


def _bad_request(detail: str, code: str) -> DomainError:
    return DomainError(detail, code, http.HTTP_400_BAD_REQUEST)


def parse_snippet_lines(text: str) -> list[dict]:
    """Zamienia tekst „tytuł;treść” (po jednym szablonie w wierszu) na listę pozycji.

    Bliźniak ``rubric.parse_criteria_lines`` – ta sama konwencja zapisu, bo oba pola stoją na tym
    samym formularzu zadania i koordynator nie ma powodu uczyć się dwóch składni. Średnik rozdziela
    **raz**: treść szablonu wolno pisać ze średnikami, a tytuł to zwykle dwa słowa.

    Rzuca ``ValueError`` z gotowym zdaniem po polsku (numer wiersza w komunikacie – przy dziesięciu
    szablonach „zła wartość” bez wskazania, która, jest zagadką). Na błąd pola zamienia je warstwa
    formularza, tak samo jak przy rubryce.
    """
    items: list[dict] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(";", 1)]
        if len(parts) < 2:
            raise ValueError(
                f"Wiersz {number}: brakuje średnika. Zapis to „tytuł;treść”, "
                "np. „Brak jednostek;Wynik jest poprawny, ale nie podałeś jednostek.”."
            )
        if not parts[0]:
            raise ValueError(f"Wiersz {number}: brakuje tytułu szablonu.")
        if not parts[1]:
            raise ValueError(f"Wiersz {number}: brakuje treści szablonu.")
        items.append({"title": parts[0][:200], "text": parts[1][:MAX_SNIPPET_LENGTH]})
    if len(items) > MAX_SNIPPETS:
        raise ValueError(f"Zadanie może mieć najwyżej {MAX_SNIPPETS} szablonów komentarzy.")
    return items


def format_snippet_lines(snippets) -> str:
    """Odwrotność ``parse_snippet_lines`` – szablony z bazy w postaci wracającej do formularza.

    Znaki końca wiersza w treści zamieniamy na spację: jeden szablon zajmuje w textarei dokładnie
    jeden wiersz, więc wielolinijkowa treść rozerwałaby zapis przy ponownym odczycie.
    """
    return "\n".join(f"{item.title};{' '.join(item.text.split())}" for item in snippets)


def problem_snippets(problem) -> list[CommentSnippet]:
    """Szablony wspólne zadania (autorstwa koordynatora), w ustalonej kolejności."""
    if problem is None:
        return []
    return list(CommentSnippet.objects.filter(problem=problem, owner__isnull=True).order_by("order", "id"))


def snippets_for(problem, member) -> list[CommentSnippet]:
    """Szablony widoczne dla recenzenta przy jednej pracy: **najpierw wspólne, potem własne**.

    Kolejność nie jest kosmetyką. Wspólne szablony są wykładnią komitetu do tego zadania i mają
    stać pierwsze; własne są prywatnym notatnikiem recenzenta i przy dwudziestu pracach z rzędu
    szuka się ich już z pamięci, nie wzrokiem.

    Zakres: szablony **tego** zadania oraz ogólne (``problem IS NULL``), a spośród prywatnych
    wyłącznie należące do pytającego. Cudzy prywatny szablon nie wychodzi stąd nigdy – także dla
    koordynatora, który ogląda ten sam ekran w innej roli.
    """
    if member is None:
        return []
    scope = Q(problem__isnull=True) if problem is None else Q(problem__isnull=True) | Q(problem=problem)
    rows = CommentSnippet.objects.filter(scope).filter(Q(owner__isnull=True) | Q(owner=member))
    # ``owner_id`` rosnąco z ``NULL`` na początku to dokładnie „wspólne, potem własne” – jedno
    # zapytanie zamiast dwóch i sklejania list. ``nulls_first`` podajemy jawnie, bo domyślne
    # ułożenie wartości pustych zależy od bazy, a kolejność jest tu częścią umowy z czytelnikiem.
    return list(rows.order_by(F("owner_id").asc(nulls_first=True), "order", "id"))


def set_problem_snippets(problem, items, *, actor=None, request=None) -> int:
    """Zapisuje wspólne szablony zadania: aktualizuje po kolejności, dopisuje, kasuje nadmiarowe.

    Aktualizacja w miejscu, a nie „skasuj wszystko i utwórz od nowa” – tak samo jak przy rubryce
    (``rubric.set_criteria``). Tutaj chodzi o coś prostszego niż tam: identyfikatory szablonów nie
    wiszą w żadnej recenzji, ale kasowanie i tworzenie od nowa przy każdym zapisie formularza
    zaśmiecałoby tabelę i mieszało kolejność. Wpis audytowy powstaje wyłącznie przy faktycznej
    zmianie – otwarcie i zapisanie formularza bez zmian nie jest zdarzeniem.

    Szablony **prywatne** recenzentów są poza zasięgiem tej funkcji: filtr obejmuje wyłącznie
    ``owner IS NULL``. Koordynator zapisujący zadanie nie może skasować cudzego notatnika.
    """
    existing = problem_snippets(problem)
    before = [{"title": item.title, "text": item.text} for item in existing]
    for index, item in enumerate(items):
        if index < len(existing):
            snippet = existing[index]
            snippet.order = index + 1
            snippet.title = item["title"]
            snippet.text = item["text"]
            snippet.save(update_fields=["order", "title", "text"])
        else:
            CommentSnippet.objects.create(
                problem=problem,
                owner=None,
                order=index + 1,
                title=item["title"],
                text=item["text"],
            )
    for snippet in existing[len(items) :]:
        snippet.delete()

    if before != items:
        audit(actor, "snippet.problem_set", problem, {"from": before, "to": items}, request=request)
    return len(items)


def add_own_snippet(member, title: str, text: str, *, problem=None, request=None) -> CommentSnippet:
    """Dopisuje prywatny szablon recenzenta. Bez zadania = szablon ogólny, na każdą pracę.

    Walidacja jest tutaj, a nie w formularzu Django, bo regułą jest wyłącznie „niepuste i nie
    dłuższe niż limit” plus limit liczby pozycji – a ten ostatni wymaga zapytania do bazy, czyli
    i tak wyszedłby poza formularz. Dwie kopie tej samej walidacji rozjeżdżają się przy pierwszej
    zmianie (patrz ``comparison.add_review_note``, gdzie stoi ta sama decyzja).
    """
    clean_title = (title or "").strip()
    clean_text = (text or "").strip()
    if not clean_title:
        raise _bad_request("Podaj tytuł szablonu.", "SNIPPET_TITLE_REQUIRED")
    if not clean_text:
        raise _bad_request("Podaj treść szablonu.", "SNIPPET_TEXT_REQUIRED")
    owned = CommentSnippet.objects.filter(owner=member)
    if owned.count() >= MAX_SNIPPETS:
        raise _bad_request(
            f"Masz już {MAX_SNIPPETS} własnych szablonów – skasuj któryś, zanim dodasz nowy.",
            "SNIPPET_LIMIT",
        )
    snippet = CommentSnippet.objects.create(
        problem=problem,
        owner=member,
        title=clean_title[:200],
        text=clean_text[:MAX_SNIPPET_LENGTH],
        order=owned.count() + 1,
    )
    # W audycie sam tytuł i długość treści: szablon bywa notatką o konkretnej pracy, a dziennik
    # zdarzeń nie jest miejscem na jej kopię.
    audit(
        getattr(member, "user", None),
        "snippet.added",
        snippet,
        {"title": snippet.title, "length": len(snippet.text)},
        request=request,
    )
    return snippet


def delete_own_snippet(member, snippet: CommentSnippet, *, request=None) -> None:
    """Kasuje **własny** szablon recenzenta. Wspólny albo cudzy to odmowa, nie cicha zgoda.

    Sprawdzenie właściciela jest ostatnią linią obrony – widok i tak filtruje queryset po
    ``owner=member`` i na cudzym szablonie kończy się 404. Reguła stoi jednak przy zapisie,
    bo to ona jest regułą, a nie kształt widoku.
    """
    if snippet.owner_id != getattr(member, "pk", None):
        raise DomainError("To nie jest Twój szablon.", "SNIPPET_NOT_OWNED", http.HTTP_403_FORBIDDEN)
    audit(
        getattr(member, "user", None),
        "snippet.deleted",
        snippet,
        {"title": snippet.title},
        request=request,
    )
    snippet.delete()
