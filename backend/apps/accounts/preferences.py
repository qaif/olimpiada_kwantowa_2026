"""Ustawienia interfejsu przeglądającego: język i tryb wysokiego kontrastu.

Dwie rzeczy w jednym module, bo z punktu widzenia użytkownika są jednym: „jak ma wyglądać ta
strona u mnie”. Obie zapisuje jeden formularz w pasku konta i obie stosuje jedna warstwa
pośrednia – gdyby były osobno, przełączenie języka gubiłoby kontrast i odwrotnie.

Gdzie to mieszka i dlaczego akurat tam:

- **konto zalogowane** → wiersz ``UserPreference``. Ustawienie ma jechać za człowiekiem na drugie
  urządzenie: uczeń, który włączył wysoki kontrast na szkolnym komputerze, nie ma go włączać
  jeszcze raz na telefonie w dniu zawodów,
- **gość** → sesja (kontrast) i ciasteczko języka (``settings.LANGUAGE_COOKIE_NAME``). Konta nie
  ma, więc nie ma gdzie zapisać – a czytelnik regulaminu ma takie samo prawo do dużego kontrastu,
  jak zalogowany uczestnik,
- **nikt nic nie ustawił** → nagłówek ``Accept-Language`` przeglądarki, którym zajmuje się
  ``django.middleware.locale.LocaleMiddleware``. Nasza warstwa stoi **za** nim i tylko nadpisuje
  jego rozstrzygnięcie jawnym wyborem człowieka: domyślnie ma wygrywać to, co ktoś ustawił sobie
  w systemie, a nie to, co zgadł serwer.

Dlaczego bez ``i18n_patterns``: adresy tego serwisu trafiają do listów, do regulaminu i do pism
(``/me/``, ``/results/12/``, ``/zgoda/<token>/``). Prefiks języka zrobiłby z każdego z nich dwa
adresy, z których jeden zawsze byłby wklejony nie tam, gdzie trzeba, a linki z listów wysłanych po
polsku prowadziłyby na polską wersję także osobę, która przełączyła serwis na angielski.

Zakres tłumaczenia jest **celowo wąski**: panel uczestnika, ekrany logowania i rejestracji, ekrany
konta, pasek konta i stopka, lista i szczegóły recenzji oraz listy wysyłane do uczestników. Ekrany
koordynatora zostają po polsku (organizator jest polski i to jego narzędzie pracy), a treść
redakcyjna w CMS-ie ma własną drogę – redaktor pisze ją w edytorze, nie w pliku ``.po``.
"""

from __future__ import annotations

from contextlib import contextmanager

from django.conf import settings
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme

#: Klucz w sesji dla trybu wysokiego kontrastu gościa. Sesja, a nie ciasteczko: wartość jest
#: czytana wyłącznie po stronie serwera (dokłada atrybut do ``<html>``), więc nie ma powodu
#: wysyłać jej przeglądarce w osobnym pliku cookie – polityka cookies obiecuje ich minimum.
CONTRAST_SESSION_KEY = "interface_high_contrast"

#: Ważność ciasteczka języka. Rok, tyle samo, co domyślnie w Django: wybór języka nie jest zgodą
#: ani stanem sesji, tylko ustawieniem, które ma przetrwać przerwę między etapami.
LANGUAGE_COOKIE_MAX_AGE = 365 * 24 * 3600

#: Wartość atrybutu ``data-contrast`` na ``<html>`` w trybie wysokiego kontrastu. Arkusz
#: (``static/css/app.css``) trzyma się **wyłącznie** tej nazwy.
CONTRAST_ATTRIBUTE_VALUE = "high"


def available_languages() -> tuple[str, ...]:
    """Kody języków, które serwis naprawdę ma. Źródłem jest ``settings.LANGUAGES``, nie literał."""
    return tuple(code for code, _label in settings.LANGUAGES)


#: Polecenie przełącznika zapisane w języku, na który przełącza. Stoi obok ``LANGUAGES`` z tego
#: samego powodu, co etykiety: przycisk ma zaczepić oko osoby, która **nie czyta** bieżącego
#: języka strony, więc jego nazwa dostępna nie może przechodzić przez gettext (byłaby wtedy
#: zawsze w języku, którego ta osoba właśnie nie rozumie). Kod spoza słownika dostaje samą
#: natywną etykietę – to nadal nazwa w dobrym języku, tylko krótsza.
LANGUAGE_SWITCH_LABELS = {
    "pl": "Przełącz na polski",
    "en": "Switch to English",
}


def language_choices() -> list[dict]:
    """Pozycje przełącznika języka: kod, etykieta i polecenie – w **tym** języku, nie w tłumaczeniu.

    „English” po polsku i „polski” po angielsku byłyby uprzejmością, która nie działa: kto szuka
    swojego języka na liście, szuka go zapisanego po swojemu. Dlatego etykiety są w ``LANGUAGES``
    zapisane natywnie i nie przechodzą przez gettext.

    ``switch_label`` jest nazwą dostępną przycisku w pasku konta. Przycisk pokazuje dziś flagę
    (organizator poprosił o ikonę zamiast napisu „EN”), a flaga jest **obrazkiem bez tekstu** –
    bez tej etykiety czytnik ekranu przeczytałby „przycisk”, i tyle.
    """
    return [
        {"code": code, "label": label, "switch_label": LANGUAGE_SWITCH_LABELS.get(code, label)}
        for code, label in settings.LANGUAGES
    ]


def stored_preference(user):
    """Wiersz ``UserPreference`` zalogowanego konta albo ``None``.

    Import modelu jest lokalny, bo ten moduł ładuje się jako warstwa pośrednia przy starcie
    procesu – czyli zanim rejestr aplikacji jest gotowy.
    """
    if user is None or not user.is_authenticated:
        return None
    return getattr(user, "preference", None)


def resolve(request) -> dict:
    """Co obowiązuje dla tego żądania: ``{"language": …, "high_contrast": …}``.

    Kolejność źródeł: zapis konta → sesja/ciasteczko → rozstrzygnięcie ``LocaleMiddleware``
    (czyli ciasteczko albo ``Accept-Language``). Pusty ``language`` w zapisie konta znaczy „nie
    wybierałem, idź za przeglądarką”, a nie „polski” – to dwie różne odpowiedzi i tylko pierwsza
    jest prawdziwa dla kogoś, kto nigdy nie dotknął przełącznika.
    """
    preference = stored_preference(getattr(request, "user", None))
    language = ""
    high_contrast = False
    if preference is not None:
        language = preference.language or ""
        high_contrast = preference.high_contrast
    else:
        session = getattr(request, "session", None)
        high_contrast = bool(session.get(CONTRAST_SESSION_KEY)) if session is not None else False
    if language not in available_languages():
        language = translation.get_language() or settings.LANGUAGE_CODE
    return {"language": language, "high_contrast": high_contrast}


def save_preferences(request, *, language: str, high_contrast: bool) -> dict:
    """Zapisuje wybór i od razu go stosuje. Zwraca stan, który ma obowiązywać od tej chwili.

    Zalogowanemu zapisujemy wiersz (jedzie za nim na inne urządzenie), gościowi – sesję. Język
    trafia dodatkowo do ciasteczka, które ustawia wołający widok: to ono jest tym, co czyta
    ``LocaleMiddleware`` przy następnym żądaniu, także po wylogowaniu.

    Pusty ``language`` znaczy „zostaw bieżący”: formularz kontrastu nie może przy okazji
    przestawiać języka na domyślny.
    """
    resolved = language if language in available_languages() else (translation.get_language() or "")
    if resolved not in available_languages():
        resolved = settings.LANGUAGE_CODE
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from .models import UserPreference

        UserPreference.objects.update_or_create(
            user=user, defaults={"language": resolved, "high_contrast": high_contrast}
        )
    if hasattr(request, "session"):
        request.session[CONTRAST_SESSION_KEY] = high_contrast
    translation.activate(resolved)
    request.LANGUAGE_CODE = resolved
    request.high_contrast = high_contrast
    return {"language": resolved, "high_contrast": high_contrast}


@contextmanager
def language_for(user):
    """Na czas bloku aktywuje język zapisany na koncie **odbiorcy**.

    Potrzebne wyłącznie tam, gdzie tekst powstaje **poza** żądaniem tej osoby: przy ogłoszeniu
    wyników (list do tysiąca uczestników składa koordynator) i przy nocnym przypomnieniu
    o rozmowie (składa je zadanie w tle). List napisany w języku nadawcy albo serwera byłby
    w obu tych miejscach po prostu przypadkowy.

    Przy liście powstającym w żądaniu adresata (potwierdzenie uploadu, aktywacja konta) nic tu
    nie trzeba robić: aktywny język **jest** już jego językiem.

    Konto bez zapisanego wyboru nie zmienia niczego – zostaje język, który akurat obowiązuje.
    """
    preference = stored_preference(user)
    language = getattr(preference, "language", "") or ""
    if language not in available_languages():
        yield
        return
    previous = translation.get_language()
    translation.activate(language)
    try:
        yield
    finally:
        # ``activate(None)`` nie istnieje – przy braku poprzedniego języka wracamy do domyślnego
        # zamiast zostawiać po sobie ustawienie cudzego konta.
        translation.activate(previous or settings.LANGUAGE_CODE)


def safe_next_url(request) -> str:
    """Adres powrotu z formularza ustawień – wyłącznie wewnętrzny.

    ``next`` przychodzi od nadawcy żądania, więc bez tego sprawdzenia przełącznik języka byłby
    gotowym otwartym przekierowaniem: „kliknij EN na stronie olimpiady” prowadziłoby na cudzą
    stronę logowania. Odwrotem jest strona, z której formularz przyszedł, a w ostateczności korzeń.
    """
    candidate = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"
    if url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return "/"


class PreferencesMiddleware:
    """Włącza język i kontrast wybrane przez człowieka – **za** ``LocaleMiddleware``.

    Kolejność jest istotna i nieprzypadkowa. ``LocaleMiddleware`` rozstrzyga język z ciasteczka
    i z ``Accept-Language``; ta warstwa dokłada jedyne źródło, o którym tamta nie wie – zapis na
    koncie. Postawiona **przed** nią zostałaby po chwili nadpisana i wybór zalogowanego uczestnika
    przegrywałby z ustawieniem przeglądarki, czyli dokładnie odwrotnie, niż powinien.

    Musi też stać za ``AuthenticationMiddleware``, bo czyta ``request.user``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        state = resolve(request)
        translation.activate(state["language"])
        request.LANGUAGE_CODE = state["language"]
        request.high_contrast = state["high_contrast"]
        response = self.get_response(request)
        # Nagłówek ``Content-Language`` ma mówić o języku, który faktycznie wyszedł – a ten mogła
        # zmienić ta warstwa już po tym, jak ``LocaleMiddleware`` ustawiło swój.
        response.setdefault("Content-Language", state["language"])
        return response


def interface(request) -> dict:
    """Procesor kontekstu: atrybut kontrastu i lista języków dla paska konta.

    ``interface_contrast`` jest **napisem** (``"high"`` albo pustym), a nie wartością logiczną,
    bo szablon wstawia go wprost w atrybut ``data-contrast`` na ``<html>``. Pusty napis znaczy
    „nie renderuj atrybutu” i tak wygląda tryb domyślny – arkusz nie ma wtedy czego zaczepić.
    """
    high_contrast = bool(getattr(request, "high_contrast", False))
    return {
        "interface_contrast": CONTRAST_ATTRIBUTE_VALUE if high_contrast else "",
        "interface_high_contrast": high_contrast,
        "interface_languages": language_choices(),
        "interface_language": getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE),
    }
