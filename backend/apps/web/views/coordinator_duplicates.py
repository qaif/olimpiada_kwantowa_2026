"""Zdublowane konta uczestników w panelu koordynatora (ACC-DUP-01, ACC-DUP-02): lista, usuwanie, CSV.

Osobny moduł obok ``coordinator_accounts.py`` z tego samego powodu, co tamten obok ``coordinator.py``:
pełne strony z własnym ekranem potwierdzenia. Reguła (co jest grupą, co kandydatem) mieszka
w serwisie ``apps.accounts.duplicates``, a usunięcie – w ``apps.accounts.profile`` (przez wspólne
``apps.accounts.account_cleanup.delete_accounts``). Widoki wyłącznie orkiestrują:

- „Usuń” w wierszu (ACC-DUP-02 § 2) – ``<details>`` z formularzem POST w komórce tabeli; wartość
  potwierdzenia niesie sam przycisk „Tak, usuń konto …”, a serwer przelicza warunki w chwili
  usuwania (``delete_duplicate_account``),
- zbiorcze – ``delete_candidates`` z ekranem potwierdzenia z dokładną listą.

Pomocniki ``requested_ids``, ``confirmed``, ``prepare_row_delete``, ``report_single``
i ``report_bulk`` są wspólne z ekranem „Nieaktywne konta” (``coordinator_inactive``): oba ekrany
mają mówić o usunięciu tym samym językiem.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.account_cleanup import MAX_BULK_IDS, AccountFacts, BulkResult
from apps.accounts.duplicates import (
    CANDIDATE,
    KEEP,
    delete_candidates,
    delete_duplicate_account,
    find_duplicate_groups,
)
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


def _with_status(report, actor=None):
    """Stan konta (ta sama trójka, co na liście kont) i – przy ``actor`` – dane formularza „Usuń”."""
    for group in report.groups:
        for account in group.accounts:
            account.status_label = account_status(account.user)
            if actor is not None:
                prepare_row_delete(account, actor)
                if account.suggestion == KEEP:
                    account.delete_warnings.insert(
                        0, "To jedyne używane konto tej osoby w grupie – zostaną wyłącznie nieużywane kopie."
                    )
    return report


# --- wspólne z ekranem „Nieaktywne konta” ----------------------------------------------------------


def prepare_row_delete(account: AccountFacts, actor) -> None:
    """Dokleja do konta ``deletable``, ``delete_warnings`` i ``delete_effect`` – treść „Usuń” w wierszu.

    ``deletable`` powtarza regułę serwisu (konto chronione, własne konto) **wyłącznie po to, żeby
    nie rysować przycisku, którego serwis i tak nie przepuści** – rozstrzyga
    ``delete_account_by_coordinator``. Ostrzeżenia mówią, co usunięcie zabiera: ekran dopuszcza
    usunięcie konta używanego, więc musi to powiedzieć przed kliknięciem, a nie po fakcie.
    """
    account.deletable = not account.protected and account.user.pk != actor.pk
    warnings = []
    if account.logged_in:
        warnings.append("Konto logowało się – ktoś z niego korzysta.")
    if account.competition_stages:
        warnings.append(f"Ma wpis do etapu zawodów: {', '.join(account.competition_stages)}.")
    if account.works:
        warnings.append(f"Ma oddane prace: {account.works}.")
    if account.certificate:
        warnings.append(f"Ma zaświadczenie o statusie ucznia ({account.certificate}).")
    if account.other_roles:
        warnings.append(
            f"Ma inne role: {', '.join(account.other_role_labels)} – konto jest wspólne, "
            "usunięcie zabierze też je."
        )
    account.delete_warnings = warnings
    account.delete_effect = (
        "Konto ma wpis do etapu albo prace, więc zostanie zanonimizowane: dane osobowe znikną, "
        "pseudonimowy ślad udziału zostanie."
        if account.will_be_anonymised
        else "Konto zniknie w całości, a adres e-mail zwolni się do rejestracji."
    )


def confirmed(request, user_id: int) -> bool:
    """Czy POST niesie potwierdzenie **tego** konta – wartość przycisku „Tak, usuń konto …”."""
    return request.POST.get("confirm") == str(user_id)


def participant_here_or_404(competition, user_id: int) -> Participant:
    """Profil uczestnika (bez anonimizacji) tego konkursu albo 404 – kod nie zdradza cudzych kont."""
    participant = (
        Participant.objects.filter(competition=competition, user_id=user_id).exclude_anonymised().first()
    )
    if participant is None:
        raise Http404("Nie ma takiego konta w tym konkursie.")
    return participant


def report_single(request, result: BulkResult, *, skip_reason: str) -> None:
    """Komunikat po usunięciu jednego konta z wiersza – ten sam na obu ekranach."""
    if result.anonymised:
        messages.success(
            request,
            f"Konto {result.anonymised[0]} zostało zanonimizowane – dane osobowe usunięte, ślad udziału "
            "w zawodach zostaje.",
        )
    elif result.deleted:
        messages.success(
            request,
            f"Konto {result.deleted[0]} zostało usunięte w całości. Adres zwolnił się do rejestracji.",
        )
    elif result.skipped:
        label = result.skipped[0]
        messages.warning(
            request, f"Konto {label} nie zostało usunięte – {result.reasons.get(label, skip_reason)}."
        )
    else:
        messages.info(request, "Tego konta już nie ma.")


def report_bulk(request, result: BulkResult, *, skip_reason: str) -> None:
    """Komunikat po usunięciu zbiorczym: ile usunięto (w tym zanonimizowano), ile pominięto i dlaczego."""
    if result.removed:
        text = f"Usunięto kont: {result.removed}."
        if result.anonymised:
            text += (
                " W tym zanonimizowanych (mają wpis do etapu albo prace – ślad udziału zostaje): "
                f"{len(result.anonymised)}."
            )
        messages.success(request, text)
    if result.skipped:
        details = ", ".join(
            f"{label} ({result.reasons[label]})" if label in result.reasons else label
            for label in result.skipped
        )
        messages.warning(request, f"Pominięto kont: {len(result.skipped)} – {skip_reason}: {details}.")


def requested_ids(request) -> list[int] | None:
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


class CoordinatorDuplicatesView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/duplicates/`` – grupy zdublowanych kont z sugestią przy każdym koncie."""

    def get(self, request):
        report = _with_status(find_duplicate_groups(request.competition), actor=request.user)
        context = {
            "report": report,
            "candidates": report.candidates,
            "back": BACK_TO_DUPLICATES,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


#: Powód pominięcia przy usunięciu z wiersza duplikatów – warunki ``delete_duplicate_account``.
SINGLE_SKIP_REASON = (
    "zmieniło się od wyświetlenia ekranu (ktoś się na nie zalogował albo osoba nie ma już innego "
    "konta w grupie); sprawdź listę jeszcze raz"
)


class CoordinatorDuplicateDeleteOneView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/accounts/duplicates/<pk>/delete/`` – „Usuń” w wierszu (ACC-DUP-02 § 2).

    Bez osobnego ekranu: potwierdzeniem jest przycisk „Tak, usuń konto <kod>” w rozwiniętym
    ``<details>`` – jego wartość (``confirm=<id>``) jest jedynym miejscem, z którego POST ją dostaje.
    Brak potwierdzenia = komunikat i nic nie usunięte. Powrót do kotwicy grupy (``#grupa-<n>``,
    ``n`` jako liczba – nigdy adres z formularza).
    """

    def post(self, request, pk: int):
        participant_here_or_404(request.competition, pk)
        url = reverse("web:coordinator-duplicates")
        group = request.POST.get("group", "")
        if group.isdigit():
            url += f"#grupa-{int(group)}"
        if not confirmed(request, pk):
            messages.error(request, "Usunięcie wymaga potwierdzenia przyciskiem „Tak, usuń konto…”.")
            return redirect(url)
        result = delete_duplicate_account(
            request.competition,
            pk,
            seen_login=request.POST.get("seen_login", ""),
            actor=request.user,
            request=request,
        )
        report_single(request, result, skip_reason=SINGLE_SKIP_REASON)
        return redirect(url)


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
        ids = requested_ids(request)
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
        report_bulk(
            request, result, skip_reason="przestały spełniać warunki kandydata (np. ktoś się zalogował)"
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
