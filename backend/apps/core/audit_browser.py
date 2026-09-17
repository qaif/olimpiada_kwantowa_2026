"""Czytanie śladu audytowego: filtry i stronicowanie dla przeglądarki koordynatora.

``apps.core.models`` odpowiada za **zapis** audytu (helper ``audit``) i za jego niezmienność.
Ten moduł jest drugą stroną tej samej tabeli – odczytem – i mieszka osobno, żeby model nie
zaczął wiedzieć, jak wygląda ekran, który go pokazuje.

Czego ten moduł **nie** robi i nie ma robić:

- nie dokłada do wierszy ani jednej informacji, której wpis audytowy sam nie niesie. Rekord ma
  z założenia zero danych osobowych w ``diff`` (patrz docstring ``apps.core.models``), a jedyną
  osobą w wierszu jest wykonawca – i to jego adres służy za filtr. Doklejenie tu nazwiska
  uczestnika „dla czytelności” zamieniłoby ślad techniczny w wyciąg z bazy osobowej,
- nie pozwala niczego zmienić. Wpis audytowy jest niemodyfikowalny i przeglądarka jest wyłącznie
  przeglądarką.

Filtry są składane z parametrów adresu (zwykły formularz GET, bez skryptu), więc wynik da się
odświeżyć, zapisać w zakładkach i przesłać odnośnikiem – a tego zwykle chce ktoś, kto właśnie
znalazł w audycie odpowiedź na czyjeś pytanie.

**Czego ten moduł nie robi: nie zawęża do konkursu.** Od wydania D robi to manager
(``AuditLog.objects.visible_to``, § 3.9) i robi to **w widoku**, na wyniku :func:`entries` –
obie warstwy są zwykłymi ``filter`` na tym samym querysecie, więc składają się w jedno zdanie SQL.
Rozdzielenie jest celowe: „co koordynator wybrał w formularzu” i „czyje wpisy wolno mu zobaczyć”
to dwa różne pytania, a wpisanie drugiego tutaj znaczyłoby, że filtr adresu i reguła widoczności
mogą się rozjechać po pierwszej zmianie jednego z nich. :func:`known_actions`
i :func:`known_target_types` odpowiadają więc o **całą tabelę** i są odpowiedzią dla operatora
platformy; listy wyboru w panelu koordynatora budują się z querysetu już zawężonego.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from .models import AuditLog

#: Ile wpisów na stronę. Sto, bo audyt czyta się „w dół” – szuka się zdarzenia z okolic konkretnej
#: godziny, a nie pojedynczego wiersza po identyfikatorze. Przy pięćdziesięciu stronicowanie
#: zaczynałoby przeszkadzać, przy tysiącu strona robiłaby się ciężka od rozwiniętych ``<details>``.
AUDIT_PAGE_SIZE = 100

#: Format daty w polach filtra (``<input type="date">`` wysyła dokładnie to).
DATE_FORMAT = "%Y-%m-%d"


def parse_date(value: str | None) -> datetime | None:
    """Data z parametru adresu jako **początek tego dnia w czasie lokalnym**, albo ``None``.

    Lokalny, a nie UTC: koordynator, który wpisuje „od 14 marca”, ma na myśli polski dzień, a nie
    przedział zaczynający się o 1:00 albo 2:00 rano. Nieparsowalna wartość jest traktowana jak
    brak filtra – adres z literówką ma pokazać listę, a nie stronę błędu.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        day = datetime.strptime(text, DATE_FORMAT).date()
    except ValueError:
        return None
    return timezone.make_aware(datetime.combine(day, time.min), timezone.get_current_timezone())


def entries(params) -> QuerySet[AuditLog]:
    """Wpisy audytu zawężone parametrami adresu, od najnowszego.

    Obsługiwane parametry: ``actor`` (fragment adresu e-mail wykonawcy), ``action`` (dokładna
    nazwa akcji z listy), ``target_type`` (``app.model``), ``from``/``to`` (daty lokalne).
    Każdy pusty parametr jest po prostu pomijany – filtr „wszystko” nie jest błędem.

    Przedział dat jest **obustronnie domknięty**: „do 14 marca” obejmuje cały ten dzień. Bez
    dołożonej doby granica wypadałaby o północy i wynik nie zawierałby ani jednego zdarzenia
    z dnia, który człowiek wpisał.

    ``select_related("actor")``, bo wykonawca stoi w każdym wierszu: bez tego strona stu wpisów
    kosztowałaby sto zapytań.
    """
    queryset = AuditLog.objects.select_related("actor").all()
    actor = (params.get("actor") or "").strip()
    if actor:
        queryset = queryset.filter(actor__email__icontains=actor)
    action = (params.get("action") or "").strip()
    if action:
        queryset = queryset.filter(action=action)
    target_type = (params.get("target_type") or "").strip()
    if target_type:
        queryset = queryset.filter(target_type=target_type)
    since = parse_date(params.get("from"))
    if since is not None:
        queryset = queryset.filter(at__gte=since)
    until = parse_date(params.get("to"))
    if until is not None:
        queryset = queryset.filter(at__lt=until + timedelta(days=1))
    return queryset.order_by("-at", "-id")


def known_actions() -> list[str]:
    """Nazwy akcji występujące w tabeli, alfabetycznie – zawartość listy wyboru w filtrze.

    Lista pochodzi z **danych**, a nie ze spisu stałych w kodzie, i to jest jej sens: koordynator
    ma wybierać spośród zdarzeń, które naprawdę padły w tej instalacji, a nie spośród wszystkich,
    jakie system potrafi zapisać. Nazwa akcji, której nikt jeszcze nie wywołał, byłaby filtrem
    gwarantującym pustą listę.
    """
    return sorted(AuditLog.objects.values_list("action", flat=True).distinct())


def known_target_types() -> list[str]:
    """Typy obiektów (``app.model``) występujące w tabeli, alfabetycznie."""
    return sorted(AuditLog.objects.values_list("target_type", flat=True).distinct())
