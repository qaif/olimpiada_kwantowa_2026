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

from django.conf import settings
from django.db import DatabaseError
from django.urls import reverse

from apps.accounts.consents import CONSENTS
from apps.accounts.models import GROUP_APPEALS, GROUP_COORDINATOR, GROUP_PARTICIPANT
from apps.accounts.services import active_reviewer_profile
from apps.accounts.supervisors import supervisor_profile
from apps.appeals.services import appeals_committee_profile

logger = logging.getLogger(__name__)

#: Wersja interfejsu pokazywana w stopce. Zmieniana ręcznie razem z wydaniem – nie jest to numer
#: schematu API (ten mieszka w ``SPECTACULAR_SETTINGS``) ani numer migracji.
APP_VERSION = "1.0"

#: Nazwy pól zgód wymaganych bezwarunkowo (regulamin, RODO) – liczone raz, z definicji zgód.
#: Statyczna krotka, więc żadnego zapytania na żądanie.
REQUIRED_CONSENT_FIELDS: tuple[str, ...] = tuple(
    consent.field_name for consent in CONSENTS if consent.required
)


def roles(request) -> dict:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_active:
        return {
            "is_participant": False,
            "is_reviewer": False,
            "is_coordinator": False,
            "is_appeals_committee": False,
            "is_supervisor": False,
        }
    names = set(user.groups.values_list("name", flat=True))
    return {
        "is_participant": GROUP_PARTICIPANT in names and hasattr(user, "participant"),
        "is_reviewer": active_reviewer_profile(user) is not None,
        "is_coordinator": GROUP_COORDINATOR in names,
        "is_appeals_committee": GROUP_APPEALS in names and appeals_committee_profile(user) is not None,
        # Opiekun szkolny – ta sama definicja, co w mixinie widoku i w przekierowaniu po
        # zalogowaniu (``apps.accounts.supervisors.supervisor_profile``).
        "is_supervisor": supervisor_profile(user) is not None,
    }


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

    state = getattr(request, _REGISTRATION_CACHE_ATTR, None)
    if state is None:
        try:
            state = current_registration_status()
        except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
            logger.warning("Nie udało się odczytać stanu rejestracji uczestników.")
            state = RegistrationStatus(False, REGISTRATION_DISABLED)
        setattr(request, _REGISTRATION_CACHE_ATTR, state)
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
        "required_consent_fields": REQUIRED_CONSENT_FIELDS,
    }


def site_chrome(request) -> dict:
    """Etykieta bieżącej edycji (podtytuł logotypu) i wersja aplikacji (stopka)."""
    from apps.competitions.services import current_edition

    label = ""
    try:
        edition = current_edition()
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli edycji
        logger.warning("Nie udało się odczytać bieżącej edycji dla nagłówka.")
    else:
        label = edition.year_label if edition is not None else ""
    return {"site_edition_label": label, "app_version": APP_VERSION}
