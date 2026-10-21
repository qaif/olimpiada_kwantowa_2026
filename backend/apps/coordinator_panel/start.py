"""Start panelu koordynatora (UX-PANEL-04 § 2, D4–D5): sprawy, terminy, liczby konkursu, skróty.

Start stoi pod tym samym adresem i w tym samym widoku, co dotychczasowy pulpit
(``apps.web.views.coordinator.CoordinatorDashboardView``) – ten moduł **dokłada** kontekst, a niczego
z dotychczasowego nie zabiera (``attention``, ``edition``, ``stage_rows`` i karty etapów zostają).

Koszt jest jedną z twardych reguł panelu (``apps/core/tests/query_budgets.py``) i dlatego liczby idą
pakietami w pamięci podręcznej **per konkurs** – jak liczniki menu (``coordinator_nav.counters_cache_key``):

- ``attention`` (60 s): komunikaty zaplanowane i nieudane (jedno ``aggregate``), zaświadczenia o statusie
  ucznia do weryfikacji (jedno ``COUNT``, tylko za flagą), najbliższe warsztaty (strona „Warsztaty”),
- ``duplicates`` (10 min): liczba grup zdublowanych kont – grupowanie idzie w Pythonie po profilach
  konkursu (``apps.accounts.duplicates``), więc to jedyny licznik, którego koszt rośnie z danymi; dziesięć
  minut to rytm, w jakim takie grupy w ogóle powstają,
- ``summary`` (5 min): uczestnicy i szkoły (jedno ``aggregate``), uczestnicy edycji, aktywny komitet.

Klucz z identyfikatorem konkursu – ten sam powód, co przy licznikach menu: jeden klucz dla instalacji
byłby wyciekiem liczb jednego konkursu do panelu drugiego.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Count, Min, Q
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.web.coordinator_nav import attention_counters, resolve

from . import services, shell

ATTENTION_SECONDS = 60
SUMMARY_SECONDS = 300
DUPLICATES_SECONDS = 600

#: Okno „najbliższych terminów”. Miesiąc, bo to horyzont planowania koordynatora (komunikat o terminie
#: idzie zwykle tydzień–dwa wcześniej), a dalsze daty i tak stoją na kartach etapów niżej.
DEADLINE_HORIZON_DAYS = 30
DEADLINE_LIMIT = 8
#: Nieudane wysyłki liczymy z ostatnich 30 dni – starsza awaria jest historią, a nie sprawą na dziś.
FAILED_WINDOW_DAYS = 30


def _key(kind: str, competition) -> str:
    return f"coordinator-panel:{kind}:{getattr(competition, 'pk', None) or 'none'}"


def invalidate(competition) -> None:
    """Zapomnij pakiety Startu konkursu – w testach i po zmianach, przy których minuta przeszkadza."""
    cache.delete_many([_key(kind, competition) for kind in ("attention", "summary", "duplicates")])


def _cached(kind: str, competition, seconds: int, compute):
    key = _key(kind, competition)
    value = cache.get(key)
    if value is None:
        try:
            value = compute()
        except DatabaseError:  # pragma: no cover - baza bez migracji którejś z tabel
            return None
        cache.set(key, value, seconds)
    return value


# --- pakiety ---------------------------------------------------------------------------------------------


def _attention_bundle(competition, edition) -> dict:
    from apps.accounts.models import BroadcastStatus, MessageBroadcast

    now = timezone.now()
    broadcasts = MessageBroadcast.objects.for_competition(competition).aggregate(
        scheduled=Count("pk", filter=Q(status=BroadcastStatus.SCHEDULED)),
        next_scheduled=Min("scheduled_for", filter=Q(status=BroadcastStatus.SCHEDULED)),
        failed=Count(
            "pk",
            filter=Q(status=BroadcastStatus.FAILED, created_at__gte=now - timedelta(days=FAILED_WINDOW_DAYS)),
        ),
    )
    upcoming_broadcasts = []
    if broadcasts["scheduled"]:
        upcoming_broadcasts = list(
            MessageBroadcast.objects.for_competition(competition)
            .filter(
                status=BroadcastStatus.SCHEDULED,
                scheduled_for__lte=now + timedelta(days=DEADLINE_HORIZON_DAYS),
            )
            .order_by("scheduled_for", "pk")
            .values("subject", "scheduled_for")[:DEADLINE_LIMIT]
        )
    student_status = None
    from apps.student_status.models import CertificateStatus, StudentStatusCertificate
    from apps.student_status.models import enabled as student_status_enabled

    if edition is not None and student_status_enabled(competition):
        student_status = StudentStatusCertificate.objects.filter(
            edition=edition, is_current=True, status=CertificateStatus.PENDING
        ).count()
    return {
        "scheduled": broadcasts["scheduled"] or 0,
        "next_scheduled": broadcasts["next_scheduled"],
        "failed": broadcasts["failed"] or 0,
        "upcoming_broadcasts": upcoming_broadcasts,
        "student_status": student_status,
        "workshops": _workshops(competition),
    }


def _workshops(competition) -> dict:
    from apps.cms.workshops import upcoming_workshops, workshops_page

    page = workshops_page(competition)
    if page is None:
        return {"page_id": None, "rows": []}
    rows = upcoming_workshops(page, limit=DEADLINE_LIMIT)
    return {
        "page_id": page.pk,
        "rows": [{"topic": row["topic"], "date": row["date_value"], "time": row["time"]} for row in rows],
    }


def _duplicates(competition) -> int:
    from apps.accounts.duplicates import find_duplicate_groups

    return len(find_duplicate_groups(competition, with_email_suspects=False).groups)


def _summary(competition, edition) -> dict:
    """Liczby konkursu: dwa zapytania niezależnie od liczby uczestników.

    Profile, szkoły i uczestnicy edycji – jednym ``aggregate`` (``distinct`` przy każdej liczbie, bo złączenie
    z zapisami do etapów mnoży wiersze profilu); aktywny komitet – drugim. Szkoła to pozycja wykazu SIO,
    placówka ze słownika organizatora albo – dla wpisu spoza wykazów – nazwa bez rozróżniania wielkości liter.
    """
    from apps.accounts.models import CommitteeMember, CommitteeStatus, Participant

    # Bez bieżącej edycji nie ma „uczestników tej edycji” – liczby nie dokładamy, zamiast liczyć zero w SQL-u.
    extra = (
        {"in_edition": Count("pk", distinct=True, filter=Q(stage_entries__stage__edition=edition))}
        if edition is not None
        else {}
    )
    totals = (
        Participant.objects.filter(competition=competition)
        .exclude_anonymised()
        .aggregate(
            participants=Count("pk", distinct=True),
            registry=Count("school_ref", distinct=True),
            custom=Count("custom_institution_ref", distinct=True),
            free=Count(
                Lower("school"),
                distinct=True,
                filter=Q(school_ref__isnull=True, custom_institution_ref__isnull=True) & ~Q(school=""),
            ),
            **extra,
        )
    )
    committee = (
        CommitteeMember.objects.for_competition(competition).filter(status=CommitteeStatus.ACTIVE).count()
    )
    return {
        "participants": totals["participants"] or 0,
        "schools": (totals["registry"] or 0) + (totals["custom"] or 0) + (totals["free"] or 0),
        "in_edition": totals.get("in_edition") or 0,
        "committee": committee,
        "computed_at": timezone.now(),
    }


# --- składanie Startu --------------------------------------------------------------------------------------


def _row(key, value, label, hint, url_names, *, args=(), query="", fragment="", icon="alert") -> dict:
    url = resolve(url_names, args) or ""
    if url and query:
        url = f"{url}?{query}"
    if url and fragment:
        url = f"{url}#{fragment}"
    return {"key": key, "value": value, "label": label, "hint": hint, "url": url, "icon": icon}


#: Ikony kafli dotychczasowych spraw (``coordinator_pages.attention_rows``) – po kluczu wiersza.
ATTENTION_ICONS = {
    "moderation": "scale",
    "activations": "user",
    "committee": "committee",
    "issues": "alert",
    "tickets": "help",
    "appeals": "shield",
    "reviews": "clock",
}


def _extra_rows(competition, bundle: dict, duplicates: int | None) -> list[dict]:
    """Sprawy, których dotychczasowy pulpit nie liczył (D4) – w kształcie wierszy ``attention_rows``."""
    counters = attention_counters(competition=competition)
    rows = [
        _row(
            "scheduled",
            bundle["scheduled"],
            _("Zaplanowane wysyłki"),
            _("Komunikaty czekające na termin wysyłki."),
            ("web:coordinator-messages",),
            fragment="zaplanowane",
            icon="calendar",
        ),
        _row(
            "failed",
            bundle["failed"],
            _("Nieudane wysyłki"),
            _("Komunikaty z ostatnich 30 dni, których nie udało się wysłać."),
            ("web:coordinator-messages",),
            fragment="wyslane",
            icon="mail",
        ),
    ]
    if bundle["student_status"] is not None:
        rows.append(
            _row(
                "student_status",
                bundle["student_status"],
                _("Zaświadczenia do weryfikacji"),
                _("Zaświadczenia o statusie ucznia czekają na akceptację."),
                ("web:coordinator-student-status",),
                query="status=oczekujace",
                icon="file",
            )
        )
    if duplicates is not None:
        rows.append(
            _row(
                "duplicates",
                duplicates,
                _("Zdublowane konta"),
                _("Ta sama osoba z kilkoma kontami uczestnika."),
                ("web:coordinator-duplicates",),
                icon="users",
            )
        )
    rows.append(
        _row(
            "chat",
            counters.get("chat", 0),
            _("Wiadomości"),
            _("Nieprzeczytane rozmowy i kolejka moderacji."),
            ("web:coordinator-chat",),
            icon="chat",
        )
    )
    if competition is not None and competition.has_feature("participant_forum"):
        rows.append(
            _row(
                "forum",
                counters.get("forum", 0),
                _("Forum do moderacji"),
                _("Wątki, wpisy i zgłoszenia czekające na moderatora."),
                ("web:coordinator-forum",),
                icon="forum",
            )
        )
    return rows


def _deadlines(stages: list, bundle: dict, now) -> list[dict]:
    """Najbliższe terminy z 30 dni: etapy (z obiektów już wczytanych), warsztaty, zaplanowane komunikaty."""
    horizon = now + timedelta(days=DEADLINE_HORIZON_DAYS)
    rows: list[dict] = []
    for stage in stages:
        if not stage.has_deadline:
            continue
        candidates = (
            (stage.opens_at, _("Otwarcie etapu")),
            (stage.submission_deadline, _("Koniec oddawania prac")),
            (stage.review_deadline_at, _("Koniec recenzji")),
            (stage.appeal_window_opens_at, _("Otwarcie okna reklamacji")),
            (stage.appeal_window_closes_at, _("Zamknięcie okna reklamacji")),
        )
        url = resolve(("web:coordinator-stage-edit",), (stage.pk,)) or ""
        for moment, label in candidates:
            if moment and now <= moment <= horizon:
                rows.append(
                    {"at": moment, "label": label, "title": stage.display_name, "url": url, "kind": "stage"}
                )
    attendance = resolve(("web:coordinator-workshop-attendance",)) or ""
    tz = timezone.get_current_timezone()
    for row in bundle["workshops"]["rows"]:
        moment = timezone.make_aware(datetime.combine(row["date"], time(0, 0)), tz)
        if moment <= horizon:
            rows.append(
                {
                    "at": moment,
                    "date_only": True,
                    "time": row["time"],
                    "label": _("Warsztat"),
                    "title": row["topic"],
                    "url": attendance,
                    "kind": "workshop",
                }
            )
    messages = resolve(("web:coordinator-messages",)) or ""
    for row in bundle["upcoming_broadcasts"]:
        rows.append(
            {
                "at": row["scheduled_for"],
                "label": _("Wysyłka komunikatu"),
                "title": row["subject"],
                "url": f"{messages}#zaplanowane" if messages else "",
                "kind": "broadcast",
            }
        )
    rows.sort(key=lambda row: row["at"])
    return rows[:DEADLINE_LIMIT]


#: Skróty do najczęstszych zadań (audyt UX-PANEL-04 § 1): nazwa adresu, dopisek, ikona. Pozycja, której
#: adresu nie ma (ekran za flagą), znika – tak samo jak w menu.
SHORTCUTS = (
    ("messages", ("web:coordinator-messages",), "", "mail"),
    ("participants", ("web:coordinator-accounts",), "role=participant", "users"),
    ("workshops", ("web:coordinator-workshop-attendance",), "", "calendar"),
    ("workshop_certificates", ("web:coordinator-workshop-auto-certificates",), "", "award"),
    ("duplicates", ("web:coordinator-duplicates",), "", "users"),
    ("funnel", ("web:coordinator-stats-funnel",), "", "chart"),
    ("chat", ("web:coordinator-chat",), "", "chat"),
    ("export", ("web:coordinator-export",), "", "file"),
)


def _shortcut_labels() -> dict:
    return {
        "messages": _("Wyślij komunikat"),
        "participants": _("Uczestnicy"),
        "workshops": _("Obecność na warsztatach"),
        "workshop_certificates": _("Zaświadczenia z warsztatów"),
        "duplicates": _("Zdublowane konta"),
        "funnel": _("Lejek uczestnika"),
        "chat": _("Wiadomości"),
        "export": _("Eksport danych"),
    }


def _shortcuts() -> list[dict]:
    labels = _shortcut_labels()
    rows = []
    for key, names, query, icon in SHORTCUTS:
        url = resolve(names)
        if url is None:
            continue
        rows.append(
            {"key": key, "label": labels[key], "url": f"{url}?{query}" if query else url, "icon": icon}
        )
    return rows


def start_context(request, context: dict) -> dict:
    """Kontekst Startu doklejany do kontekstu pulpitu (``dashboard_context``) – rola sprawdzana tutaj."""
    services.require_coordinator(request)
    competition = request.competition
    edition = context.get("edition")
    stages = [row["stage"] for row in context.get("stage_rows") or []]
    now = timezone.now()
    bundle = _cached(
        "attention", competition, ATTENTION_SECONDS, lambda: _attention_bundle(competition, edition)
    )
    bundle = bundle or {
        "scheduled": 0,
        "next_scheduled": None,
        "failed": 0,
        "upcoming_broadcasts": [],
        "student_status": None,
        "workshops": {"page_id": None, "rows": []},
    }
    duplicates = _cached("duplicates", competition, DUPLICATES_SECONDS, lambda: _duplicates(competition))
    summary = _cached("summary", competition, SUMMARY_SECONDS, lambda: _summary(competition, edition))
    rows = []
    for row in context.get("attention") or []:
        rows.append({**row, "icon": ATTENTION_ICONS.get(row.get("key"), "alert")})
    rows += _extra_rows(competition, bundle, duplicates)
    open_stages = sum(1 for stage in stages if stage.closed_at is None)
    workshops_page_id = bundle["workshops"]["page_id"]
    return {
        "start_due": [row for row in rows if row["value"]],
        "start_quiet": [row for row in rows if not row["value"]],
        "start_deadlines": _deadlines(stages, bundle, now),
        "start_summary": summary,
        "start_stages": {"total": len(stages), "open": open_stages, "closed": len(stages) - open_stages},
        "start_next_scheduled": bundle["next_scheduled"],
        "start_shortcuts": _shortcuts(),
        "start_pins": shell.navigation(request)["nav_pins"],
        "start_pin_limit": shell.MAX_PINS,
        "start_workshops_edit_url": (
            resolve(("wagtailadmin_pages:edit",), (workshops_page_id,)) or "" if workshops_page_id else ""
        ),
        "now": context.get("now") or now,
    }
