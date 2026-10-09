"""Zdublowane konta uczestników w panelu koordynatora (ACC-DUP-01): lista, zbiorcze usunięcie, CSV.

Osobny moduł obok ``coordinator_accounts.py`` z tego samego powodu, co tamten obok ``coordinator.py``:
pełne strony z własnym ekranem potwierdzenia. Reguła (co jest grupą, co kandydatem) mieszka
w serwisie ``apps.accounts.duplicates``, a usunięcie – w ``apps.accounts.profile``. Widoki wyłącznie
orkiestrują: pojedyncze „Usuń” prowadzi na **istniejący** ekran potwierdzenia
(``CoordinatorAccountDeleteView`` z ``?back=duplicates``), a zbiorcze idzie przez
``delete_candidates``, który i tak woła ten sam ``delete_account_by_coordinator``.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.duplicates import CANDIDATE, MAX_BULK_IDS, delete_candidates, find_duplicate_groups
from apps.accounts.models import Participant
from apps.core.exports import Dataset, csv_response
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.views.coordinator_accounts import account_status

LIST_TEMPLATE = "web/coordinator/accounts_duplicates.html"
CONFIRM_TEMPLATE = "web/coordinator/accounts_duplicates_delete.html"

#: Wartość ``back`` rozpoznawana przez ekran usuwania konta. Zamknięta lista zamiast adresu
#: powrotu w parametrze – parametr z adresem to gotowe otwarte przekierowanie.
BACK_TO_DUPLICATES = "duplicates"


def _with_status(report):
    """Etykieta stanu konta (aktywne / nieaktywowane / zablokowane) – ta sama, co na liście kont."""
    for group in report.groups:
        for account in group.accounts:
            account.status_label = account_status(account.user)
    return report


class CoordinatorDuplicatesView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/duplicates/`` – grupy zdublowanych kont z sugestią przy każdym koncie."""

    def get(self, request):
        report = _with_status(find_duplicate_groups(request.competition))
        context = {
            "report": report,
            "candidates": report.candidates,
            "back": BACK_TO_DUPLICATES,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


def _requested_ids(request) -> list[int] | None:
    """Identyfikatory kont z formularza, bez powtórzeń, w kolejności podania. ``None`` – śmieci."""
    ids: list[int] = []
    for raw in request.POST.getlist("account"):
        try:
            value = int(raw)
        except TypeError, ValueError:
            return None
        if value not in ids:
            ids.append(value)
    return ids


class CoordinatorDuplicatesDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/accounts/duplicates/delete/`` – zbiorcze usunięcie kandydatów, dwa kroki.

    Pierwszy POST (bez ``confirm``) pokazuje **dokładną listę** kont z polem wyboru przy każdym,
    ze stanem przeliczonym w tej chwili; drugi (``confirm=1``) usuwa zaznaczone. Oba kroki są POST-em,
    bo lista identyfikatorów w adresie GET zostawałaby w historii przeglądarki i w logach serwera.

    Identyfikator konta spoza profili uczestnika **tego** konkursu kończy żądanie 404 bez usunięcia
    czegokolwiek – tak samo jak pojedynczy ekran usuwania (§ 3.6: kod odpowiedzi nie potwierdza
    istnienia cudzego konta).
    """

    def post(self, request):
        ids = _requested_ids(request)
        if ids is None or len(ids) > MAX_BULK_IDS:
            return HttpResponseBadRequest("Nieprawidłowa lista kont.")
        back = redirect(reverse("web:coordinator-duplicates"))
        if not ids:
            messages.info(request, "Nie wybrano żadnego konta.")
            return back
        mine = set(
            Participant.objects.filter(competition=request.competition, user_id__in=ids).values_list(
                "user_id", flat=True
            )
        )
        if mine != set(ids):
            raise Http404("Nie ma takiego konta w tym konkursie.")

        if not request.POST.get("confirm"):
            return self._confirm(request, ids)

        result = delete_candidates(request.competition, ids, actor=request.user, request=request)
        removed = len(result.deleted) + len(result.anonymised)
        if removed:
            text = f"Usunięto kont: {removed}."
            if result.anonymised:
                text += (
                    f" W tym zanonimizowanych (mają wpis do treningu – ślad udziału zostaje): "
                    f"{len(result.anonymised)}."
                )
            messages.success(request, text)
        if result.skipped:
            messages.warning(
                request,
                f"Pominięto kont: {len(result.skipped)} – przestały spełniać warunki kandydata "
                f"(np. ktoś się zalogował): {', '.join(result.skipped)}.",
            )
        return back

    def _confirm(self, request, ids: list[int]):
        report = _with_status(find_duplicate_groups(request.competition, with_email_suspects=False))
        by_user = {account.user.pk: (group, account) for group in report.groups for account in group.accounts}
        rows = []
        for user_id in ids:
            group, account = by_user.get(user_id, (None, None))
            rows.append({"user_id": user_id, "group": group, "account": account})
        context = {
            "rows": rows,
            "eligible": [row for row in rows if row["account"] and row["account"].suggestion == CANDIDATE],
            "skipped": [row for row in rows if not row["account"] or row["account"].suggestion != CANDIDATE],
        }
        return TemplateResponse(request, CONFIRM_TEMPLATE, context)


EXPORT_HEADER = [
    "Grupa",
    "Imię",
    "Nazwisko",
    "Szkoła",
    "Kod publiczny",
    "E-mail",
    "Założone",
    "Ostatnie logowanie",
    "Stan",
    "Etapy zawodów",
    "Trening",
    "Prace",
    "Zaświadczenie",
    "Inne role",
    "Sugestia",
    "Uzasadnienie",
]


class CoordinatorDuplicatesExportView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/accounts/duplicates/export.csv`` – te same grupy w arkuszu.

    Eksport danych osobowych jest zdarzeniem w audycie (``account.duplicates_exported``) – z liczbą
    grup i wierszy, bez żadnej danej osobowej, tak jak pozostałe eksporty panelu.
    """

    def get(self, request):
        report = find_duplicate_groups(request.competition, with_email_suspects=False)
        rows = [
            [
                group.number,
                account.user.first_name,
                account.user.last_name,
                account.participant.school,
                account.participant.public_code,
                account.user.email,
                account.user.date_joined,
                account.user.last_login,
                account_status(account.user),
                ", ".join(account.competition_stages),
                ", ".join(account.training_stages),
                account.works,
                account.certificate,
                account.other_roles,
                account.suggestion_label,
                account.reason,
            ]
            for group in report.groups
            for account in group.accounts
        ]
        audit(
            request.user,
            "account.duplicates_exported",
            request.competition,
            {"groups": report.people, "rows": len(rows)},
            request=request,
        )
        return csv_response(
            Dataset(
                header=EXPORT_HEADER,
                rows=iter(rows),
                count=len(rows),
                title="Zdublowane konta",
                filename="zdublowane-konta",
            )
        )
