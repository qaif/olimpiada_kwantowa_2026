"""Powiadomienia e-mail dla uczestnika: potwierdzenie wysyłki, werdykt skanu, wyniki, reklamacja.

Dlaczego jeden moduł na cztery zdarzenia z trzech aplikacji: to jest **jedna decyzja
organizatora** – „o czym piszemy uczestnikowi i czego mu nie piszemy” – i musi dać się przeczytać
w jednym miejscu. Rozsypana po serwisach rozjechałaby się co do tonu, co do zakresu danych
(potwierdzenie nie może nieść punktów) i co do momentu wysyłki. Serwisy domenowe wołają stąd
jedną funkcję i nie wiedzą nic o treści listu.

Zasady, wspólne z ``apps.accounts.activation``:

- list jest **skutkiem ubocznym** operacji, a nie jej warunkiem. Kolejkujemy przez
  ``transaction.on_commit`` (``activation.queue_mail``), więc niedostępny MTA nie zamienia
  przyjętego rozwiązania w błąd 500, a worker nie czyta wiersza, którego jeszcze nie ma w bazie,
- **milczenie jest domyślne**. Piszemy tylko wtedy, gdy zaszło coś, na co uczestnik ma
  zareagować albo co musi zachować jako dowód. Czysty skan jest ciszą: gdyby szedł list, każdy
  uczestnik dostawałby dwa listy za jedną wysyłkę, a pierwszy przestałby cokolwiek znaczyć,
- **do listu nie trafiają punkty ani cudze dane.** Potwierdzenie wysyłki niesie numer zadania,
  wersję, sumę kontrolną i czas; powiadomienie o wynikach niesie wyłącznie odnośnik do tabeli.
  Skrzynka pocztowa nie jest kanałem zabezpieczonym i nie ma w niej po co trzymać wyniku,
- **konto wyłączone nie dostaje poczty** (``User.is_active``). Konto nieaktywowane albo
  zablokowane przez koordynatora nie jest adresem, pod którym ktokolwiek czeka na wiadomość,
- w audycie zostaje sam **rodzaj** powiadomienia (``notification.sent``). Ani treść, ani adres:
  adres jest daną osobową, a treść da się odtworzyć z rodzaju i z obiektu.
"""

from __future__ import annotations

import logging

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.activation import absolute_url, queue_mail
from apps.core.models import audit
from apps.tenancy import branding

logger = logging.getLogger(__name__)

#: Rodzaje powiadomień – kody maszynowe do audytu. Nazwy są stabilne, bo po nich szuka się
#: w logu „czy uczestnik dostał list o decyzji komisji”.
TYPE_SUBMISSION_RECEIVED = "submission.received"
TYPE_SUBMISSION_INFECTED = "submission.infected"
TYPE_RESULTS_PUBLISHED = "results.published"
TYPE_APPEAL_DECIDED = "appeal.decided"

#: Tematy listów. Leniwe (``gettext_lazy``), bo moduł ładuje się przy starcie procesu –
#: do napisu sprowadza je ``queue_mail`` tuż przed kolejkowaniem zadania.
SUBMISSION_RECEIVED_SUBJECT = gettext_lazy("Rozwiązanie przyjęte – Olimpiada Kwantowa")
SUBMISSION_INFECTED_SUBJECT = gettext_lazy("Plik odrzucony przez skan antywirusowy – Olimpiada Kwantowa")
RESULTS_PUBLISHED_SUBJECT = gettext_lazy("Wyniki etapu ogłoszone – Olimpiada Kwantowa")
APPEAL_DECIDED_SUBJECT = gettext_lazy("Decyzja w sprawie reklamacji – Olimpiada Kwantowa")

#: Te same cztery tematy jako wzorce z nazwą konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 1.1.1).
#: Stałe wyżej zostają odwrotem i są napisami dosłownymi, a nie wzorcami podstawionymi nazwą
#: Konkursu #1; wybiera między nimi ``apps.tenancy.branding.subject``.
SUBMISSION_RECEIVED_SUBJECT_TEMPLATE = gettext_lazy("Rozwiązanie przyjęte – %(competition)s")
SUBMISSION_INFECTED_SUBJECT_TEMPLATE = gettext_lazy(
    "Plik odrzucony przez skan antywirusowy – %(competition)s"
)
RESULTS_PUBLISHED_SUBJECT_TEMPLATE = gettext_lazy("Wyniki etapu ogłoszone – %(competition)s")
APPEAL_DECIDED_SUBJECT_TEMPLATE = gettext_lazy("Decyzja w sprawie reklamacji – %(competition)s")


def _signature(competition=None) -> tuple[str, ...]:
    """Stopka każdego listu – jedna, bo to ten sam nadawca i ta sama skrzynka bez odbioru.

    Funkcja, a nie stała: od wprowadzenia angielskiej wersji serwisu napis ma się przetłumaczyć
    w chwili składania listu, a moduł ładuje się przy starcie procesu, zanim jakikolwiek język
    jest aktywny. Podpis idzie przez ``branding.signature``, więc przy wyłączonej fladze wraca
    dokładnie ten przetłumaczalny literał, co dotąd.
    """
    return (
        "--",
        branding.signature(competition, fallback=_("Olimpiada Kwantowa")),
        _("Wiadomość wysłana automatycznie; prosimy na nią nie odpowiadać."),
    )


def _message(*lines: str, competition=None) -> str:
    """Składa treść listu razem ze stopką – żeby żaden list nie wyszedł bez podpisu."""
    return "\n".join([*lines, "", *_signature(competition)])


def _recipient(user) -> str:
    """Adres, na który wolno pisać: konto musi istnieć, być aktywne i mieć adres."""
    if user is None or not user.is_active:
        return ""
    return (user.email or "").strip()


def _send(user, subject: str, message: str, *, kind: str, target, competition=None) -> bool:
    """Kolejkuje jeden list i zostawia w audycie sam rodzaj powiadomienia.

    Zwraca, czy list poszedł – wartość jest dla testów i dla logu, nie dla przeglądarki.
    Audyt powstaje **tylko** przy faktycznej wysyłce: wpis „wysłano” przy koncie zablokowanym
    byłby śladem zdarzenia, które się nie odbyło.

    ``competition`` wyznacza kopertę (nadawcę). Temat i podpis złożył już wołający – ma je z tego
    samego konkursu, bo bierze go z obiektu, którego dotyczy powiadomienie.
    """
    recipient = _recipient(user)
    if not recipient:
        return False
    queue_mail(subject, message, recipient, competition=competition)
    audit(None, "notification.sent", target, {"type": kind})
    logger.info("Zakolejkowano powiadomienie %s dla obiektu %s.", kind, target.pk)
    return True


# --- (a) potwierdzenie przyjęcia rozwiązania ----------------------------------------------------


def submission_received_message(submission, submission_file, competition=None) -> str:
    """Treść potwierdzenia: co, która wersja, kiedy i o jakiej sumie kontrolnej.

    Suma sha256 jest w liście celowo. To jedyny dowód, jaki uczestnik ma w ręku na to, **który**
    plik dotarł na serwer: gdyby kiedykolwiek spierał się o treść oddanej pracy, porównanie sumy
    z sumą pliku na jego dysku rozstrzyga sprawę bez wiary komukolwiek na słowo.
    """
    stage = submission.entry.stage
    return _message(
        _("Twoje rozwiązanie zostało przyjęte przez serwis Olimpiady Kwantowej (%(stage)s).")
        % {"stage": stage.display_name},
        "",
        _("Zadanie: %(number)s. %(title)s")
        % {"number": submission.problem.number, "title": submission.problem.title},
        _("Wersja: %(version)s") % {"version": submission.version},
        _("Czas przyjęcia (UTC): %(when)s") % {"when": submission.submitted_at.isoformat()},
        _("Suma kontrolna pliku (sha256): %(sha)s") % {"sha": submission_file.sha256},
        "",
        _(
            "Plik trafił na skan antywirusowy. Jeśli skan go odrzuci, dostaniesz osobną wiadomość – "
            "w przeciwnym razie nie piszemy nic więcej i ta wersja idzie do oceny."
        ),
        "",
        _("Każda kolejna wysyłka tworzy nową wersję; oceniana jest ostatnia."),
        competition=competition,
    )


def notify_submission_received(submission, submission_file) -> bool:
    """Potwierdzenie przyjęcia pracy. Woła je ``submissions.services.create_submission``."""
    # Konkurs bierze się z **pracy**, a nie z kontekstu żądania: kolumna ``Submission.competition``
    # jest tą samą, po której koordynator widzi tę pracę na swoim ekranie, więc list nie może
    # nieść innej marki niż panel.
    competition = submission.competition
    return _send(
        submission.entry.participant.user,
        branding.subject(SUBMISSION_RECEIVED_SUBJECT_TEMPLATE, SUBMISSION_RECEIVED_SUBJECT, competition),
        submission_received_message(submission, submission_file, competition),
        kind=TYPE_SUBMISSION_RECEIVED,
        target=submission,
        competition=competition,
    )


# --- (b) werdykt skanu: wyłącznie plik odrzucony -------------------------------------------------


def submission_infected_message(submission, submission_file, competition=None) -> str:
    """Treść listu o odrzuconym pliku – z jawnym „oddaj jeszcze raz”, póki etap jest otwarty."""
    stage = submission.entry.stage
    return _message(
        _(
            "Plik, który wysłałeś do serwisu Olimpiady Kwantowej, został odrzucony przez skan "
            "antywirusowy i nie wejdzie do oceniania."
        ),
        "",
        _("Etap: %(stage)s") % {"stage": stage.display_name},
        _("Zadanie: %(number)s. %(title)s")
        % {"number": submission.problem.number, "title": submission.problem.title},
        _("Wersja: %(version)s") % {"version": submission.version},
        _("Suma kontrolna pliku (sha256): %(sha)s") % {"sha": submission_file.sha256},
        "",
        _(
            "Najczęstszą przyczyną jest zainfekowany komputer albo plik pobrany z nieznanego "
            "źródła, a nie treść samego rozwiązania."
        ),
        "",
        _(
            "Co zrobić: sprawdź komputer programem antywirusowym, wygeneruj plik ponownie i wyślij "
            "go jeszcze raz w panelu uczestnika. Liczy się ostatnia wersja przyjęta przed terminem."
        ),
        competition=competition,
    )


def notify_submission_infected(submission, submission_file) -> bool:
    """List o odrzuceniu zainfekowanego pliku. Czysty skan jest cichy – patrz nagłówek modułu."""
    competition = submission.competition
    return _send(
        submission.entry.participant.user,
        branding.subject(SUBMISSION_INFECTED_SUBJECT_TEMPLATE, SUBMISSION_INFECTED_SUBJECT, competition),
        submission_infected_message(submission, submission_file, competition),
        kind=TYPE_SUBMISSION_INFECTED,
        target=submission,
        competition=competition,
    )


# --- (c) ogłoszenie wyników etapu ---------------------------------------------------------------


def results_published_message(stage, link: str, feedback_link: str, competition=None) -> str:
    """Treść listu o wynikach: dwa odnośniki i ani jednej liczby.

    Punktów w liście nie ma świadomie. Tabela jest w serwisie, za logowaniem albo pod pseudonimem
    – a list wędruje przez serwery, których nie kontrolujemy, i zostaje w skrzynce na lata.
    """
    return _message(
        f"Wyniki etapu „{stage.display_name}” ({stage.edition.year_label}) zostały ogłoszone.",
        "",
        f"Tabela wyników: {link}",
        f"Twoje punkty i komentarze recenzentów: {feedback_link}",
        "",
        "Jeżeli nie zgadzasz się z oceną, reklamację składa się w panelu uczestnika w oknie "
        "reklamacji wyznaczonym dla tego etapu.",
        competition=competition,
    )


def notify_results_published(publication, *, request=None) -> int:
    """Powiadamia wszystkich uczestników etapu o ogłoszeniu wyników. Zwraca liczbę listów.

    Woła to ``results.services.publish_results`` – po zapisie publikacji, wewnątrz jej transakcji.
    Listy idą przez ``queue_mail``, czyli po commicie: gdyby publikacja się wycofała, nikt nie
    dostanie wiadomości o tabeli, której nie ma.

    Ponowna publikacja tego samego etapu wysyła listy jeszcze raz i to jest zamierzone: ogłoszenie
    poprawionej tabeli jest nową informacją, a cisza po niej byłaby gorsza od powtórzonego listu.

    W audycie stoi jeden wpis na całe ogłoszenie (cel: publikacja), a nie wpis na uczestnika –
    tysiąc wierszy różniących się wyłącznie adresatem nie niesie nic ponad licznik, a każdy z nich
    wskazywałby konkretną osobę.
    """
    # Import lokalny: ``apps.competitions`` nie zależy od ``apps.submissions``, ale ten moduł
    # ładuje się przy rejestracji aplikacji i nie ma po co ciągnąć modeli zawodów na starcie.
    from apps.accounts.preferences import language_for
    from apps.competitions.models import StageEntry

    stage = publication.stage
    # Konkurs etapu czytamy **zawsze**, także w żądaniu: od etapu 2 wyznacza on nie tylko domenę
    # w linku (poniżej), ale też markę w temacie i nadawcę listu, a te muszą być te same dla
    # wszystkich odbiorców jednego ogłoszenia – niezależnie od tego, czy poszło z panelu, czy
    # z przebiegu wsadowego. Koszt to dwa zapytania na **całe** ogłoszenie, a nie na uczestnika.
    competition = stage.edition.competition
    # W żądaniu adres nadal buduje się z ``request`` (protokół i host z nagłówków), więc konkursu
    # tam nie podajemy – to jest zachowanie sprzed tej zmiany i nie ma powodu go ruszać.
    link_competition = None if request is not None else competition
    link = absolute_url(reverse("web:results", args=[stage.pk]), request, link_competition)
    feedback_link = absolute_url(
        reverse("web:participant-feedback", args=[stage.pk]), request, link_competition
    )
    sent = 0
    for entry in StageEntry.objects.filter(stage=stage).select_related("participant__user"):
        user = entry.participant.user
        recipient = _recipient(user)
        if not recipient:
            continue
        # Treść składa się **per odbiorca**, bo każdy może mieć inny język interfejsu. Koszt to
        # kilkanaście operacji na napisach na uczestnika – nieporównanie mniej niż jedno zapytanie
        # do bazy, a alternatywą byłby list w języku koordynatora dla wszystkich.
        # Temat składa się **wewnątrz** bloku języka razem z treścią: ``branding.subject`` rozwiązuje
        # leniwy napis w chwili wywołania, więc wyniesienie go przed pętlę wysłałoby wszystkim temat
        # w języku koordynatora – dokładnie ta regresja, której ten blok tu zapobiega.
        # Konto bez zapisanego języka dostaje język **konkursu** etapu (§ 1.6.3), a nie ten, który
        # akurat obowiązuje koordynatorowi – ogłoszenie ma brzmieć tak samo dla wszystkich.
        with language_for(user, competition):
            queue_mail(
                branding.subject(RESULTS_PUBLISHED_SUBJECT_TEMPLATE, RESULTS_PUBLISHED_SUBJECT, competition),
                results_published_message(stage, link, feedback_link, competition),
                recipient,
                competition=competition,
            )
        sent += 1
    if sent:
        audit(None, "notification.sent", publication, {"type": TYPE_RESULTS_PUBLISHED})
    logger.info("Zakolejkowano %s powiadomień o wynikach etapu %s.", sent, stage.pk)
    return sent


# --- (d) decyzja w sprawie reklamacji ------------------------------------------------------------


def appeal_decided_message(appeal, decision, link: str, competition=None) -> str:
    """Treść listu o rozstrzygnięciu reklamacji – z uzasadnieniem, bo to ono jest tu treścią.

    Nowa punktacja w liście **nie** stoi: decyzja bywa zmianą oceny w obie strony, a liczba
    wyrwana z kontekstu tabeli mówi mniej niż uzasadnienie. Panel pokazuje jedno i drugie.
    """
    submission = appeal.submission
    return _message(
        "Komisja odwoławcza rozstrzygnęła Twoją reklamację w Olimpiadzie Kwantowej.",
        "",
        f"Etap: {submission.entry.stage.display_name}",
        f"Zadanie: {submission.problem.number}. {submission.problem.title}",
        f"Rozstrzygnięcie: {appeal.get_status_display()}",
        "",
        "Uzasadnienie komisji:",
        decision.justification,
        "",
        f"Szczegóły i aktualną punktację znajdziesz w panelu uczestnika: {link}",
        competition=competition,
    )


def notify_appeal_decided(appeal, decision, *, request=None) -> bool:
    """List o decyzji komisji. Woła to ``appeals.services.decide_appeal``."""
    user = appeal.filed_by.user
    # Jw. – konkurs pracy, której dotyczy reklamacja. Czytamy go zawsze (marka i nadawca listu),
    # a do adresu podajemy tylko poza żądaniem, tak jak dotąd.
    competition = appeal.submission.competition
    link = absolute_url(reverse("web:me"), request, None if request is not None else competition)
    return _send(
        user,
        branding.subject(APPEAL_DECIDED_SUBJECT_TEMPLATE, APPEAL_DECIDED_SUBJECT, competition),
        appeal_decided_message(appeal, decision, link, competition),
        kind=TYPE_APPEAL_DECIDED,
        target=appeal,
        competition=competition,
    )
