"""Co reguła wygenerowałaby i kiedy – czyste funkcje, bez zapisu i bez zapytań (MSG-AUTO-01).

Moduł odpowiada na dwa pytania: „jakie są wystąpienia tej reguły” (warsztaty z harmonogramu albo
dni przed terminem etapu, każdy z terminem wysyłki i wypełnionym tekstem) i „czy szablon jest
poprawny”. Zapis komunikatów, idempotencja i wycofywanie nieaktualnych mieszkają w ``services``.
Rozdział jest celowy: reguły czasu (strefa, zmiana czasu, „N godzin przed”) i szablonu da się
przetestować bez bazy, a to one najłatwiej psują się po cichu.

**Czas polski.** Termin „w dniu warsztatu o 06:00” to 06:00 czasu ściennego w ``TIME_ZONE``
(Europe/Warsaw) – także w dniu zmiany czasu – a „2 godziny przed początkiem” to dwie rzeczywiste
godziny, liczone w UTC (patrz ``RuleTiming``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field

from django.utils import timezone
from django.utils.translation import gettext

from apps.accounts.messaging import SCHEDULE_MIN_LEAD

from .models import RuleKind, RuleTiming

#: Jak daleko naprzód beat zamienia wystąpienia w komunikaty planowane. Tydzień: najbliższy
#: warsztat (zwykle co tydzień) jest zawsze widoczny w „Zaplanowanych” i da się go anulować,
#: a zmiana w harmonogramie dalej niż za tydzień nie wymaga wycofywania czegokolwiek.
GENERATION_HORIZON = dt.timedelta(days=7)

#: Ile wystąpień jednej reguły powstaje w jednym przebiegu – bezpiecznik na wypadek reguły „0 dni”
#: przy harmonogramie z kilkudziesięcioma wierszami w jednym tygodniu.
MAX_PER_RUN = 20

#: Pola szablonu dla każdego rodzaju reguły. Kolejność = kolejność w podpowiedzi na ekranie.
PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    RuleKind.WORKSHOP: ("temat", "prowadzacy", "data", "godziny", "link"),
    RuleKind.STAGE_DEADLINE: ("etap", "termin", "dni", "link"),
}

#: Pisownia z polskim znakiem też działa – koordynator pisze „{prowadzący}” odruchowo.
ALIASES = {"prowadzący": "prowadzacy"}

#: ``{nazwa}`` – bez spacji i bez zagnieżdżeń. Pojedynczy nawias klamrowy w treści (np. w kodzie
#: Qiskit wklejonym do przypomnienia) nie jest polem i zostaje literą.
PLACEHOLDER = re.compile(r"\{([^{}\s]{1,40})\}")

#: Pierwsza godzina w kolumnie „godziny”: „11:00–14:00”, „11.00 – 13.00”, „godz. 9:30”.
START_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")

#: Dopisek prowadzącego na końcu tematu – ta sama reguła, co w ``seed_forum_categories``.
LECTURER_SUFFIX = re.compile(r"\s*\(\s*prowadzący\s*:\s*(?P<lecturer>[^()]*)\)\s*$")

#: Znaczniki odwołania w temacie, terminie albo godzinach wiersza harmonogramu („ODWOŁANE”,
#: „warsztat odwołany”, „cancelled”). Blok harmonogramu nie ma pola „odwołany”, a redaktor
#: odwołuje zajęcia, dopisując to w tabeli – i tak ma zostać na stronie /warsztaty/.
CANCELLED_MARKERS = ("odwoł", "cancel")

#: Wartość pola, którego harmonogram nie podaje (prowadzący, godziny) – zdanie w liście ma się
#: dać przeczytać, a „Prowadzący: ” z pustką wygląda jak błąd.
UNKNOWN = "do potwierdzenia"

MONTHS_GENITIVE = (
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
)

#: Najdłuższy temat listu (``MessageBroadcast.subject``).
SUBJECT_MAX = 200


@dataclass(frozen=True)
class Occurrence:
    """Jedno wystąpienie reguły: kiedy wysłać, co i dlaczego (opis źródła dla ekranu)."""

    key: str
    send_at: dt.datetime
    subject: str
    body: str
    label: str


@dataclass
class Plan:
    """Wszystkie wystąpienia reguły (od najwcześniejszego) i wiersze pominięte z powodem."""

    occurrences: list[Occurrence] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# --- szablon ---------------------------------------------------------------------------------------


def placeholder_names(text: str) -> list[str]:
    """Pola użyte w tekście (po zamianie pisowni z „ą”), w kolejności wystąpienia, bez powtórzeń."""
    names: list[str] = []
    for raw in PLACEHOLDER.findall(text or ""):
        name = ALIASES.get(raw, raw)
        if name not in names:
            names.append(name)
    return names


def unknown_placeholders(kind: str, text: str) -> list[str]:
    """Pola tekstu spoza listy rodzaju reguły – posortowane, bez powtórzeń."""
    allowed = PLACEHOLDERS[kind]
    return sorted({name for name in placeholder_names(text) if name not in allowed})


def unknown_error(kind: str, unknown: list[str]) -> str:
    """Zdanie błędu „nieznane pola” z listą dozwolonych – jedno brzmienie dla formularza i serwisu."""
    return gettext("Nieznane pola szablonu: %(unknown)s. Dozwolone: %(allowed)s.") % {
        "unknown": ", ".join("{" + name + "}" for name in unknown),
        "allowed": ", ".join("{" + name + "}" for name in PLACEHOLDERS[kind]),
    }


def link_missing_error() -> str:
    return gettext("Szablon używa pola {link}, a pole „Link” jest puste.")


def template_errors(kind: str, *, subject: str, body: str, link: str) -> list[str]:
    """Błędy szablonu reguły – lista zdań; pusta, gdy szablon jest dobry.

    Nieznane pole to błąd **przy zapisie**, a nie pusty napis w liście: literówka „{prowadzacz}”
    wyszłaby do kilku tysięcy osób dosłownie. Z tego samego powodu ``{link}`` bez wpisanego linku
    jest błędem – list „Link do spotkania: ” bez adresu jest gorszy niż brak listu.
    """
    errors = []
    unknown = sorted(set(unknown_placeholders(kind, subject)) | set(unknown_placeholders(kind, body)))
    if unknown:
        errors.append(unknown_error(kind, unknown))
    if "link" in placeholder_names(subject) + placeholder_names(body) and not (link or "").strip():
        errors.append(link_missing_error())
    return errors


def render(text: str, values: dict[str, str], *, single_line: bool = False) -> str:
    """Szablon z wypełnionymi polami. Pole spoza ``values`` zostaje dosłownie (walidacja go nie wpuści).

    ``single_line`` – temat listu: bez znaków nowej linii (nagłówek ``Subject``) i najwyżej
    ``SUBJECT_MAX`` znaków, bo dłuższy temat nie przeszedłby walidacji komunikatu.
    """

    def substitute(match: re.Match) -> str:
        name = ALIASES.get(match.group(1), match.group(1))
        return values[name] if name in values else match.group(0)

    result = PLACEHOLDER.sub(substitute, text or "")
    if single_line:
        result = " ".join(result.split())[:SUBJECT_MAX]
    return result


def polish_date(value: dt.date) -> str:
    """„28 lutego 2027” – bez zależności od języka aktywnego w procesie beatu."""
    return f"{value.day} {MONTHS_GENITIVE[value.month - 1]} {value.year}"


def days_phrase(days: int) -> str:
    """„1 dzień”, „7 dni” – liczba z rzeczownikiem, żeby „zostało {dni}” brzmiało po polsku."""
    return "1 dzień" if days == 1 else f"{days} dni"


def split_topic(topic: str) -> tuple[str, str]:
    """Temat bez dopisku „(prowadzący: …)” i sam dopisek (pusty, gdy go nie ma)."""
    match = LECTURER_SUFFIX.search(topic or "")
    if match is None:
        return (topic or "").strip(), ""
    return topic[: match.start()].strip(), match.group("lecturer").strip()


def workshop_values(row: dict, link: str) -> dict[str, str]:
    """Wartości pól szablonu warsztatu z wiersza harmonogramu."""
    topic, suffix = split_topic(row.get("topic", ""))
    date_value = row.get("date_value")
    when = (row.get("date") or "").strip() or (
        polish_date(date_value) if isinstance(date_value, dt.date) else ""
    )
    return {
        "temat": topic,
        "prowadzacy": (row.get("lecturer") or "").strip() or suffix or UNKNOWN,
        "data": when,
        "godziny": (row.get("time") or "").strip() or UNKNOWN,
        "link": link or "",
    }


def stage_values(stage, days: int, link: str) -> dict[str, str]:
    """Wartości pól szablonu terminu etapu; termin w czasie polskim."""
    deadline = timezone.localtime(stage.deadline_at, timezone.get_default_timezone())
    return {
        "etap": stage.display_name,
        "termin": f"{polish_date(deadline.date())}, {deadline:%H:%M}",
        "dni": days_phrase(days),
        "link": link or "",
    }


# --- terminy ---------------------------------------------------------------------------------------


def local_at(day: dt.date, at: dt.time) -> dt.datetime:
    """``day`` o godzinie ``at`` czasu polskiego jako chwila ze strefą (ściennie, także przy DST)."""
    return timezone.make_aware(dt.datetime.combine(day, at), timezone.get_default_timezone())


def hours_before(start: dt.datetime, hours: int) -> dt.datetime:
    """``hours`` **rzeczywistych** godzin przed ``start`` – arytmetyka w UTC, wynik w czasie polskim.

    Odejmowanie ``timedelta`` od chwili ze strefą ``zoneinfo`` jest w Pythonie ścienne: 11:00 w dniu
    zmiany czasu minus 24 h dałoby 11:00 dnia poprzedniego, czyli 25 albo 23 godziny.
    """
    moment = start.astimezone(dt.UTC) - dt.timedelta(hours=hours)
    return moment.astimezone(timezone.get_default_timezone())


def start_time(text: str) -> dt.time | None:
    """Godzina początku z kolumny „godziny” albo ``None``, gdy nie da się jej odczytać."""
    match = START_TIME.search(text or "")
    if match is None:
        return None
    return dt.time(int(match.group(1)), int(match.group(2)))


def is_cancelled(row: dict) -> bool:
    """Czy wiersz harmonogramu jest odwołany (znacznik w temacie, terminie albo godzinach)."""
    text = " ".join(str(row.get(name) or "") for name in ("topic", "date", "time")).casefold()
    return any(marker in text for marker in CANCELLED_MARKERS)


def schedule_rows(page) -> list[dict]:
    """**Wszystkie** wiersze harmonogramu strony „Warsztaty”, także bez daty – z kluczem warsztatu.

    ``apps.cms.workshops.workshop_rows`` oddaje wyłącznie wiersze z datą; tu potrzebujemy też
    pozostałych, żeby podgląd reguły powiedział, co pominął i dlaczego.
    """
    from apps.cms.workshops import workshop_key

    if page is None:
        return []
    rows = []
    for block in page.body:
        if block.block_type != "schedule":
            continue
        for row in block.value.get("rows", []):
            date_value = row.get("date_value")
            dated = isinstance(date_value, dt.date)
            rows.append(
                {
                    "topic": row.get("topic", ""),
                    "date": row.get("date", ""),
                    "date_value": date_value if dated else None,
                    "time": row.get("time", "") or "",
                    "lecturer": row.get("lecturer", "") or "",
                    "key": workshop_key(row.get("topic", ""), date_value) if dated else "",
                }
            )
    return rows


def _day_offsets(rule) -> list[int]:
    # Wyłącznie liczby nieujemne – „-1 dzień przed” byłby listem po wydarzeniu, a powłoka nie ma formularza.
    return sorted({int(value) for value in (rule.days_before or []) if str(value).isdigit()}, reverse=True)


def plan_workshops(rule, rows: list[dict]) -> Plan:
    """Wystąpienia reguły warsztatowej z wierszy harmonogramu (``schedule_rows``)."""
    plan = Plan()
    for row in rows:
        topic = split_topic(row.get("topic", ""))[0] or row.get("topic", "")
        if not isinstance(row.get("date_value"), dt.date):
            plan.skipped.append(gettext("„%(topic)s” – bez daty w harmonogramie") % {"topic": topic})
            continue
        if is_cancelled(row):
            plan.skipped.append(gettext("„%(topic)s” – odwołany") % {"topic": topic})
            continue
        values = workshop_values(row, rule.link)
        label = f"{topic} – {values['data']}"
        if rule.timing == RuleTiming.HOURS_BEFORE:
            begins = start_time(row.get("time", ""))
            if begins is None or not rule.hours_before:
                plan.skipped.append(gettext("„%(topic)s” – brak godziny początku") % {"topic": topic})
                continue
            send_at = hours_before(local_at(row["date_value"], begins), rule.hours_before)
            plan.occurrences.append(
                _occurrence(rule, f"w:{row['key']}:h{rule.hours_before}", send_at, values, label)
            )
            continue
        for days in _day_offsets(rule):
            send_at = local_at(row["date_value"] - dt.timedelta(days=days), rule.send_time)
            plan.occurrences.append(_occurrence(rule, f"w:{row['key']}:d{days}", send_at, values, label))
    plan.occurrences.sort(key=lambda occurrence: (occurrence.send_at, occurrence.key))
    return plan


def plan_stage(rule, stage) -> Plan:
    """Wystąpienia reguły „termin etapu”: po jednym na każdą liczbę dni przed ``deadline_at``.

    Etap treningowy (``has_deadline`` fałszywe – termin techniczny w 2099 r.) i etap innego konkursu
    nie dają niczego: przypomnienie o terminie, którego nie ma albo który nie jest nasz, nie wychodzi.
    """
    plan = Plan()
    if stage is None:
        plan.skipped.append(gettext("Reguła nie ma etapu – etap usunięto albo nie wybrano go."))
        return plan
    if not stage.has_deadline or stage.edition.competition_id != rule.competition_id:
        plan.skipped.append(
            gettext("Etap „%(stage)s” nie ma terminu do przypomnienia.") % {"stage": stage.display_name}
        )
        return plan
    deadline_day = timezone.localtime(stage.deadline_at, timezone.get_default_timezone()).date()
    for days in _day_offsets(rule):
        values = stage_values(stage, days, rule.link)
        send_at = local_at(deadline_day - dt.timedelta(days=days), rule.send_time)
        label = f"{stage.display_name} – {values['dni']}"
        plan.occurrences.append(_occurrence(rule, f"s:{stage.pk}:d{days}", send_at, values, label))
    plan.occurrences.sort(key=lambda occurrence: (occurrence.send_at, occurrence.key))
    return plan


def _occurrence(rule, key: str, send_at: dt.datetime, values: dict, label: str) -> Occurrence:
    return Occurrence(
        key=key,
        send_at=send_at,
        subject=render(rule.subject_template, values, single_line=True),
        body=render(rule.body_template, values),
        label=label,
    )


def in_window(send_at: dt.datetime, now: dt.datetime) -> bool:
    """Czy wystąpienie ma powstać **teraz**: od ``SCHEDULE_MIN_LEAD`` do ``GENERATION_HORIZON`` naprzód.

    Dolna granica jest granicą ``schedule_broadcast`` (5 minut) – wystąpienie bliższe nie da się
    zaplanować i zostaje pominięte, zamiast kończyć się błędem w każdym przebiegu.
    """
    return now + SCHEDULE_MIN_LEAD <= send_at <= now + GENERATION_HORIZON


def fingerprint(rule, occurrence: Occurrence) -> str:
    """Skrót tego, co komunikat wystąpienia niesie: termin, temat, treść, grupa i jej parametry."""
    payload = json.dumps(
        [
            occurrence.send_at.astimezone(dt.UTC).isoformat(),
            occurrence.subject,
            occurrence.body,
            rule.group,
            rule.parameters or {},
        ],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
