"""Baner komunikatów: co organizator ma do powiedzenia **na każdej** stronie serwisu.

Po co osobny mechanizm, skoro jest newsroom i są strony redakcyjne: bo to jest inna wiadomość.
Aktualność czyta ten, kto wejdzie na ``/aktualnosci/``; komunikat („przedłużamy termin do
piątku”, „logowanie przez Google nie działa, pracujemy nad tym”) musi zobaczyć każdy, kto jest
w serwisie – łącznie z uczestnikiem, który właśnie próbuje wysłać pracę i nigdzie indziej nie
zagląda. Dotąd jedyną drogą do tego było wydanie aplikacji albo masowa wysyłka listów.

Trzy decyzje:

- **tekst, nie treść bogata.** Komunikat ma ≤ 500 znaków zwykłego tekstu i jeden opcjonalny
  odnośnik (adres + etykieta). Redaktor pisze go w pośpiechu, w sytuacji, w której coś już nie
  działa – edytor z formatowaniem byłby tu przeszkodą, a HTML w banerze na każdej stronie serwisu
  jest powierzchnią, której nie ma powodu otwierać,
- **okno czasowe, nie ręczne gaszenie.** ``starts_at``/``ends_at`` sprawiają, że komunikat
  o przerwie technicznej znika sam. Wyłącznik ``is_active`` zostaje obok jako hamulec awaryjny –
  bo „zdejmij to natychmiast” jest osobną potrzebą od „to obowiązuje do piątku”,
- **dwie warstwy zamknięcia.** ``dismissible`` mówi, czy czytelnik może baner zamknąć; wybór
  zapamiętuje ``localStorage`` przeglądarki (``static/js/announcements.js``), a nie cookie ani
  konto – to preferencja widoku, a nie dana o osobie. Komunikat **niezamykalny** (awaria, termin)
  zostaje na ekranie i tak ma być.

Czwarta decyzja doszła razem z wielokonkursowością: **komunikat ma właściciela**
(``Announcement.competition``, ``docs/UNIWERSALNY-ETAP-1.md`` § 3.7). Baner wisi na każdej stronie
serwisu, więc komunikat globalny dla instalacji znaczyłby, że organizator konkursu A ogłasza
przerwę techniczną na stronie konkursu B – i to bez żadnej możliwości zdjęcia jej przez tego,
kogo dotyczy. Zawężone jest jedno i drugie: zapytanie i pamięć podręczna.

Bez JavaScriptu baner jest w pełni sprawny: serwer renderuje go od razu, a znika wyłącznie
przycisk zamknięcia. To jest ta sama zasada, co przy pasku cookie.

Sam model (``Announcement``) stoi w ``apps.cms.models`` razem z resztą modeli aplikacji – tutaj
jest wyłącznie to, co się z nim **robi**: zapytanie o komunikaty „na teraz”, pamięć podręczna
i procesor kontekstu. Ten sam podział, co przy analityce (``apps.cms.analytics``) i warsztatach.
"""

from __future__ import annotations

from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Announcement, AnnouncementLevel
from .tenancy import competition_for_request, resolve_competition

#: Przedrostek i czas życia pamięci podręcznej aktywnych komunikatów. Baner renderuje się na
#: **każdej** stronie serwisu, więc bez tego każda odsłona kosztowałaby zapytanie do bazy. Minuta
#: jest kompromisem: redaktor, który ogłasza przerwę techniczną, nie czeka dłużej niż minutę, a przy
#: krótszym TTL pamięć przestaje cokolwiek oszczędzać. Zapis i skasowanie komunikatu i tak czyszczą
#: ją od razu (sygnały niżej) – TTL jest wyłącznie zabezpieczeniem dla pozostałych procesów.
#:
#: Klucz jest **per konkurs** i to jest sedno tej zmiany: jeden wspólny wpis znaczyłby, że baner
#: zbuforowany przy odsłonie konkursu A wyświetli się czytelnikowi konkursu B przez całą minutę –
#: czyli że izolacja zależy od tego, kto pierwszy wszedł na serwis.
CACHE_PREFIX = "cms:announcements:active"
CACHE_TTL_SECONDS = 60


#: Kolejność wag w banerze. Awaria pierwsza, informacja ostatnia – czytelnik, który przeczyta
#: tylko pierwszy komunikat, ma przeczytać ten najważniejszy.
LEVEL_ORDER = (AnnouncementLevel.DANGER, AnnouncementLevel.WARNING, AnnouncementLevel.INFO)


def cache_key(competition) -> str:
    """Klucz wpisu dla konkursu. ``None`` ma własny klucz, a nie wspólny z kimkolwiek.

    Osobny klucz dla „nie wiadomo, o który konkurs chodzi” jest tu istotny: pod tym kluczem leży
    **pusta** lista (zawężenie do ``None`` niczego nie widzi), a dzielenie go z jakimkolwiek
    konkursem znaczyłoby, że żądanie spod nieznanego hosta czyści albo zatruwa cudzy baner.
    """
    return f"{CACHE_PREFIX}:{getattr(competition, 'pk', None) or 'none'}"


def active_announcements(competition=None, now=None) -> list[Announcement]:
    """Komunikaty konkursu obowiązujące „na teraz”, od najważniejszego. Bez pamięci podręcznej.

    Warunek czasu jest ten sam, co w ``Announcement.is_live``, tylko wyrażony zapytaniem: włączony,
    po dacie startu i przed datą końca (pusta data końca = bez ograniczenia).

    Bez argumentu bierze konkurs „na teraz” (``apps.cms.tenancy.resolve_competition``) – tak wołają
    ją komendy i testy, które konkursu nie mają skąd podać. Zawężenie robi ``for_competition``
    z managera, a nie ``filter`` wpisany tutaj: droga modelu do konkursu jest własnością modelu,
    a nie decyzją wołającego (§ 3.5).
    """
    now = now or timezone.now()
    rows = (
        Announcement.objects.for_competition(resolve_competition(competition))
        .filter(is_active=True, starts_at__lte=now)
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
    )
    order = {value: index for index, value in enumerate(LEVEL_ORDER)}
    return sorted(rows, key=lambda item: (order.get(item.level, len(order)), -item.pk))


def cached_announcements(competition=None, now=None) -> list[Announcement]:
    """Aktywne komunikaty konkursu z pamięcią podręczną (60 s), unieważnianą przy zapisie.

    Baner renderuje się na każdej stronie serwisu, więc odczyt musi być tani. Błąd bazy nie może
    wywrócić szablonu bazowego – tak samo, jak w ``site_chrome`` i w menu CMS-a – więc kończy się
    pustą listą, czyli brakiem banera.

    Cache trzyma **listę obiektów**, a nie identyfikatory: wiersz jest krótki (kilkaset znaków),
    a drugie zapytanie po identyfikatorach zjadłoby całą oszczędność.

    Uwaga na wygaśnięcie okna: komunikat z ``ends_at`` w środku minuty zostanie na ekranie do
    końca TTL. Baner nie jest zegarem – minuta opóźnienia przy komunikacie, który i tak wisiał
    kilka godzin, jest ceną bez znaczenia.
    """
    competition = resolve_competition(competition)
    key = cache_key(competition)
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        rows = active_announcements(competition, now)
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli komunikatów
        return []
    cache.set(key, rows, CACHE_TTL_SECONDS)
    return rows


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętane listy **wszystkich** konkursów. Wołają to sygnały i testy.

    Czyszczenie hurtem, a nie klucza jednego konkursu, mimo że sygnał zna zapisany wiersz. Powód:
    komunikat da się przenieść między konkursami (``/admin/`` pozwala zmienić klucz obcy), a wtedy
    unieważnienie „po nowym właścicielu” zostawiłoby go na banerze starego aż do wygaśnięcia TTL.
    Zapis komunikatu jest rzadki, więc jedno zapytanie o listę konkursów jest tu ceną bez znaczenia
    – a odczyt banera, który wykonuje się przy każdej odsłonie, nie płaci za to nic.
    """
    cache.delete_many(_all_cache_keys())


def _all_cache_keys() -> list[str]:
    """Klucze wszystkich konkursów plus klucz „bez konkursu”.

    Import lokalny i osłona na błąd bazy, bo tę funkcję wywołuje sygnał ``post_save`` – ten sam,
    który chodzi w migracjach danych i w testach przewijających bazę, czyli w chwilach, w których
    tabela konkursów bywa jeszcze (albo już) nieosiągalna.
    """
    from apps.tenancy.models import Competition

    keys = [cache_key(None)]
    try:
        keys += [f"{CACHE_PREFIX}:{pk}" for pk in Competition.objects.values_list("pk", flat=True)]
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli konkursów
        pass
    return keys


@receiver(post_save, sender=Announcement, dispatch_uid="cms.announcements.reset_on_save")
@receiver(post_delete, sender=Announcement, dispatch_uid="cms.announcements.reset_on_delete")
def _reset_on_change(sender, **kwargs) -> None:
    """Zapis albo skasowanie komunikatu ma być widoczne od razu, a nie po upływie TTL.

    Sygnał czyści wpis w Redisie, czyli pamięć **współdzieloną** – w odróżnieniu od
    ``apps.cms.analytics``, gdzie pamięć jest lokalna dla procesu i sygnał działa tylko w nim.
    Tutaj wszystkie workery widzą nowy komunikat natychmiast po kliknięciu „Zapisz”.
    """
    reset_cache()


def announcements(request) -> dict:
    """Procesor kontekstu: aktywne komunikaty **tego** konkursu dla szablonu bazowego.

    Konkurs bierzemy z żądania (``request.competition``, ustawia je ``CompetitionMiddleware``),
    a przy jego braku – z kontekstu. Nie z „pierwszego w bazie”: baner wisi na każdej stronie
    serwisu, więc pomyłka tutaj jest ogłoszeniem jednego organizatora na stronie drugiego,
    widocznym dla wszystkich jego czytelników naraz.

    Panel redakcyjny i panel administracyjny baneru **nie** dostają: mają własną ramę (Wagtail,
    Django admin) i własny system powiadomień, a pasek organizatora wstrzyknięty w cudzy layout
    rozsypuje go, zamiast cokolwiek ogłosić. Rozpoznanie panelu jest to samo, co w polityce CSP
    (``apps.web.middleware.is_admin_path``) – jedna definicja „to jest panel” w całym projekcie.
    """
    from apps.web.middleware import is_admin_path

    if is_admin_path(request.path):
        return {"announcements": []}
    return {"announcements": cached_announcements(competition_for_request(request))}
