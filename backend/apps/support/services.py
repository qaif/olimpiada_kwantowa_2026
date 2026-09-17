"""Reguły zgłoszeń: zbieranie kontekstu, założenie sprawy, odpowiedź i zmiana stanu.

Widoki tego modułu nie dublują: cała wiedza o tym, co wolno zebrać, komu idzie powiadomienie
i kiedy sprawa wraca do kolejki, mieszka tutaj. Dzięki temu ekran uczestnika, ekran koordynatora
i (gdyby kiedyś powstało) API odpowiadają tak samo.

Powiadomienia idą przez ``apps.accounts.activation.queue_mail``, czyli tą samą drogą, co listy
aktywacyjne: wysyłka jest **skutkiem ubocznym** zapisu i jedzie po commicie. Niedostępny MTA nie
może zamienić zgłoszonej sprawy w błąd 500 – a to jest sprawa kogoś, komu już coś nie zadziałało.

Adres organizatora bierzemy z **konkursu sprawy** (``tenancy.Competition.contact_email``), a nie
z ``DEFAULT_FROM_EMAIL``: to drugie jest adresem **nadawcy** naszych listów (``noreply@…``), więc
powiadomienie o nowym zgłoszeniu lądowałoby w skrzynce, której nikt nie czyta. Dalsze odwroty to
``cms.SiteSettings.contact_email`` (Ustawienia → Dane serwisu) i dopiero na końcu
``DEFAULT_FROM_EMAIL`` – list bez idealnego adresata jest lepszy niż wywrócona wysyłka. Dla
Olimpiady Kwantowej wszystkie trzy wskazują dziś na ten sam adres: ``tenancy.0002`` przepisała go
z ustawień serwisu (``docs/UNIWERSALNY-ETAP-1.md`` § 0.1).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from rest_framework import status as http

from apps.accounts.activation import absolute_url, queue_mail
from apps.core.api import DomainError
from apps.core.models import audit

from .models import (
    CONTEXT_AUDIT_MINUTES,
    MAX_BODY_LENGTH,
    PENDING_STATUSES,
    SupportCategory,
    SupportMessage,
    SupportTicket,
    TicketStatus,
)

logger = logging.getLogger(__name__)

#: Ile znaków nagłówka ``User-Agent`` zapisujemy. Pełny bywa trzystuznakowy i nic ponad pierwsze
#: sto znaków nie mówi o przeglądarce nic, czego nie wiedzielibyśmy wcześniej.
USER_AGENT_LIMIT = 200

#: Ile etapów wymieniamy w kontekście. Uczestnik bywa zapisany w kilku etapach kilku edycji;
#: sprawa dotyczy tego, co robi teraz, więc lista jest krótka i od najnowszego etapu.
CONTEXT_STAGE_LIMIT = 5


def _clean_text(value: str, *, field: str, label: str) -> str:
    """Tekst od użytkownika: bez białych znaków na brzegach, niepusty, w granicach limitu."""
    text = (value or "").strip()
    if not text:
        raise DomainError(f"{label} nie może być puste.", f"{field}_REQUIRED", http.HTTP_400_BAD_REQUEST)
    if len(text) > MAX_BODY_LENGTH:
        raise DomainError(
            f"{label} jest za długie (limit {MAX_BODY_LENGTH} znaków).",
            f"{field}_TOO_LONG",
            http.HTTP_400_BAD_REQUEST,
        )
    return text


def role_snapshot(user, competition=None) -> str:
    """Rola konta w chwili zgłoszenia, jednym słowem. Pusty napis = zgłoszenie bez konta.

    Kolejność rozstrzygania jest ta sama, co na liście kont koordynatora
    (``apps.web.views.coordinator_accounts.account_role``) – dwie różne odpowiedzi na pytanie
    „kim jest ta osoba” byłyby gorsze niż brak odpowiedzi.

    Rola jest rolą **w konkursie sprawy** (§ 3.8): ta sama osoba bywa uczestnikiem jednej
    olimpiady i recenzentem drugiej, a wątek czyta się w kontekście tego, kim nadawca był po tej
    stronie. Profil uczestnika podajemy wprost, bo po § 3.3 relacja jest wielokrotna i to wołający
    rozstrzyga, o który z profili chodzi.
    """
    if user is None or not user.is_authenticated:
        return ""
    from apps.web.views.coordinator_accounts import account_role

    return account_role(user, competition, _participant_of(user, competition))


def _last_audit_action(user) -> str:
    """Ostatnia czynność użytkownika w audycie z ostatnich kilku minut – sama **nazwa akcji**.

    To jest najcenniejszy element kontekstu i jednocześnie ten, przy którym najłatwiej przesadzić.
    Bierzemy wyłącznie nazwę akcji (``submission.uploaded``, ``account.email_changed``), nigdy
    ``diff`` ani adresu IP: nazwa mówi „co ta osoba robiła, zanim coś nie zadziałało”, a reszta
    wpisu jest już dziennikiem zdarzeń, który ma własny ekran i własne uprawnienia.
    """
    if user is None or not user.is_authenticated:
        return ""
    from apps.core.models import AuditLog

    since = timezone.now() - timedelta(minutes=CONTEXT_AUDIT_MINUTES)
    entry = AuditLog.objects.filter(actor=user, at__gte=since).order_by("-at", "-id").first()
    return entry.action if entry is not None else ""


def _participant_of(user, competition):
    """Profil uczestnika zgłaszającego **w tym konkursie** albo ``None``.

    ``participant_for``, a nie ``user.participant``: od wydania D jedna osoba ma tyle profili, ile
    olimpiad, w których startuje (§ 3.3). Kontekst zgłoszenia ma opisywać tę, z której strony
    sprawa przyszła – kod uczestnika z cudzego konkursu byłby w kolejce organizatora
    identyfikatorem, który u niego niczego nie otwiera.
    """
    if user is None:
        return None
    from apps.accounts.services import participant_for

    return participant_for(user, competition)


def _stage_ids(user, competition) -> list[int]:
    """Etapy, w których zgłaszający jest zapisany – od najnowszego, najwyżej kilka.

    Identyfikatory, a nie nazwy: koordynator i tak klika w etap w panelu, a nazwa w kontekście
    rozjechałaby się z nazwą etapu, gdyby organizator ją zmienił po zgłoszeniu.
    """
    participant = _participant_of(user, competition)
    if participant is None:
        return []
    from apps.competitions.models import StageEntry

    return list(
        StageEntry.objects.filter(participant=participant)
        .order_by("-stage__opens_at", "-stage_id")
        .values_list("stage_id", flat=True)[:CONTEXT_STAGE_LIMIT]
    )


def collect_context(request, *, page_url: str = "") -> dict:
    """Kontekst techniczny zgłoszenia. Wyłącznie to, o co organizator i tak by zapytał.

    Czego tu **nie** ma i nie może być: ciasteczek, nagłówka ``Authorization``, tokenu CSRF, treści
    formularzy, adresu IP. Kontekst jedzie do bazy razem z treścią sprawy i bywa czytany długo po
    niej – to nie jest miejsce na cokolwiek, czym dałoby się przejąć czyjąś sesję.

    ``page_url`` przychodzi z ukrytego pola formularza, czyli **od klienta**. Zapisujemy go jako
    tekst i nigdzie nie używamy jako adresu do przekierowania ani do zbudowania odnośnika –
    w panelu koordynatora stoi jako zwykły napis. Dzięki temu podstawiony adres jest wyłącznie
    kłamstwem w kontekście zgłoszenia, a nie wektorem.
    """
    user = getattr(request, "user", None)
    if user is not None and not user.is_authenticated:
        user = None
    competition = ticket_competition(request)
    participant = _participant_of(user, competition)
    return {
        "adres_strony": (page_url or "").strip()[:500],
        "przegladarka": (request.headers.get("User-Agent") or "").strip()[:USER_AGENT_LIMIT],
        # Język **interfejsu**, a nie nagłówek ``Accept-Language``: zgłoszenie „strona pokazuje
        # coś dziwnego” czyta się inaczej, gdy przyszło z wersji angielskiej.
        "jezyk": get_language() or "",
        "kod_uczestnika": participant.public_code if participant is not None else "",
        "etapy": _stage_ids(user, competition),
        "ostatnia_czynnosc": _last_audit_action(user),
    }


def ticket_competition(request=None):
    """Konkurs, do którego idzie zgłoszenie składane właśnie teraz.

    Kolejność jest ta sama, co wszędzie indziej w tym etapie: konkurs żądania (ustawia go
    ``CompetitionMiddleware``) → kontekst (``competition_context`` w zadaniu, komendzie, teście) →
    ``None``. ``None`` jest odpowiedzią **poprawną**, a nie brakiem danych: sprawa napisana pod
    adresem, którego nikt nie przypisał do konkursu, idzie do operatora platformy (§ 3.2).

    Czego ta funkcja świadomie **nie** robi: nie schodzi na „jedyny konkurs w instalacji”
    (``default_competition``). Tamten odwrót jest dla kodu, który musi komuś przypisać nowy
    wiersz; tutaj pusty konkurs ma własne, sensowne znaczenie, więc zgadywanie tylko by je
    zamazało.
    """
    from apps.tenancy.context import current_competition

    competition = getattr(request, "competition", None) if request is not None else None
    return competition if competition is not None else current_competition()


def _organizer_email(competition=None) -> str:
    """Adres organizatora dla powiadomień o nowych zgłoszeniach.

    Źródłem prawdy jest **konkurs sprawy** (``Competition.contact_email``): zgłoszenie idzie do
    tego organizatora, którego strona je przyjęła, a nie do adresu instalacji. Dla Konkursu #1
    nic się przez to nie zmienia – migracja ``tenancy.0002`` wzięła ten adres dokładnie
    z ``SiteSettings.contact_email`` (§ 0.1), więc listy chodzą tam, gdzie chodziły.

    Kolejność odwrotów: konkurs → ``SiteSettings`` witryny domyślnej (sprawa do operatora
    platformy, konkurs bez wpisanego adresu) → ``DEFAULT_FROM_EMAIL``. Odczyt ustawień jest
    opakowany w ``try``, bo bywają nieosiągalne (baza bez drzewa stron), a niedostępne ustawienie
    nie może wywrócić zapisu zgłoszenia.
    """
    if competition is not None:
        address = (competition.contact_email or "").strip()
        if address:
            return address
    try:
        from wagtail.models import Site

        from apps.cms.models import SiteSettings

        site = Site.objects.filter(is_default_site=True).first()
        if site is not None:
            address = (SiteSettings.for_site(site).contact_email or "").strip()
            if address:
                return address
    except Exception:  # noqa: BLE001 - brak witryny/tabeli nie może zablokować zgłoszenia
        logger.warning("Nie udało się odczytać adresu kontaktowego z ustawień serwisu.")
    return settings.DEFAULT_FROM_EMAIL


def _notify_organizer(ticket: SupportTicket, *, request=None) -> None:
    """List do organizatora o nowej sprawie. Bez treści zgłoszenia – z linkiem do wątku.

    Treść zostaje w serwisie świadomie: zgłoszenie bywa opisem cudzego problemu z danymi (kod
    uczestnika, szkoła, adres), a skrzynka organizatora nie jest miejscem, w którym ma leżeć jego
    kopia. List mówi, że sprawa jest, jakiej kategorii i gdzie ją przeczytać.
    """
    link = absolute_url(reverse("web:coordinator-support-detail", args=[ticket.pk]), request)
    message = "\n".join(
        [
            f"Nowe zgłoszenie #{ticket.pk} w kategorii „{ticket.get_category_display()}”.",
            f"Temat: {ticket.subject}",
            "",
            "Treść jest w panelu koordynatora:",
            link,
        ]
    )
    queue_mail(
        f"Nowe zgłoszenie #{ticket.pk} – Olimpiada Kwantowa",
        message,
        _organizer_email(ticket.competition),
    )


def _notify_reporter(ticket: SupportTicket, *, request=None) -> None:
    """List do zgłaszającego o odpowiedzi organizatora. Bez treści odpowiedzi – z linkiem.

    Ten sam powód, co wyżej, tylko w drugą stronę: odpowiedź bywa zdaniem o czyimś koncie, a poczta
    bywa czytana na cudzym ekranie. Zgłoszenie bez konta dostaje odnośnik publiczny – ale sam wątek
    i tak wymaga zalogowania, więc list mówi wtedy wprost, żeby odpowiedzieć na tę wiadomość.
    """
    recipient = ticket.reply_to
    if not recipient:  # pragma: no cover - constraint wymaga konta albo adresu
        return
    if ticket.user_id is not None:
        link = absolute_url(reverse("web:support-detail", args=[ticket.pk]), request)
        tail = ["Odpowiedź jest w Twoim panelu:", link]
    else:
        tail = ["Odpowiedź przyjdzie osobną wiadomością od organizatora."]
    message = "\n".join(
        [
            f"Organizator odpowiedział na Twoje zgłoszenie #{ticket.pk}.",
            f"Temat: {ticket.subject}",
            "",
            *tail,
        ]
    )
    queue_mail(f"Odpowiedź na zgłoszenie #{ticket.pk} – Olimpiada Kwantowa", message, recipient)


@transaction.atomic
def open_ticket(
    *,
    user,
    category: str,
    subject: str,
    body: str,
    email: str = "",
    context: dict | None = None,
    request=None,
) -> SupportTicket:
    """Zakłada sprawę razem z jej pierwszą wiadomością. Zwraca zgłoszenie.

    Sprawa i pierwsza wiadomość powstają **razem i w jednej transakcji**: zgłoszenie bez treści
    nie jest zgłoszeniem, a wątek, w którym pierwsza wypowiedź bywa nieobecna, zmuszałby każdy
    ekran do sprawdzania, czy w ogóle jest co pokazać.

    Adres zwrotny jest wymagany dokładnie wtedy, gdy nie ma konta – i to jest jedyna różnica
    między zgłoszeniem uczestnika a zgłoszeniem z publicznego formularza kontaktowego. Resztę
    (CAPTCHA, pułapka, próg czasu) wnosi formularz, tak samo jak przy rejestracji.
    """
    if category not in SupportCategory.values:
        raise DomainError("Wybierz kategorię z listy.", "CATEGORY_INVALID", http.HTTP_400_BAD_REQUEST)
    clean_subject = _clean_text(subject, field="SUBJECT", label="Temat")[:200]
    clean_body = _clean_text(body, field="BODY", label="Opis zgłoszenia")
    account = user if (user is not None and user.is_authenticated) else None
    address = "" if account is not None else (email or "").strip().lower()
    if account is None and not address:
        raise DomainError(
            "Podaj adres e-mail – bez niego nie mamy dokąd odpowiedzieć.",
            "EMAIL_REQUIRED",
            http.HTTP_400_BAD_REQUEST,
        )

    competition = ticket_competition(request)
    ticket = SupportTicket.objects.create(
        competition=competition,
        user=account,
        email=address,
        role_snapshot=role_snapshot(account, competition),
        category=category,
        subject=clean_subject,
        context=context or {},
    )
    SupportMessage.objects.create(ticket=ticket, author=account, from_coordinator=False, body=clean_body)
    audit(
        account,
        "ticket.opened",
        ticket,
        # Sama kategoria i długość opisu, nigdy treść: wpis audytowy zostaje na stałe i czytają go
        # też osoby bez prawa do danych osobowych, a zgłoszenie bywa opisem cudzej sprawy.
        {"category": category, "length": len(clean_body), "anonymous": account is None},
        request=request,
    )
    _notify_organizer(ticket, request=request)
    return ticket


@transaction.atomic
def reply(
    ticket: SupportTicket, body: str, *, author, from_coordinator: bool, request=None
) -> SupportMessage:
    """Dopisuje wypowiedź do wątku i przestawia stan sprawy.

    Reguła stanu jest krótka i jest tu jej jedyna kopia: odpowiedź organizatora daje ``ANSWERED``,
    dopisek zgłaszającego wraca do ``OPEN``. Bez tej drugiej połowy sprawa odpowiedziana, do której
    ktoś dopisał „to nadal nie działa”, znikałaby z kolejki na zawsze.

    Sprawa zamknięta nie przyjmuje wypowiedzi – z obu stron. Odmowa, a nie ciche otwarcie na nowo:
    zamknięcie jest decyzją organizatora i ma zostać decyzją, a nie stanem, który każdy dopisek
    unieważnia.
    """
    if ticket.is_closed:
        raise DomainError(
            "To zgłoszenie jest zamknięte. Załóż nowe, jeśli sprawa wróciła.",
            "TICKET_CLOSED",
            http.HTTP_409_CONFLICT,
        )
    clean_body = _clean_text(body, field="BODY", label="Treść odpowiedzi")
    message = SupportMessage.objects.create(
        ticket=ticket,
        author=author if getattr(author, "is_authenticated", False) else None,
        from_coordinator=from_coordinator,
        body=clean_body,
    )
    if from_coordinator:
        ticket.status = TicketStatus.ANSWERED
        ticket.answered_at = timezone.now()
        ticket.save(update_fields=["status", "answered_at"])
        audit(
            author,
            "ticket.answered",
            ticket,
            {"length": len(clean_body)},
            request=request,
        )
        _notify_reporter(ticket, request=request)
    else:
        ticket.status = TicketStatus.OPEN
        ticket.save(update_fields=["status"])
    return message


@transaction.atomic
def set_status(ticket: SupportTicket, new_status: str, *, actor, request=None) -> SupportTicket:
    """Zmienia stan sprawy (w praktyce: zamyka ją albo otwiera z powrotem).

    Zamknięcie zostawia wpis ``ticket.closed`` – bez niego w aktach nie byłoby widać, kto uznał
    sprawę za załatwioną. Przestawienie na ten sam stan nie robi nic i nie zostawia wpisu:
    zdarzeniem jest zmiana, a nie kliknięcie.
    """
    if new_status not in TicketStatus.values:
        raise DomainError("Nieznany status zgłoszenia.", "STATUS_INVALID", http.HTTP_400_BAD_REQUEST)
    if ticket.status == new_status:
        return ticket
    ticket.status = new_status
    ticket.closed_at = timezone.now() if new_status == TicketStatus.CLOSED else None
    ticket.save(update_fields=["status", "closed_at"])
    if new_status == TicketStatus.CLOSED:
        audit(actor, "ticket.closed", ticket, {"category": ticket.category}, request=request)
    return ticket


def pending_tickets(competition=None):
    """Sprawy czekające na organizatora **tego** konkursu – jedno źródło kolejki i licznika.

    Kolejka w panelu i licznik na pulpicie muszą liczyć to samo, więc zawężenie jest tu, a nie
    w dwóch widokach. ``for_competition`` jest ścisłe: sprawa bez konkursu należy do operatora
    platformy i w kolejce organizatora nie ma czego szukać.
    """
    queryset = SupportTicket.objects.filter(status__in=PENDING_STATUSES)
    return queryset.for_competition(ticket_competition() if competition is None else competition)


def open_ticket_count(competition=None) -> int:
    """Liczba spraw czekających na organizatora – licznik na pulpicie koordynatora.

    Argument jest opcjonalny, bo licznik liczy się w żądaniu, a tam konkurs stoi w kontekście.
    Wołający, który konkurs zna (komenda, test, zadanie wsadowe), podaje go wprost.
    """
    return pending_tickets(competition).count()
