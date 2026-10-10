"""Nieaktywne konta uczestników w panelu koordynatora (ACC-DUP-02): lista, usuwanie, CSV.

Prośba organizatora z 10.10.2026: „sekcja nieaktywne konta – te konta, na które nikt się nie logował,
i tu pozwól wybrać liczbę dni lub nigdy”. Filtr, liczniki i warunki usuwania liczy serwis
``apps.accounts.inactive``; usunięcie idzie wspólną drogą ``account_cleanup.delete_accounts`` →
``delete_account_by_coordinator``. Widoki wyłącznie orkiestrują i mówią o usunięciu tym samym językiem,
co ekran duplikatów (pomocniki z ``coordinator_duplicates``).

Cały stan listy jest w adresie (filtry, strona, „zaznacz wszystkie”), a każdy POST niesie filtry
w ukrytych polach: serwer sprawdza je ponownie **w chwili usuwania** i wraca na tę samą listę. Adres
powrotu składamy z filtra po walidacji (``InactiveFilter.querystring``), nigdy z tekstu formularza.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.accounts.account_cleanup import MAX_BULK_IDS, collect_facts
from apps.accounts.inactive import (
    ACTIVATION_CHOICES,
    DAY_PRESETS,
    DEFAULT_JOINED_DAYS,
    DEFAULT_LOGIN_DAYS,
    JOINED_PRESETS,
    MAX_DAYS,
    SKIP_REASON,
    InactiveFilter,
    bulk_blocker,
    counters,
    delete_inactive,
    delete_inactive_account,
    facts_in_order,
    inactive_participants,
)
from apps.accounts.models import Participant
from apps.core.exports import Dataset, csv_response
from apps.core.models import audit
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.views.coordinator_accounts import account_status
from apps.web.views.coordinator_duplicates import (
    confirmed,
    participant_here_or_404,
    prepare_row_delete,
    report_bulk,
    report_single,
    requested_ids,
)

LIST_TEMPLATE = "web/coordinator/accounts_inactive.html"
CONFIRM_TEMPLATE = "web/coordinator/accounts_inactive_delete.html"

#: Wierszy na stronę. Więcej niż na liście kont (50), bo ten ekran służy do przeglądania i zaznaczania
#: hurtem; mniej niż limit POST-a (``MAX_BULK_IDS``), więc „zaznacz wszystkie na stronie” zawsze się mieści.
INACTIVE_PER_PAGE = 100


def _list_url(flt: InactiveFilter, page: str | None = None) -> str:
    url = f"{reverse('web:coordinator-inactive')}?{flt.querystring()}"
    if page and page.isdigit():
        url += f"&page={int(page)}"
    return url


def _rows(competition, participant_ids, actor) -> list:
    """Fakty kont w kolejności listy z doklejonym stanem, formularzem „Usuń” i blokadą zbiorczego."""
    rows = facts_in_order(competition, participant_ids)
    for account in rows:
        account.status_label = account_status(account.user)
        account.bulk_blocker = bulk_blocker(account)
        prepare_row_delete(account, actor)
    return rows


class CoordinatorInactiveView(CoordinatorRequiredMixin, View):
    """``/coordinator/accounts/inactive/`` – konta uczestników, na które nikt się nie logował."""

    def get(self, request):
        flt, errors = InactiveFilter.parse(request.GET)
        context = {
            "errors": errors,
            "flt": flt,
            "day_presets": DAY_PRESETS,
            "joined_presets": JOINED_PRESETS,
            "activation_choices": ACTIVATION_CHOICES,
            "max_days": MAX_DAYS,
            # Wartości pól formularza: to, co koordynator wpisał (także błędne – żeby mógł poprawić),
            # a przy braku parametru – wartość domyślna filtra.
            "form": {
                "login": request.GET.get("login") or "never",
                "days": request.GET.get("days") or DEFAULT_LOGIN_DAYS,
                "joined": request.GET.get("joined") or DEFAULT_JOINED_DAYS,
                "activation": request.GET.get("activation") or "",
            },
        }
        if flt is None:
            return TemplateResponse(request, LIST_TEMPLATE, context)

        profiles = inactive_participants(request.competition, flt)
        paginator = Paginator(profiles.values_list("pk", flat=True), INACTIVE_PER_PAGE)
        page = paginator.get_page(request.GET.get("page"))
        context.update(
            {
                "counts": counters(profiles),
                "rows": _rows(request.competition, list(page.object_list), request.user),
                "page_obj": page,
                "paginator": paginator,
                "filter_query": flt.querystring(),
                "hidden": [*flt.params(), ("page", str(page.number))],
                "select_all": request.GET.get("zaznacz") == "1",
            }
        )
        return TemplateResponse(request, LIST_TEMPLATE, context)


class CoordinatorInactiveDeleteOneView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/accounts/inactive/<pk>/delete/`` – „Usuń” w wierszu (ACC-DUP-02 § 3.4).

    Potwierdzenie i znacznik logowania jak na ekranie duplikatów; dodatkowo filtr z ukrytych pól –
    konto musi **nadal** go spełniać. Konto spoza profili uczestnika tego konkursu → 404.
    """

    def post(self, request, pk: int):
        participant_here_or_404(request.competition, pk)
        flt, _errors = InactiveFilter.parse(request.POST)
        if flt is None:
            return HttpResponseBadRequest("Nieprawidłowy filtr.")
        url = _list_url(flt, request.POST.get("page"))
        if not confirmed(request, pk):
            messages.error(request, "Usunięcie wymaga potwierdzenia przyciskiem „Tak, usuń konto…”.")
            return redirect(url)
        result = delete_inactive_account(
            request.competition,
            pk,
            flt,
            seen_login=request.POST.get("seen_login", ""),
            actor=request.user,
            request=request,
        )
        report_single(
            request,
            result,
            skip_reason="konto nie spełnia już filtra albo ktoś się na nie zalogował po wyświetleniu listy",
        )
        return redirect(url)


class CoordinatorInactiveDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/accounts/inactive/delete/`` – zbiorcze usunięcie, dwa kroki (jak duplikaty).

    Pierwszy POST (bez ``confirm``) pokazuje dokładną listę z polem wyboru przy każdym koncie i listę
    „zostaną pominięte” z powodem; drugi (``confirm=1``) usuwa zaznaczone, sprawdzając filtr i brak
    innych ról przy każdym koncie osobno pod blokadą wiersza. Identyfikator spoza profili uczestnika
    tego konkursu → 404 (nic nie usunięte), za długa lista albo zły filtr → 400.
    """

    def post(self, request):
        ids = requested_ids(request)
        flt, _errors = InactiveFilter.parse(request.POST)
        if ids is None or len(ids) > MAX_BULK_IDS or flt is None:
            return HttpResponseBadRequest("Nieprawidłowa lista kont albo filtr.")
        back = _list_url(flt, request.POST.get("page"))
        if not ids:
            messages.info(request, "Nie wybrano żadnego konta.")
            return redirect(back)
        mine = dict(
            Participant.objects.filter(competition=request.competition, user_id__in=ids)
            .exclude_anonymised()
            .values_list("user_id", "pk")
        )
        if set(mine) != set(ids):
            raise Http404("Nie ma takiego konta w tym konkursie.")

        if not request.POST.get("confirm"):
            return self._confirm(request, ids, mine, flt)

        result = delete_inactive(request.competition, ids, flt, actor=request.user, request=request)
        report_bulk(request, result, skip_reason=SKIP_REASON)
        return redirect(back)

    def _confirm(self, request, ids: list[int], mine: dict, flt: InactiveFilter):
        matching = set(
            inactive_participants(request.competition, flt)
            .filter(user_id__in=ids)
            .values_list("user_id", flat=True)
        )
        facts = collect_facts(request.competition, list(mine.values()))
        eligible, skipped = [], []
        for user_id in ids:
            account = facts.get(mine[user_id])
            if account is None:
                continue
            account.status_label = account_status(account.user)
            blocker = bulk_blocker(account)
            if user_id not in matching:
                skipped.append((account, "nie spełnia już filtra (np. ktoś się zalogował)"))
            elif blocker:
                skipped.append((account, blocker))
            else:
                eligible.append(account)
        context = {
            "eligible": eligible,
            "skipped": skipped,
            "flt": flt,
            "hidden": [*flt.params(), ("page", request.POST.get("page", ""))],
            "back_url": _list_url(flt, request.POST.get("page")),
        }
        return TemplateResponse(request, CONFIRM_TEMPLATE, context)


EXPORT_HEADER = [
    "Kod publiczny",
    "Imię",
    "Nazwisko",
    "E-mail",
    "Szkoła",
    "Założone",
    "Ostatnie logowanie",
    "Stan",
    "Z importu",
    "Etapy zawodów",
    "Trening",
    "Prace",
    "Zaświadczenie",
    "Opiekun szkolny wskazany",
    "Inne role",
]


class CoordinatorInactiveExportView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/accounts/inactive/export.csv?<filtry>`` – wszystkie wiersze filtra (nie strona).

    Audyt ``account.inactive_exported`` z liczbą wierszy i wartościami filtra – bez danych osobowych,
    jak pozostałe eksporty panelu. Fakty dociągamy podzapytaniem (``values("pk")``), żeby nie wklejać
    tysięcy identyfikatorów w cztery zapytania.
    """

    def get(self, request):
        flt, _errors = InactiveFilter.parse(request.GET)
        if flt is None:
            return HttpResponseBadRequest("Nieprawidłowy filtr.")
        now = timezone.now()
        profiles = inactive_participants(request.competition, flt, now=now)
        facts = collect_facts(request.competition, profiles.values("pk"))
        ordered = [facts[pk] for pk in profiles.values_list("pk", flat=True) if pk in facts]
        rows = [
            [
                account.participant.public_code,
                account.user.first_name,
                account.user.last_name,
                account.user.email,
                account.participant.school,
                account.user.date_joined,
                account.user.last_login,
                account_status(account.user),
                bool(account.participant.invited_at),
                ", ".join(account.competition_stages),
                ", ".join(account.training_stages),
                account.works,
                account.certificate,
                bool(account.participant.supervisor_email),
                ", ".join(account.other_role_labels),
            ]
            for account in ordered
        ]
        audit(
            request.user,
            "account.inactive_exported",
            request.competition,
            {"rows": len(rows), **flt.as_audit()},
            request=request,
        )
        return csv_response(
            Dataset(
                header=EXPORT_HEADER,
                rows=iter(rows),
                count=len(rows),
                title="Nieaktywne konta",
                filename="nieaktywne-konta",
            )
        )
