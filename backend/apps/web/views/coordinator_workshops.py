"""Panel koordynatora: obecność na warsztatach i zaświadczenia z warsztatów.

Warsztaty są jedyną częścią olimpiady, w której **serwis nie wie nic sam z siebie**. Terminy są
treścią redakcyjną, zajęcia odbywają się na platformie wideo, a lista obecności powstaje tam,
nie tu. Ten ekran jest więc dokładnie jednym: miejscem, w którym organizator przepisuje (albo
importuje) to, co wie skądinąd, żeby dało się z tego wystawić dokument.

Trzy decyzje o kształcie ekranu:

- tabela **uczestnicy × warsztaty** zamiast osobnej listy na każde zajęcia. Odhaczanie idzie
  zwykle po zakończeniu cyklu i dotyczy kilkunastu kolumn naraz („Kasia była na wszystkich poza
  grudniowymi”), a przeklikanie kilkunastu osobnych list byłoby tą samą pracą razy dwanaście,
- zapis obejmuje **tylko widoczną stronę** (``apps.cms.workshops.save_attendance``): formularz
  przysyła wyłącznie kratki zaznaczone, więc bez ograniczenia zakresu zapisanie drugiej strony
  kasowałoby obecności z pierwszej,
- stronicowanie po stu uczestnikach, bo tabela ma tyle kolumn, ile warsztatów – trzysta wierszy
  razy szesnaście kratek to strona, której przeglądarka na telefonie nie unosi.
"""

from __future__ import annotations

from urllib.parse import quote

from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import Participant
from apps.cms.models import WorkshopAttendance
from apps.cms.workshops import save_attendance, workshop_rows, workshops_page
from apps.competitions.services import current_edition
from apps.core.models import audit
from apps.results.certificates import build_certificates_zip, issue_workshop_certificates
from apps.web.certificate_forms import WorkshopAttendanceImportForm
from apps.web.list_controls import DELETED_PARAM, ListControls
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/workshop_attendance.html"

#: Ilu uczestników na stronie. Sto wierszy to granica, przy której tabela z kilkunastoma kolumnami
#: kratek jeszcze się renderuje płynnie i jeszcze da się ją przejść wzrokiem.
PAGE_SIZE = 100


def _participants(
    competition, edition, query: str, controls: ListControls | None = None
) -> list[Participant]:
    """Uczestnicy z wpisem do dowolnego etapu edycji, przefiltrowani po nazwisku albo kodzie.

    Krąg jest ten sam, co krąg odbiorców zaświadczenia: dokument przypina się do wpisu w edycji,
    więc uczestnik bez wpisu nie miałby na czym go dostać i nie ma po co stać w tabeli.

    Konta usunięte na żądanie (``apps.accounts.anonymised``) są schowane, dopóki ``controls``
    (przełącznik „Pokaż usunięte konta”) ich nie przywróci – obecności nie odhacza się osobie,
    której konta już nie ma, a jej wiersz z pustym nazwiskiem wyglądał w tabeli jak błąd.
    """
    if edition is None:
        return []
    queryset = (
        Participant.objects.for_competition(competition)
        .filter(stage_entries__stage__edition=edition)
        .select_related("user")
        .distinct()
        .order_by("user__last_name", "user__first_name", "public_code")
    )
    query = (query or "").strip()
    if query:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(user__last_name__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(public_code__icontains=query)
        )
    if controls is not None:
        queryset = controls.filter_deleted(queryset, "user")
    return list(queryset)


def _workshops(competition) -> list[dict]:
    """Kolumny tabeli: warsztaty z harmonogramu **tego konkursu**, od najwcześniejszych.

    Stronę wskazuje ``apps.cms.workshops.workshops_page`` – jedno wejście dla panelu i dla części
    informacyjnej. Harmonogram jest treścią redakcyjną, a drzewo stron należy do witryny konkursu,
    więc bez tego zawężenia tabela obecności konkursu A miałaby kolumny z harmonogramu konkursu B
    (a klucze obecności – ``workshop_key`` – przestałyby do czegokolwiek pasować).
    """
    return workshop_rows(workshops_page(competition))


class WorkshopAttendanceView(CoordinatorRequiredMixin, View):
    """``/coordinator/workshops/attendance/`` – tabela obecności: odczyt, zapis i import CSV.

    Wszystko na jednym adresie, bo to jedna czynność w trzech odmianach; ``action`` w formularzu
    rozstrzyga którą. Filtr i numer strony idą w adresie (``?q=…&page=…``), więc po zapisie da się
    wrócić dokładnie tam, gdzie się było – a stan tabeli jest odświeżalny i da się go przesłać.
    """

    def get(self, request):
        return self._render(request, WorkshopAttendanceImportForm())

    def post(self, request):
        if request.POST.get("action") == "import":
            return self._import(request)
        workshops = _workshops(request.competition)
        keys = [row["key"] for row in workshops]
        # Identyfikatory przychodzą z formularza, czyli **od klienta**: zawężamy je do uczestników
        # tego konkursu, zanim serwis cokolwiek zapisze. Ukryte pole ze strony, którą koordynator
        # właśnie widział, nie jest dowodem na to, co przyszło w żądaniu.
        posted = [int(value) for value in request.POST.getlist("participant") if value.isdigit()]
        participant_ids = list(
            Participant.objects.for_competition(request.competition)
            .filter(pk__in=posted)
            .values_list("pk", flat=True)
        )
        marked = set()
        for value in request.POST.getlist("attend"):
            participant_id, _, key = value.partition(":")
            if participant_id.isdigit() and key in keys:
                marked.add((int(participant_id), key))
        result = save_attendance(participant_ids, keys, marked, actor=request.user)
        if result["added"] or result["removed"]:
            audit(
                request.user,
                "workshop_attendance.saved",
                # Wpis audytowy wisi na **edycji**, a nie na uczestniku: zdarzeniem jest zapis
                # jednej strony tabeli, a nie zmiana przy jednej osobie. Liczby zamiast listy
                # nazwisk – audyt ma mówić, co się stało, a nie być drugą kopią danych osobowych.
                current_edition(request.competition) or request.user,
                {"added": result["added"], "removed": result["removed"]},
                request=request,
            )
        messages.success(
            request,
            f"Zapisano obecności: dodano {result['added']}, zdjęto {result['removed']}.",
        )
        return redirect(self._back(request))

    def _import(self, request):
        form = WorkshopAttendanceImportForm(request.POST, request.FILES)
        if not form.is_valid():
            for error in form.errors.get("file", []):
                messages.error(request, error)
            return self._render(request, form, status=400)
        keys = {row["key"] for row in _workshops(request.competition)}
        codes = {code for code, _ in form.rows}
        participants = {
            participant.public_code.upper(): participant.pk
            for participant in Participant.objects.for_competition(request.competition).filter(
                public_code__in=codes
            )
        }
        rows = [
            WorkshopAttendance(
                participant_id=participants[code],
                workshop_key=key,
                created_by=request.user,
            )
            for code, key in form.rows
            if code in participants and key in keys
        ]
        # ``ignore_conflicts``: import po każdych zajęciach dokłada wiersze do tych, które już są,
        # a plik z platformy wideo zwykle zawiera całą listę od początku cyklu.
        WorkshopAttendance.objects.bulk_create(rows, ignore_conflicts=True)
        skipped = len(form.rows) - len(rows)
        messages.success(
            request,
            f"Zaimportowano {len(rows)} wierszy obecności."
            + (f" Pominięto {skipped} – nieznany kod uczestnika albo nieznany warsztat." if skipped else ""),
        )
        return redirect(self._back(request))

    def _back(self, request) -> str:
        """Powrót na tę samą stronę tabeli i z tym samym filtrem – zapis nie gubi miejsca pracy."""
        url = reverse("web:coordinator-workshop-attendance")
        query = request.POST.get("query") or ""
        page = request.POST.get("page") or ""
        # Przełącznik kont usuniętych wraca razem z frazą i stroną – zapis nie zmienia widoku tabeli.
        deleted = request.POST.get(DELETED_PARAM) == "1"
        params = [
            part
            for part in (
                f"q={quote(query)}" if query else "",
                f"page={page}" if page.isdigit() else "",
                f"{DELETED_PARAM}=1" if deleted else "",
            )
            if part
        ]
        return f"{url}?{'&'.join(params)}" if params else url

    def _render(self, request, form, *, status: int = 200):
        edition = current_edition(request.competition)
        query = request.GET.get("q", "")
        controls = ListControls(request, ())
        workshops = _workshops(request.competition)
        participants = _participants(request.competition, edition, query, controls)
        page_number = max(1, int(request.GET.get("page") or 1))
        start = (page_number - 1) * PAGE_SIZE
        visible = participants[start : start + PAGE_SIZE]
        marked = {
            (row.participant_id, row.workshop_key)
            for row in WorkshopAttendance.objects.filter(
                participant_id__in=[participant.pk for participant in visible]
            )
        }
        context = {
            "edition": edition,
            "workshops": workshops,
            "query": query,
            "controls": controls,
            "import_form": form,
            "page_number": page_number,
            "page_count": max(1, -(-len(participants) // PAGE_SIZE)),
            "total": len(participants),
            "rows": [
                {
                    "participant": participant,
                    "cells": [
                        {"workshop": workshop, "checked": (participant.pk, workshop["key"]) in marked}
                        for workshop in workshops
                    ],
                }
                for participant in visible
            ],
        }
        return TemplateResponse(request, TEMPLATE, context, status=status)


class IssueWorkshopCertificatesView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/workshops/certificates/`` – zaświadczenia z warsztatów i paczka ZIP.

    Odbiorcą jest każdy, kto ma odhaczoną **choć jedną** obecność i wpis do etapu w tej edycji.
    Progu „co najmniej połowa zajęć” tu nie ma z premedytacją: dokument wylicza konkretne tematy
    i daty, więc sam mówi, ile tego było – a próg byłby regułą, której nie ustanowił żaden regulamin.
    """

    def post(self, request):
        edition = current_edition(request.competition)
        if edition is None:
            messages.error(request, "Nie ustawiono bieżącej edycji – nie ma dla czego wystawiać zaświadczeń.")
            return redirect(reverse("web:coordinator-workshop-attendance"))
        certificates = issue_workshop_certificates(edition, actor=request.user, request=request)
        if not certificates:
            messages.error(
                request,
                "Nikt nie ma jeszcze odhaczonej obecności na warsztatach w tej edycji.",
            )
            return redirect(reverse("web:coordinator-workshop-attendance"))
        archive = build_certificates_zip(certificates)
        response = FileResponse(archive.stream, content_type="application/zip", as_attachment=True)
        response["Content-Disposition"] = 'attachment; filename="zaswiadczenia-warsztaty.zip"'
        return response
