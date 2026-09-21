"""Role zalogowanego użytkownika oraz dane „ramy” serwisu dla szablonu bazowego.

Nawigacja nie jest zabezpieczeniem – dostęp rozstrzygają mixiny ról (``apps.web.mixins``) i klasy
uprawnień DRF. Ten procesor służy wyłącznie temu, żeby nie pokazywać linków, które i tak dadzą 403.
Reguły są te same, co w mixinach: jedna definicja roli w całym systemie.

``site_chrome`` dokłada wyłącznie dane prezentacyjne nagłówka i stopki (etykieta bieżącej edycji,
numer wersji). Nie ma tam żadnej reguły domenowej, a błąd bazy nie może wywrócić szablonu
bazowego – dlatego zapytanie jest opakowane w ``try``.

``social_providers`` mówi szablonom, którzy dostawcy OAuth są skonfigurowani. Źródłem prawdy jest
ta sama lista, z której korzysta allauth (``settings.SOCIALACCOUNT_PROVIDERS[...]["APPS"]``),
budowana w ``config/settings/base.py`` ze zmiennych środowiskowych. Dzięki temu przycisk nie może
pojawić się dla dostawcy, którego allauth nie zna – kliknięcie kończyłoby się błędem 500.
"""

import logging
import os

from django.conf import settings
from django.db import DatabaseError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.consents import MINOR_MAX_AGE, consent_set
from apps.accounts.models import CompetitionRole
from apps.accounts.services import active_reviewer_profile, participant_for, roles_for
from apps.accounts.supervisors import supervisor_profile
from apps.appeals.services import appeals_committee_profile

logger = logging.getLogger(__name__)

#: Wersja pokazywana w stopce i na ``/status/``. Pochodzi z wydania (``APP_VERSION`` ustawia
#: ``scripts/deploy.sh`` z ``git describe`` i przekazuje do kontenera przez compose), a nie z ręcznie
#: podbijanej stałej – ta rozjeżdżała się z tagiem już po drugim wydaniu. Poza wdrożeniem (dev,
#: testy) jest to „dev”. Nie jest to numer schematu API (``SPECTACULAR_SETTINGS``) ani migracji.
APP_VERSION = os.environ.get("APP_VERSION", "dev")


def consent_field_names(competition=None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Nazwy pól zgód wymaganych bezwarunkowo i wymaganych wyłącznie od niepełnoletnich.

    Dwie krotki z **jednego** odczytu zestawu (``consents.consent_set``), bo to jeden zestaw i dwa
    pytania do niego. Skrypt odsłaniający blok zgody opiekuna (``static/js/register-age.js``) nie
    może mieć własnej listy nazw pól – rozjechałaby się z regułą serwera przy pierwszej zmianie
    zestawu zgód – a blok zgód w formularzu rejestracji oznacza po tej pierwszej liście wiersze
    „wymagane”, zamiast wypisywać nazwy pól w szablonie.

    Do etapu 2 były to dwie stałe modułu, liczone raz na proces. Teraz zestaw może zależeć od
    konkursu, więc liczą się na żądanie – i nadal **bez zapytania**, dopóki konkurs nie włączył
    ``per_competition_consents``: ``consent_set`` czyta wtedy stałą i nie zagląda do bazy (§ 5.6).
    """
    consents = consent_set(competition)
    return (
        tuple(consent.field_name for consent in consents if consent.required),
        tuple(consent.field_name for consent in consents if consent.required_for_minor),
    )


def roles(request) -> dict:
    """Role zalogowanej osoby **w konkursie z żądania** – wyłącznie do rysowania nawigacji.

    Konkurs bierzemy z żądania, bo rola jest zawsze rolą w konkursie (§ 3.8): bez tego menu
    uczestnika olimpiady A rysowałoby się także pod domeną konkursu B, a każdy z tych linków
    kończyłby się 403 z mixinu. Źródłem prawdy jest ``roles_for`` – ta sama funkcja, którą
    bramkują mixiny i uprawnienia DRF, więc menu i dostęp nie mogą się rozjechać.

    Profile (recenzent, komisja, opiekun) sprawdzamy **dodatkowo**, bo sama rola nie wystarcza:
    recenzent bez zatwierdzonego wpisu w komitecie nie ma czego recenzować, a link do kolejki
    prowadziłby go w 403.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return {
            "is_participant": False,
            "is_reviewer": False,
            "is_coordinator": False,
            "is_appeals_committee": False,
            "is_supervisor": False,
            "can_use_forum": False,
        }
    competition = getattr(request, "competition", None)
    names = roles_for(user, competition)
    return {
        "is_participant": CompetitionRole.PARTICIPANT in names
        and participant_for(user, competition) is not None,
        "is_reviewer": active_reviewer_profile(user, competition) is not None,
        "is_coordinator": CompetitionRole.COORDINATOR in names,
        "is_appeals_committee": CompetitionRole.APPEALS in names
        and appeals_committee_profile(user) is not None,
        # Opiekun szkolny – ta sama definicja, co w mixinie widoku i w przekierowaniu po
        # zalogowaniu (``apps.accounts.supervisors.supervisor_profile``).
        "is_supervisor": supervisor_profile(user, competition) is not None,
        # Forum uczestników: pozycja w pasku konta jest **wyłącznie** wtedy, gdy adres odpowie.
        # Dwa warunki naraz, bo forum ma dwie bramki i obie muszą być spełnione – przełącznik
        # konkursu (bez niego ``/forum/`` daje 404) i rola czytelnika. Liczone z ``names``, które
        # już mamy, więc pasek konta nie płaci za tę pozycję ani jednym dodatkowym zapytaniem.
        "can_use_forum": _forum_visible(competition, names, user),
    }


def _forum_visible(competition, names: set[str], user) -> bool:
    """Czy pokazać pozycję „Forum” – ta sama reguła, co ``apps.forum.services.can_read``.

    Powtórzenie reguły, a nie jej wywołanie, i to jest kompromis z otwartymi oczami:
    ``can_read`` pyta ``has_role`` trzy razy, czyli trzy zapytania **na każdej stronie serwisu**,
    a tutaj mamy już gotowy zbiór ról z jednego zapytania. Rozjazd między tymi dwoma miejscami
    kosztuje najwyżej pozycję w pasku prowadzącą w 403 (nawigacja nie jest zabezpieczeniem –
    patrz docstring modułu), a nie dostęp do cudzej rozmowy.

    Jedyna świadoma różnica wobec ``can_read``: recenzent bez zatwierdzonego wpisu w komitecie
    dostaje tu pozycję, a tam wejście. To jest ta sama różnica, którą ma dziś ``is_reviewer``
    w drugą stronę, i nie wpuszcza nikogo nigdzie.
    """
    from apps.forum.models import FORUM_FLAG

    if competition is None or not competition.has_feature(FORUM_FLAG):
        return False
    if names & {CompetitionRole.COORDINATOR, CompetitionRole.REVIEWER, CompetitionRole.APPEALS}:
        return True
    return CompetitionRole.PARTICIPANT in names and participant_for(user, competition) is not None


#: Kolejność przycisków na stronie logowania i rejestracji (stała, niezależna od słownika ustawień).
SOCIAL_PROVIDER_ORDER = (("google", "Google"), ("facebook", "Facebook"))


def social_providers(request) -> dict:
    """Lista dostawców OAuth z kompletem kluczy: ``[{"id": "google", "name": "Google"}, …]``.

    Pusta lista = sekcja „Lub kontynuuj z” w ogóle się nie renderuje. To jest jedyny przełącznik
    tej funkcji: instalacja bez kluczy wygląda dokładnie tak, jak przed jej dodaniem.
    """
    configured = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    return {
        "social_providers": [
            # ``login_url`` liczymy tutaj, a nie znacznikiem ``{% provider_login_url %}``: ten
            # ostatni sięga po obiekt ``SocialApp`` i przy dostawcy bez kluczy kończy się wyjątkiem
            # w środku renderowania szablonu. Adres jest zwykłym wpisem urlconfa.
            {"id": provider_id, "name": name, "login_url": reverse(f"{provider_id}_login")}
            for provider_id, name in SOCIAL_PROVIDER_ORDER
            if (configured.get(provider_id) or {}).get("APPS")
        ]
    }


#: Klucz podręczny na obiekcie żądania. Procesory kontekstu odpalają się **raz na renderowanie
#: szablonu**, a strona bywa składana z kilku (szablon strony + fragmenty HTMX) – bez tej pamięci
#: ta sama edycja byłaby czytana z bazy kilka razy w jednym żądaniu.
_REGISTRATION_CACHE_ATTR = "_registration_status_cache"


def registration(request) -> dict:
    """Stan rejestracji uczestników dla nawigacji, strony głównej i formularza ``/register/``.

    Szablony **nie decydują** o tym, czy rejestracja jest otwarta – decyduje serwis
    (``apps.competitions.registration.ensure_registration_open``), a to jest wyłącznie to samo
    rozstrzygnięcie przyniesione do widoku, żeby nie pokazywać przycisku prowadzącego do odmowy
    i żeby zapowiedź startu („Rejestracja rusza 8 września 2026”) brała datę z bazy, a nie z treści
    redakcyjnej, która rozjedzie się przy pierwszej zmianie terminu.

    Błąd bazy nie może wywrócić szablonu bazowego – tak samo, jak w ``site_chrome``. Awaryjnym
    stanem jest „wyłączona”: gdy nie wiadomo, czy rejestracja trwa, lepiej nie zapraszać do
    formularza, który i tak nie zapisze konta.
    """
    from apps.competitions.models import REGISTRATION_DISABLED, RegistrationStatus
    from apps.competitions.registration import current_registration_status, registration_message

    # Konkurs z żądania, a nie z kontekstu: procesor renderuje **każdą** stronę, więc jest to
    # miejsce, w którym pomyłka najbardziej boli – otwarta rejestracja sąsiada zapraszałaby do
    # formularza konkursu, który jeszcze (albo już) nie zapisuje kont. Ten sam konkurs rozstrzyga
    # o zestawie zgód, bo to ten sam formularz.
    competition = getattr(request, "competition", None)
    state = getattr(request, _REGISTRATION_CACHE_ATTR, None)
    if state is None:
        try:
            state = current_registration_status(competition=competition)
        except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
            logger.warning("Nie udało się odczytać stanu rejestracji uczestników.")
            state = RegistrationStatus(False, REGISTRATION_DISABLED)
        setattr(request, _REGISTRATION_CACHE_ATTR, state)
    required_consent_fields, minor_consent_fields = consent_field_names(competition)
    return {
        "registration": {
            "is_open": state.is_open,
            "reason": state.reason,
            "opens_at": state.opens_at,
            "closes_at": state.closes_at,
            # Gotowe zdanie dla użytkownika – jedno źródło treści dla strony, formularza i API.
            "message": registration_message(state),
        },
        # Nazwy zgód wymaganych bezwarunkowo. Blok zgód w formularzu rejestracji oznacza nimi
        # wiersze („wymagane”), a nie robi tego listą nazw pól w szablonie: reguła wymagalności ma
        # jedno źródło (``apps.accounts.consents``), więc dopisanie zgody nie zostawi wiersza bez
        # oznaczenia. Zgody warunkowe („wymagane dla osób niepełnoletnich”) i dobrowolne mówią to
        # same, podpowiedzią pola – tu ich nie ma.
        "required_consent_fields": required_consent_fields,
        # Reguła niepełnoletności w postaci, którą da się postawić w atrybutach ``data-*``.
        # Skrypt odsłaniający blok zgody opiekuna liczy dokładnie to samo, co ``consents.is_minor``
        # (``rok bieżący − rocznik <= max_age``), ale **rok bierze stąd**, a nie z zegara
        # przeglądarki: zegar użytkownika bywa przestawiony, a w nocy sylwestrową i tak
        # pokazywałby inny rok niż serwer. Rozstrzyga serwer; skrypt ma tylko nie kłamać
        # wcześniej, niż serwer zdąży odpowiedzieć.
        "minor_rule": {
            "max_age": MINOR_MAX_AGE,
            "current_year": timezone.localdate().year,
            "consent_fields": minor_consent_fields,
        },
    }


def site_chrome(request) -> dict:
    """Marka i etykieta bieżącej edycji dla nagłówka oraz wersja aplikacji dla stopki.

    **Skąd bierze się marka.** Nazwa, hasło i dane organizatora w nagłówku i stopce idą
    z ``cms.SiteSettings``, a te są ustawieniem **witryny** (``BaseSiteSetting``, § 1.6) –
    czyli są zakresowane od chwili, w której konkurs dostał własną ``wagtailcore.Site``.
    Szablon bazowy czyta je znacznikiem ``{{ settings.cms.SiteSettings… }}``, więc Konkurs #1
    renderuje **dokładnie te same napisy**, co przed wielokonkursowością – i tego pilnują testy
    niezmienności oraz złote (``apps/tenancy/tests/test_invariants.py``, ``test_golden_*``).

    Nazwy z ``Competition`` (``name``, ``short_name``, odmiana) ten procesor **nie dokłada** i to
    jest decyzja, a nie przeoczenie. Szablon ma je już z procesora ``tenancy.competition``
    (``{{ competition.short_name }}``, ``{{ competition.genitive }}``), a druga droga do tej samej
    wartości znaczyłaby pytanie „którą z nich czyta ten nagłówek” przy każdej zmianie marki.
    Dopóki marka w ramie serwisu idzie z ``SiteSettings``, dokładanie tu drugiej kopii byłoby
    kluczem kontekstowym, którego nikt nie renderuje.

    Etykieta edycji bierze konkurs **z żądania**, a nie z kontekstu: w bazie wielokonkursowej
    „pierwsza edycja z brzegu” jest cudza, a nagłówek stoi na każdej stronie serwisu.

    Błąd bazy nie może wywrócić szablonu bazowego – stąd ``try``.
    """
    from apps.competitions.services import current_edition

    competition = getattr(request, "competition", None)
    label = ""
    try:
        edition = current_edition(competition)
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
        logger.warning("Nie udało się odczytać bieżącej edycji dla nagłówka.")
    else:
        label = edition.year_label if edition is not None else ""
    return {"site_edition_label": label, "app_version": APP_VERSION}
