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

#: Klucz i czas życia pamięci podręcznej aktywnych komunikatów. Baner renderuje się na **każdej**
#: stronie serwisu, więc bez tego każda odsłona kosztowałaby zapytanie do bazy. Minuta jest
#: kompromisem: redaktor, który ogłasza przerwę techniczną, nie czeka dłużej niż minutę, a przy
#: krótszym TTL pamięć przestaje cokolwiek oszczędzać. Zapis i skasowanie komunikatu i tak czyszczą
#: ją od razu (sygnały niżej) – TTL jest wyłącznie zabezpieczeniem dla pozostałych procesów.
CACHE_KEY = "cms:announcements:active"
CACHE_TTL_SECONDS = 60


#: Kolejność wag w banerze. Awaria pierwsza, informacja ostatnia – czytelnik, który przeczyta
#: tylko pierwszy komunikat, ma przeczytać ten najważniejszy.
LEVEL_ORDER = (AnnouncementLevel.DANGER, AnnouncementLevel.WARNING, AnnouncementLevel.INFO)


def active_announcements(now=None) -> list[Announcement]:
    """Komunikaty obowiązujące „na teraz”, od najważniejszego. Bez pamięci podręcznej.

    Warunek jest ten sam, co w ``Announcement.is_live``, tylko wyrażony zapytaniem: włączony,
    po dacie startu i przed datą końca (pusta data końca = bez ograniczenia).
    """
    now = now or timezone.now()
    rows = Announcement.objects.filter(is_active=True, starts_at__lte=now).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    )
    order = {value: index for index, value in enumerate(LEVEL_ORDER)}
    return sorted(rows, key=lambda item: (order.get(item.level, len(order)), -item.pk))


def cached_announcements(now=None) -> list[Announcement]:
    """Aktywne komunikaty z pamięcią podręczną (60 s), unieważnianą przy zapisie.

    Baner renderuje się na każdej stronie serwisu, więc odczyt musi być tani. Błąd bazy nie może
    wywrócić szablonu bazowego – tak samo, jak w ``site_chrome`` i w menu CMS-a – więc kończy się
    pustą listą, czyli brakiem banera.

    Cache trzyma **listę obiektów**, a nie identyfikatory: wiersz jest krótki (kilkaset znaków),
    a drugie zapytanie po identyfikatorach zjadłoby całą oszczędność.

    Uwaga na wygaśnięcie okna: komunikat z ``ends_at`` w środku minuty zostanie na ekranie do
    końca TTL. Baner nie jest zegarem – minuta opóźnienia przy komunikacie, który i tak wisiał
    kilka godzin, jest ceną bez znaczenia.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        rows = active_announcements(now)
    except DatabaseError:  # pragma: no cover - baza bez migracji tabeli komunikatów
        return []
    cache.set(CACHE_KEY, rows, CACHE_TTL_SECONDS)
    return rows


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętaną listę. Wołają to sygnały zapisu i skasowania oraz testy."""
    cache.delete(CACHE_KEY)


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
    """Procesor kontekstu: aktywne komunikaty dla szablonu bazowego.

    Panel redakcyjny i panel administracyjny baneru **nie** dostają: mają własną ramę (Wagtail,
    Django admin) i własny system powiadomień, a pasek organizatora wstrzyknięty w cudzy layout
    rozsypuje go, zamiast cokolwiek ogłosić. Rozpoznanie panelu jest to samo, co w polityce CSP
    (``apps.web.middleware.is_admin_path``) – jedna definicja „to jest panel” w całym projekcie.
    """
    from apps.web.middleware import is_admin_path

    if is_admin_path(request.path):
        return {"announcements": []}
    return {"announcements": cached_announcements()}
