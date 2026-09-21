"""Zapowiedź najbliższych warsztatów online – wspólna reguła dla strony ``/warsztaty/`` i głównej.

Harmonogram warsztatów jest **treścią redakcyjną**: system zawodów o warsztatach nic nie wie,
bo nie egzekwuje ich terminów. Mieszka więc tam, gdzie go wpisano – w bloku ``schedule`` w treści
strony „Warsztaty” – i to jest jedyne jego źródło. Strona główna nie ma własnej listy warsztatów
do utrzymania: czyta tę samą tabelę i pokazuje z niej trzy najbliższe wiersze.

Dlaczego osobny moduł, a nie metoda ``HomePage``: ta reguła (co znaczy „najbliższe”) jest
odpowiedzią na pytanie o treść strony „Warsztaty”, a nie o układ strony głównej, i musi dać się
przetestować bez budowania drzewa stron. Moduł świadomie **nie importuje** ``apps.cms.models``
– dostaje stronę z zewnątrz – bo ``models`` importuje ten moduł, a w drugą stronę byłby cykl.

Terminarz etapów zawodów to osobna sprawa i osobny moduł (``apps.cms.timeline``): tam daty
pochodzą z bazy i strona nie ma prawa ogłaszać innych niż serwer.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone
from django.utils.text import slugify

#: Slug strony z harmonogramem warsztatów. Zapowiedź na stronie głównej szuka jej po slugu, bo
#: tytuł jest redakcyjny (może brzmieć „Warsztaty online”), a adres wisi w komunikatach.
WORKSHOPS_SLUG = "warsztaty"

#: Ile warsztatów pokazuje zapowiedź na stronie głównej. Trzy, bo tyle mieści się w jednym rzędzie
#: kart obok siebie, a zapowiedź ma odpowiadać na „co najbliżej”, nie zastępować całej tabeli.
UPCOMING_LIMIT = 3


def workshops_page(competition=None):
    """Strona „Warsztaty” **tego** konkursu albo ``None``.

    Jedno wejście dla wszystkich, którzy tej strony szukają po slugu: linii czasu w nagłówku,
    zaświadczeń o obecności i panelu koordynatora. Bez tego każdy z nich pisał
    ``ContentPage.objects.live().filter(slug=WORKSHOPS_SLUG).first()``, czyli pytał o „jakąkolwiek
    stronę o tym slugu w tej bazie” – a w instalacji wielokonkursowej „jakakolwiek” bywa cudza
    i pasek jednej olimpiady wyliczałby warsztaty drugiej.

    Zawężenie idzie przez **drzewo stron**, a nie przez klucz obcy: strona należy do konkursu przez
    witrynę, w której poddrzewie stoi (``Competition.site``), i drugiej drogi do tej prawdy nie ma.
    Warunek na ``path`` jest indeksowany (treebeard trzyma ścieżkę materializowaną), więc kosztuje
    tyle samo, co dotychczasowy filtr po slugu.

    Konkurs bez witryny, konkurs ``None`` i baza bez konkursów zachowują się tak, jak przed
    wielokonkursowością: pytanie zostaje globalne. To jest jedyny wariant, w którym może oddać
    cudzą stronę, i dotyczy wyłącznie instalacji, w której cudzej nie ma.

    Import modelu jest w środku funkcji – ``apps.cms.models`` importuje ten moduł, więc na poziomie
    pliku byłby to cykl. Tą samą drogą chodzi tu ``attended_workshops``.
    """
    from .models import ContentPage

    pages = ContentPage.objects.live().filter(slug=WORKSHOPS_SLUG)
    root = getattr(getattr(competition, "site", None), "root_page", None)
    if root is not None:
        pages = pages.descendant_of(root, inclusive=True)
    return pages.first()


def upcoming_workshops(page, *, now=None, limit: int = UPCOMING_LIMIT) -> list[dict]:
    """Najbliższe warsztaty z bloków ``schedule`` w treści strony, od najwcześniejszego.

    ``page`` bywa ``None`` (strony „Warsztaty” jeszcze nie ma albo jest szkicem) – wynikiem jest
    wtedy pusta lista, a nie wyjątek: sekcja na stronie głównej ma po prostu zniknąć.

    Wiersz bez odczytanej daty (``date_value``) zostaje pominięty. To nie jest awaria – termin
    w harmonogramie redakcyjnym bywa nieostry, a zapowiedź może pokazać tylko to, co umie
    uszeregować względem zegara. W tabeli na stronie „Warsztaty” taki wiersz stoi normalnie.

    Granicą jest **dzień**, a nie godzina: w tabeli stoi data i (tekstem) godziny, więc warsztat
    dzisiejszy jest jeszcze „najbliższy” – godziny bywają zresztą podane jako „do potwierdzenia”
    i nie ma czego porównywać.
    """
    if page is None:
        return []
    today = _today(now)
    rows = [
        {
            "topic": row.get("topic", ""),
            "date": row.get("date", ""),
            "date_value": row["date_value"],
            "time": row.get("time", ""),
        }
        for block in page.body
        if block.block_type == "schedule"
        for row in block.value.get("rows", [])
        if isinstance(row.get("date_value"), date) and row["date_value"] >= today
    ]
    rows.sort(key=lambda row: row["date_value"])
    return rows[:limit]


#: Ile znaków ma klucz warsztatu. Mieści datę (10), myślnik i slug tematu – dłuższy temat jest
#: ucinany, bo klucz ma identyfikować wiersz, a nie go cytować.
WORKSHOP_KEY_LENGTH = 120


def workshop_key(topic: str, date_value: date) -> str:
    """Trwały identyfikator wiersza harmonogramu: ``2026-11-12-kubity-i-bramki``.

    Klucz z **daty i tematu**, a nie z pozycji wiersza w bloku ani z identyfikatora StreamFielda.
    Powód jest jeden i praktyczny: obecność na warsztatach zapisuje się przy tym kluczu i ma
    przeżyć redakcję strony. Redaktor dopisze wcześniejszy termin na początku tabeli, poprawi
    literówkę w godzinach albo przeniesie blok – i gdyby klucz zależał od kolejności, komplet
    odhaczonych obecności przesunąłby się na sąsiednie zajęcia.

    Cena tej decyzji jest jawna: zmiana **tematu albo daty** tworzy nowy klucz i stara obecność
    przestaje do wiersza pasować. To jest właściwy kompromis – zmiana tematu znaczy, że to inne
    zajęcia, a przesunięcie terminu koordynator widzi w tabeli obecności jako pustą kolumnę
    i może ją odhaczyć ponownie.
    """
    return f"{date_value.isoformat()}-{slugify(topic)}"[:WORKSHOP_KEY_LENGTH]


def attended_workshops(participant, competition=None) -> list[dict]:
    """Warsztaty, na których ten uczestnik był – w kolejności kalendarza.

    Przecięcie dwóch źródeł: odhaczonych kluczy (``cms.WorkshopAttendance``) i bieżącego
    harmonogramu redakcyjnego. Wiersza, którego w harmonogramie już nie ma (temat zmieniono,
    zajęcia odwołano), zaświadczenie **nie** wymienia: dokument ma wyliczać zajęcia, o których
    da się dziś powiedzieć, kiedy się odbyły i czego dotyczyły, a nie sam klucz z bazy.

    Harmonogram bierzemy ze strony **konkursu uczestnika** (``workshops_page``), a nie
    z jakiejkolwiek strony o slugu ``warsztaty``: zaświadczenie wystawia konkretny organizator
    i ma wyliczać zajęcia, które sam prowadził. Bez wskazania konkursu wchodzi odwrót „konkurs
    na teraz”, czyli w instalacji jednokonkursowej dokładnie dzisiejsze zachowanie.

    Import modeli jest wewnątrz funkcji – ``apps.cms.models`` importuje ten moduł, więc na
    poziomie pliku byłby to cykl. Tą samą drogą chodzi tu ``apps.cms.timeline``.
    """
    from .models import WorkshopAttendance
    from .tenancy import resolve_competition

    keys = set(
        WorkshopAttendance.objects.filter(participant=participant).values_list("workshop_key", flat=True)
    )
    if not keys:
        return []
    return [
        row for row in workshop_rows(workshops_page(resolve_competition(competition))) if row["key"] in keys
    ]


def workshop_rows(page) -> list[dict]:
    """**Wszystkie** warsztaty z odczytaną datą, od najwcześniejszego – dla paska w nagłówku.

    Różnica wobec ``upcoming_workshops`` jest jedna i wynika z pytania, na które odpowiada każda
    z funkcji. Zapowiedź na stronie głównej pyta „co dalej”, więc odcina przeszłość i kończy na
    trzech wierszach. Linia czasu w nagłówku pyta „jak wygląda cała edycja” – minione warsztaty
    są w niej tak samo potrzebne, jak minione etapy, bo to one pokazują, ile drogi już za nami.

    Daty nie parsujemy tutaj: robi to import treści (``legacy_markdown.parse_polish_date``) i to
    on jest miejscem, w którym polszczyzna zamienia się w ``date``. Wiersz z terminem nieostrym
    („do potwierdzenia”) nie ma ``date_value`` i po prostu nie trafia na oś – w tabeli na stronie
    „Warsztaty” stoi normalnie, bo tam jest tekstem, a nie punktem na osi.

    ``time`` wchodzi do wyniku z tego samego powodu, co w ``upcoming_workshops``: godziny są
    treścią tabeli, a nie tylko zapowiedzi na stronie głównej – kto czyta wiersz spoza tych trzech
    najbliższych (np. komenda zasiewająca działy forum jednym na warsztat), ma dostać to samo pole,
    zamiast czytać blok jeszcze raz po swojemu.
    """
    if page is None:
        return []
    rows = [
        {
            "topic": row.get("topic", ""),
            "date_value": row["date_value"],
            # Brzmienie terminu tak, jak podał go organizator – to ono stoi na zaświadczeniu,
            # bo dokument cytuje harmonogram, a nie przepisuje datę po swojemu.
            "date": row.get("date", ""),
            "time": row.get("time", ""),
            "lecturer": row.get("lecturer", "") or "",
            "key": workshop_key(row.get("topic", ""), row["date_value"]),
        }
        for block in page.body
        if block.block_type == "schedule"
        for row in block.value.get("rows", [])
        if isinstance(row.get("date_value"), date)
    ]
    rows.sort(key=lambda row: row["date_value"])
    return rows


def _today(now=None) -> date:
    """Dzisiejszy dzień w strefie serwisu. ``now`` (aware albo naive) dla testów bez freezegunu."""
    if now is None:
        return timezone.localdate()
    if timezone.is_aware(now):
        return timezone.localtime(now).date()
    return now.date()


def save_attendance(
    participant_ids: list[int], keys: list[str], marked: set[tuple[int, str]], *, actor=None
) -> dict:
    """Zapisuje **jedną stronę** tabeli obecności i zwraca ``{"added": n, "removed": n}``.

    Zakres zapisu jest tu najważniejszą decyzją. Formularz przysyła wyłącznie kratki zaznaczone,
    więc „czego nie ma w POST-cie, tego nie było” dałoby się zastosować albo do całej bazy, albo
    do tego, co koordynator naprawdę widział. Bierzemy drugie: kasujemy obecności **tylko** dla
    uczestników z tej strony i warsztatów z tej tabeli. Inaczej przejście na drugą stronę listy
    i zapisanie jej kasowałoby obecności wszystkich pozostałych – po cichu i nieodwracalnie.

    Zapis idzie hurtem (jedno zapytanie kasujące i jedno wstawiające), bo strona ma sto wierszy
    razy kilkanaście warsztatów; pętla z zapisem w środku to półtora tysiąca zapytań na jedno
    kliknięcie „Zapisz”.
    """
    from .models import WorkshopAttendance

    if not participant_ids or not keys:
        return {"added": 0, "removed": 0}
    existing = {
        (row.participant_id, row.workshop_key): row.pk
        for row in WorkshopAttendance.objects.filter(
            participant_id__in=participant_ids, workshop_key__in=keys
        )
    }
    wanted = {pair for pair in marked if pair[0] in set(participant_ids) and pair[1] in set(keys)}
    stale = [pk for pair, pk in existing.items() if pair not in wanted]
    added = [pair for pair in wanted if pair not in existing]
    if stale:
        WorkshopAttendance.objects.filter(pk__in=stale).delete()
    if added:
        WorkshopAttendance.objects.bulk_create(
            [
                WorkshopAttendance(
                    participant_id=participant_id,
                    workshop_key=key,
                    created_by=actor if getattr(actor, "is_authenticated", False) else None,
                )
                for participant_id, key in added
            ],
            # Wyścig dwóch koordynatorów zapisujących tę samą stronę kończy się pominięciem
            # duplikatu, a nie błędem 500 na ekranie tego, kto kliknął drugi.
            ignore_conflicts=True,
        )
    return {"added": len(added), "removed": len(stale)}
