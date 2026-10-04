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

**Które języki są w ogóle oferowane, rozstrzyga konkurs, a nie kod.** Zbiór stoi w
``tenancy.Competition.interface_languages`` (I18N-01 § 1; wcześniej jeden przełącznik
``cms.SiteSettings.english_interface_enabled``). Olimpiada Kwantowa ma tam sam polski, bo tak
poprosił jej organizator: „strona tylko w wersji polskiej (…) do polskiej olimpiady niech będzie
wersja tylko w języku polskim na razie”. Jeden język znaczy tu **naprawdę** jeden: polski
obowiązuje także wtedy, gdy przeglądarka prosi o ``Accept-Language: en``, gdy w ciasteczku stoi
``en`` i gdy konto ma zapisany angielski – bo inaczej organizator zobaczyłby swoją stronę po
polsku, a uczeń z angielskim systemem po angielsku, czyli dokładnie to, o czym poprosił, żeby się
nie działo. Międzynarodowy konkurs ``iqo`` ma w zbiorze jedenaście języków, w tym arabski
pisany od prawej do lewej (``interface_direction``).

Zapisu na koncie **nie kasujemy**: ``UserPreference.language`` zostaje w bazie i wraca do użytku
w dniu, w którym konkurs ten język włączy. Odebranie komuś ustawienia przy zmianie konfiguracji
serwisu byłoby odpowiedzią na pytanie, którego nikt nie zadał.
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


def offered_languages(request) -> tuple[str, ...]:
    """Języki, które oferuje **konkurs tego żądania** (``Competition.ui_languages``).

    Odczyt jest darmowy i to jest warunek, pod którym w ogóle wolno go zadać z warstwy pośredniej:
    ``request.competition`` wiąże ``apps.tenancy.middleware`` jednym zapytaniem, którym i tak
    znajduje konkurs, a zbiór jest kolumną tego samego wiersza. Progi zapytań
    z ``apps/tenancy/tests/test_invariants.py`` zostają więc nietknięte także w żądaniach API.

    Żądanie bez konkursu – host, który nie pasuje do żadnej witryny, ``RequestFactory`` w teście
    jednostkowym – dostaje wyłącznie język instalacji. Odwrót jest celowo po stronie **prośby
    organizatora**: strona, o której nic nie wiadomo, ma być polska, a nie zgadywana z nagłówka
    przeglądarki.
    """
    competition = getattr(request, "competition", None)
    languages = getattr(competition, "ui_languages", None)
    return tuple(languages) if languages else (settings.LANGUAGE_CODE,)


def available_languages(request=None) -> tuple[str, ...]:
    """Kody języków, które serwis naprawdę ma **w tym żądaniu**. Źródłem jest ``settings.LANGUAGES``.

    Bez żądania odpowiadamy za całą instalację: wołają tak komenda, zadanie w tle i test
    jednostkowy, a żadne z nich nie ma konkursu, o który można by zapytać. Z żądaniem odpowiedź
    jest zawężona do zbioru jego konkursu (:func:`offered_languages`).

    Jedna funkcja na oba pytania, a nie dwie: „jakie języki serwis ma” i „jakie języki wolno teraz
    wybrać” to z punktu widzenia każdego wołającego (przełącznik, zapis, rozstrzygnięcie żądania)
    to samo pytanie, a rozdzielenie ich byłoby pierwszym miejscem, w którym jedno z nich zostanie
    zadane w złej postaci.
    """
    if request is not None:
        return offered_languages(request)
    return tuple(code for code, _label in settings.LANGUAGES)


def text_direction(language: str | None) -> str:
    """``"rtl"`` dla języka pisanego od prawej do lewej (``settings.LANGUAGES_BIDI``), inaczej ``"ltr"``.

    Z listy Django, a nie z własnej: ``LANGUAGES_BIDI`` zna też języki, których dziś nie mamy
    (hebrajski, perski, urdu), więc dołożenie któregoś do ``LANGUAGES`` nie wymaga pamiętania
    o drugim miejscu.
    """
    base = (language or "").split("-")[0].lower()
    return "rtl" if base in settings.LANGUAGES_BIDI else "ltr"


#: Polecenie przełącznika zapisane w języku, na który przełącza. Stoi obok ``LANGUAGES`` z tego
#: samego powodu, co etykiety: przycisk ma zaczepić oko osoby, która **nie czyta** bieżącego
#: języka strony, więc jego nazwa dostępna nie może przechodzić przez gettext (byłaby wtedy
#: zawsze w języku, którego ta osoba właśnie nie rozumie). Kod spoza słownika dostaje samą
#: natywną etykietę – to nadal nazwa w dobrym języku, tylko krótsza.
LANGUAGE_SWITCH_LABELS = {
    "pl": "Przełącz na polski",
    "en": "Switch to English",
    "zh-hans": "切换到简体中文",
    "hi": "हिन्दी में बदलें",
    "es": "Cambiar a español",
    "ar": "التبديل إلى العربية",
    "fr": "Passer en français",
    "bn": "বাংলায় দেখুন",
    "pt": "Mudar para português",
    "ru": "Переключить на русский",
    "id": "Beralih ke Bahasa Indonesia",
}


def language_choices(request=None) -> list[dict]:
    """Pozycje przełącznika języka: kod, etykieta i polecenie – w **tym** języku, nie w tłumaczeniu.

    „English” po polsku i „polski” po angielsku byłyby uprzejmością, która nie działa: kto szuka
    swojego języka na liście, szuka go zapisanego po swojemu. Dlatego etykiety są w ``LANGUAGES``
    zapisane natywnie i nie przechodzą przez gettext.

    ``switch_label`` jest nazwą dostępną przycisku w pasku konta. Przy dwóch językach przycisk
    pokazuje flagę (organizator poprosił o ikonę zamiast napisu „EN”), a flaga jest **obrazkiem
    bez tekstu** – bez tej etykiety czytnik ekranu przeczytałby „przycisk”, i tyle.
    ``direction`` trafia do atrybutu ``dir`` pozycji menu: arabska nazwa na liście
    lewostronnej ma się wyrównać i złamać jak tekst arabski.

    Konkurs z jednym językiem dostaje listę **jednoelementową**, a szablon paska konta nie rysuje
    wtedy formularza języka w ogóle: przełącznik z jedną pozycją, która i tak już obowiązuje,
    byłby przyciskiem, po którego kliknięciu nic się nie dzieje.
    """
    codes = available_languages(request)
    return [
        {
            "code": code,
            "label": label,
            "switch_label": LANGUAGE_SWITCH_LABELS.get(code, label),
            "direction": text_direction(code),
        }
        for code, label in settings.LANGUAGES
        if code in codes
    ]


def stored_preference(user):
    """Wiersz ``UserPreference`` zalogowanego konta albo ``None``.

    Import modelu jest lokalny, bo ten moduł ładuje się jako warstwa pośrednia przy starcie
    procesu – czyli zanim rejestr aplikacji jest gotowy.
    """
    if user is None or not user.is_authenticated:
        return None
    return getattr(user, "preference", None)


def _supported(code: str | None, languages: tuple[str, ...]) -> str:
    """Kod z ``languages`` odpowiadający ``code`` (z wariantami: ``zh-CN`` → ``zh-hans``) albo ``""``.

    Dopasowanie wariantów robi Django (``get_supported_language_variant`` z ``LANG_INFO``), a my
    tylko przycinamy wynik do zbioru konkursu – dlatego ``pt-BR`` trafia w ``pt``, a ``zh-TW``
    w ``zh-hans`` jedynie wtedy, gdy konkurs chiński w ogóle oferuje.
    """
    if not code:
        return ""
    try:
        variant = translation.get_supported_language_variant(code)
    except LookupError:
        return ""
    return variant if variant in languages else ""


def _browser_language(request, languages: tuple[str, ...]) -> str:
    """Pierwszy język nagłówka ``Accept-Language`` (w kolejności wag), który konkurs oferuje.

    Własne przejście po nagłówku, a nie ``translation.get_language_from_request``: tamto przy
    braku dopasowania oddaje ``settings.LANGUAGE_CODE``, czyli polski, i nie da się odróżnić
    „przeglądarka prosi o polski” od „przeglądarka nie prosi o nic, co znamy”. Konkurs
    angielskojęzyczny pokazywałby wtedy gościowi bez nagłówka stronę po polsku.
    """
    from django.utils.translation.trans_real import parse_accept_lang_header

    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "") if hasattr(request, "META") else ""
    for accepted, _quality in parse_accept_lang_header(header):
        if accepted == "*":
            break
        found = _supported(accepted, languages)
        if found:
            return found
    return ""


def resolve(request) -> dict:
    """Co obowiązuje dla tego żądania: ``{"language": …, "high_contrast": …}``.

    Kolejność źródeł języka (I18N-01 § 2), każde **przycięte** do zbioru konkursu:

    1. zapis konta (``UserPreference.language``) – jedyna odpowiedź udzielona świadomie,
    2. ciasteczko języka (wybór gościa z przełącznika),
    3. ``Accept-Language`` przeglądarki – najlepsze dopasowanie w zbiorze,
    4. ``Competition.default_language`` – język, w którym organizator prowadzi konkurs,
    5. pierwszy język zbioru.

    Pusty ``language`` w zapisie konta znaczy „nie wybierałem, idź za przeglądarką”, a nie
    „polski” – to dwie różne odpowiedzi i tylko pierwsza jest prawdziwa dla kogoś, kto nigdy nie
    dotknął przełącznika.

    Przycięcie jest sednem: bez niego jednojęzyczność byłaby pozorna – zapisany na koncie ``en``
    przeszedłby pierwszym warunkiem, a ``Accept-Language: en`` – trzecim, więc ta sama strona
    byłaby polska dla organizatora i angielska dla ucznia z angielskim systemem.
    """
    languages = available_languages(request)
    preference = stored_preference(getattr(request, "user", None))
    language = ""
    high_contrast = False
    if preference is not None:
        language = preference.language or ""
        high_contrast = preference.high_contrast
    else:
        session = getattr(request, "session", None)
        high_contrast = bool(session.get(CONTRAST_SESSION_KEY)) if session is not None else False
    if language not in languages:
        language = ""
    if not language and len(languages) > 1:
        cookies = getattr(request, "COOKIES", {}) or {}
        language = _supported(cookies.get(settings.LANGUAGE_COOKIE_NAME), languages)
        language = language or _browser_language(request, languages)
    if not language:
        default = getattr(getattr(request, "competition", None), "default_language", "")
        language = default if default in languages else languages[0]
    return {"language": language, "high_contrast": high_contrast}


def save_preferences(request, *, language: str, high_contrast: bool) -> dict:
    """Zapisuje wybór i od razu go stosuje. Zwraca stan, który ma obowiązywać od tej chwili.

    Zalogowanemu zapisujemy wiersz (jedzie za nim na inne urządzenie), gościowi – sesję. Język
    trafia dodatkowo do ciasteczka, które ustawia wołający widok: to ono jest tym, co czyta
    ``LocaleMiddleware`` przy następnym żądaniu, także po wylogowaniu.

    Pusty ``language`` znaczy „zostaw bieżący”: formularz kontrastu nie może przy okazji
    przestawiać języka na domyślny.

    W serwisie **bez** angielskiego kolumny ``language`` nie dotykamy w ogóle i to jest sedno
    obietnicy „zapis zostaje w bazie”. Gdyby zapisywać wyliczone ``pl``, wystarczyłoby jedno
    kliknięcie w kontrast, żeby zapisany wcześniej ``en`` zniknął na zawsze – a wtedy włączenie
    angielskiego z powrotem nie przywróciłoby nikomu jego wyboru, tylko zastałoby puste pole.
    """
    languages = available_languages(request)
    resolved = language if language in languages else (translation.get_language() or "")
    if resolved not in languages:
        resolved = languages[0]
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from .models import UserPreference

        defaults = {"high_contrast": high_contrast}
        if len(languages) > 1:
            defaults["language"] = resolved
        UserPreference.objects.update_or_create(user=user, defaults=defaults)
    if hasattr(request, "session"):
        request.session[CONTRAST_SESSION_KEY] = high_contrast
    translation.activate(resolved)
    request.LANGUAGE_CODE = resolved
    request.high_contrast = high_contrast
    return {"language": resolved, "high_contrast": high_contrast}


def competition_language(competition=None) -> str:
    """Język domyślny konkursu – podanego wprost albo wziętego z kontekstu. Pusty napis, gdy go brak.

    Kolejność źródeł jest ta sama, co w ``apps.accounts.activation.mail_competition``, i z tego
    samego powodu: temat, nadawca i **język** jednego listu mają pochodzić z jednego konkursu.
    Kontekst wiąże warstwa pośrednia w żądaniu, a ``competition_context`` w zadaniu i w komendzie.

    Kod spoza ``settings.LANGUAGES`` jest tu równoważny brakowi wyboru: instalacja ma katalogi
    tłumaczeń wyłącznie tych języków, które wymienia konfiguracja, a aktywowanie kodu bez katalogu
    dałoby napisy źródłowe podane jako przekład.

    Import kontekstu jest lokalny, bo ten moduł ładuje się jako warstwa pośrednia przy starcie
    procesu – czyli zanim rejestr aplikacji jest gotowy.
    """
    if competition is None:
        from apps.tenancy.context import current_competition

        competition = current_competition()
    language = getattr(competition, "default_language", "") or ""
    return language if language in available_languages() else ""


def competition_languages(competition) -> tuple[str, ...]:
    """Języki, w których wolno napisać list o tym konkursie – tak, jak widzi je jego serwis.

    To jest odpowiednik ``available_languages(request)`` dla kodu, który żądania nie ma: przy
    liście składanym hurtem nie ma nagłówka ``Host``, ale jest **konkurs**, a zbiór jest kolumną
    jego wiersza (``Competition.ui_languages``) – ogłoszenie wyników do tysiąca uczestników nie
    płaci za to ani jednego zapytania.

    Konkurs nieznany (wołający nie podał, kontekst pusty) znaczy „nie wiadomo, czyj to list”
    i zostaje przy językach instalacji: odbieranie komukolwiek zapisanego wyboru na podstawie
    **braku** informacji byłoby zgadywaniem.
    """
    if competition is None:
        return available_languages()
    languages = getattr(competition, "ui_languages", None)
    return tuple(languages) if languages else (settings.LANGUAGE_CODE,)


@contextmanager
def language_for(user, competition=None):
    """Na czas bloku aktywuje język **odbiorcy**, a gdy konto go nie zapisało – język konkursu.

    Trzy źródła w tej kolejności:

    1. zapis na koncie odbiorcy (``UserPreference.language``) – jedyna z tych odpowiedzi, której
       ktoś udzielił świadomie, więc wygrywa z każdą inną – **o ile serwis tego konkursu ten język
       w ogóle oferuje** (``Competition.ui_languages``). Konkurs tylko po polsku nie wysyła
       angielskich listów do kogoś, kto angielski zapisał sobie kiedy indziej: uczestnik, który
       całą stronę widzi po polsku, dostałby inaczej list w języku, którego na tej stronie nie
       ma. Zapis zostaje w bazie nietknięty i wraca razem z językiem w zbiorze konkursu,
    2. ``Competition.default_language`` konkursu, o którym jest list – podanego wprost albo
       wziętego z kontekstu. Bez tego kroku konkurs prowadzony po angielsku wysyłałby list po
       polsku z serwisu, który uczestnik widział wyłącznie po angielsku
       (``docs/UNIWERSALNY-ETAP-2.md`` § 1.6.3),
    3. ``settings.LANGUAGE_CODE`` – język instalacji, czyli odwrót sprzed etapu 2, używany tam,
       gdzie konkursu nie ma skąd wziąć (zadanie okresowe po całej instalacji, test jednostkowy).

    Dla Konkursu #1 wszystkie trzy kroki dają ``pl``: konto bez wyboru dostaje
    ``default_language`` konkursu, a ten jest ``"pl"`` od migracji ``tenancy.0002``. Listy zostają
    polskie co do bajtu i pilnują tego ``apps/tenancy/tests/test_invariants.py``
    oraz ``apps/tenancy/tests/test_branding.py``.

    Konkurs przychodzi argumentem, a nie zapytaniem: wołający ma go już wczytanego, bo tym samym
    obiektem podpisuje list (``queue_mail(..., competition=…)``). Ustawienie jego serwisu kosztuje
    **jedno** zapytanie na całą wysyłkę, a nie jedno na list – patrz ``competition_languages``.

    Kroku 2 przełącznik nie dotyczy i to jest rozmyślne: ``Competition.default_language`` jest
    deklaracją organizatora o **jego własnym** konkursie („zawody prowadzę po angielsku”), a nie
    ofertą wyboru dla przeglądającego. Konkurs #1 ma tam ``pl``, więc po wyłączeniu angielskiego
    jego listy są polskie wszystkimi trzema drogami naraz.

    Potrzebne wyłącznie tam, gdzie tekst powstaje **poza** żądaniem tej osoby: przy ogłoszeniu
    wyników (list do tysiąca uczestników składa koordynator) i przy nocnym przypomnieniu
    o rozmowie (składa je zadanie w tle). List napisany w języku nadawcy albo serwera byłby
    w obu tych miejscach po prostu przypadkowy.

    Przy liście powstającym w żądaniu adresata (potwierdzenie uploadu, aktywacja konta) nic tu
    nie trzeba robić: aktywny język **jest** już jego językiem.
    """
    if competition is None:
        from apps.tenancy.context import current_competition

        competition = current_competition()
    preference = stored_preference(user)
    language = getattr(preference, "language", "") or ""
    if language not in competition_languages(competition):
        language = competition_language(competition) or settings.LANGUAGE_CODE
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
    i z ``Accept-Language``; ta warstwa dokłada dwa źródła, o których tamta nie wie – zapis na
    koncie i **ofertę językową serwisu**. Postawiona **przed** nią zostałaby po chwili nadpisana:
    wybór zalogowanego uczestnika przegrywałby z ustawieniem przeglądarki, a serwis ustawiony na
    „tylko po polsku” pokazywałby się po angielsku każdemu, kto ma angielski system.

    Stąd bierze się też to, że ``Content-Language`` ustawiamy sami: nagłówek ma mówić o języku,
    który naprawdę wyszedł, a nie o tym, który wybrało ``LocaleMiddleware`` z ``Accept-Language``.

    Musi też stać za ``AuthenticationMiddleware``, bo czyta ``request.user``.

    Panele redakcyjne to osobna sprawa i nie przechodzą tędy: Wagtail otacza widoki ``/cms/``
    własnym ``translation.override`` z ``UserProfile.preferred_language`` (patrz
    ``wagtail/admin/auth.py``), więc redaktor zachowuje język panelu niezależnie od tego, w jakim
    języku serwis rozmawia z czytelnikiem.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        state = resolve(request)
        translation.activate(state["language"])
        request.LANGUAGE_CODE = state["language"]
        request.high_contrast = state["high_contrast"]
        response = None
        try:
            response = self.get_response(request)
            # Nagłówek ``Content-Language`` ma mówić o języku, który faktycznie wyszedł – a ten mogła
            # zmienić ta warstwa już po tym, jak ``LocaleMiddleware`` ustawiło swój.
            response.setdefault("Content-Language", state["language"])
            return response
        finally:
            # Język jest ustawieniem **wątku**, a gunicorn w trybie ``gthread`` obsługuje w jednym
            # wątku kolejne żądania i kod spoza nich (sygnały po odpowiedzi, zadania wołane
            # synchronicznie). Bez sprzątania arabski z ostatniego żądania zostawałby aktywny do
            # następnego ``activate`` – dla kodu, który języka nie ustawia sam (list do koordynatora,
            # log), byłby to język przypadkowy. Wyjątkiem jest odpowiedź **strumieniowa**: jej treść
            # powstaje dopiero przy wysyłaniu, już za tą warstwą, i ma powstać w języku żądania.
            if response is None or not getattr(response, "streaming", False):
                translation.deactivate()


def interface(request) -> dict:
    """Procesor kontekstu: atrybut kontrastu i lista języków dla paska konta.

    ``interface_contrast`` jest **napisem** (``"high"`` albo pustym), a nie wartością logiczną,
    bo szablon wstawia go wprost w atrybut ``data-contrast`` na ``<html>``. Pusty napis znaczy
    „nie renderuj atrybutu” i tak wygląda tryb domyślny – arkusz nie ma wtedy czego zaczepić.

    ``interface_languages`` bywa listą **jednoelementową** – w konkursie jednojęzycznym – i to
    jest jedyne miejsce, w którym szablon paska konta się o tym dowiaduje.

    ``interface_direction`` (``ltr``/``rtl``) idzie w atrybut ``dir`` na ``<html>``, a
    ``interface_language_label`` – natywna nazwa bieżącego języka – jest napisem przycisku menu
    języków, gdy konkurs ma ich więcej niż dwa (I18N-01 § 3).
    """
    high_contrast = bool(getattr(request, "high_contrast", False))
    language = getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)
    choices = language_choices(request)
    current = next((item for item in choices if item["code"] == language), None)
    return {
        "interface_contrast": CONTRAST_ATTRIBUTE_VALUE if high_contrast else "",
        "interface_high_contrast": high_contrast,
        "interface_languages": choices,
        "interface_language": language,
        "interface_language_label": current["label"] if current else language,
        "interface_direction": text_direction(language),
    }
