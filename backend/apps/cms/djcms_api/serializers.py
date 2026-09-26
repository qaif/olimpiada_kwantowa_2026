"""Obiekty zawodów → JSON dla wersji ``dj.`` (kształty: ``docs/tasks/DJ-01.md`` § 3.2).

Trzy reguły wspólne dla każdej funkcji tego modułu:

- **gotowe napisy obok wartości surowych.** Każda data idzie jako ISO 8601 z offsetem **i** jako
  napis sformatowany tymi samymi filtrami, których używają szablony Wagtaila (``local_time``,
  ``local_datetime``, ``event_dates``, ``points``). Szablony ``dj.`` nie formatują danych zawodów
  same – inaczej termin finału miałby na dwóch wersjach serwisu dwa brzmienia,
- **biała lista kluczy.** Każdy DTO jest składany od zera z jawnie wypisanych pól, a nie przez
  ``model_to_dict`` czy kopię słownika. Dotyczy to zwłaszcza wierszy tabeli wyników: klucz
  dopisany kiedyś do snapshotu (albo wstrzyknięty ręcznie do JSON-a w bazie) nie przejdzie dalej,
  dopóki ktoś świadomie nie dopisze go tutaj,
- **adresy przez ``api_href``.** Pod ``dj.`` istnieją wyłącznie strony z drzewa Wagtaila (po
  imporcie), więc tylko ich ścieżki zostają względne; każdy adres aplikacji (logowanie, wyniki,
  PDF zadania, dokument, media) staje się bezwzględnym adresem domeny głównej.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from apps.core.points import format_points, points_json
from apps.web.templatetags.web_extras import edition_title, event_dates, local_datetime, local_time

#: Nazwa wzorca catch-alla Wagtaila (``wagtail.urls``). Adres, który rozwiązuje się na niego, jest
#: ścieżką strony z drzewa – ta sama ścieżka istnieje po imporcie na ``dj.``.
WAGTAIL_SERVE_URL_NAME = "wagtail_serve"

#: Jedyne schematy adresu bezwzględnego, które przepuszczamy. ``javascript:``, ``data:`` i reszta
#: dają pusty napis – szablon ``dj.`` wstawia te adresy do ``href`` i ``src``.
SAFE_SCHEMES = frozenset({"http", "https"})


def main_url(path: str) -> str:
    """Adres na domenie głównej: ``DJCMS_MAIN_PUBLIC_URL`` + ścieżka zaczynająca się od ``/``."""
    return f"{settings.DJCMS_MAIN_PUBLIC_URL.rstrip('/')}{path}"


def _served_by_app(path: str) -> bool:
    """Czy ścieżka należy do aplikacji, a nie do drzewa stron Wagtaila.

    Media i pliki statyczne sprawdzamy przedrostkiem, a nie urlconfem: w produkcji ``/media/``
    nie ma wzorca (pliki leżą w S3, a wzorzec ``static()`` istnieje tylko w ``DEBUG``), więc
    ``resolve`` oddałby catch-all Wagtaila – i obraz z wersji deweloperskiej zostałby na ``dj.``
    adresem względnym, pod którym nic nie ma.
    """
    for prefix in (settings.MEDIA_URL, settings.STATIC_URL):
        if prefix and prefix.startswith("/") and path.startswith(prefix):
            return True
    try:
        match = resolve(path)
    except Resolver404:
        return True
    return match.url_name != WAGTAIL_SERVE_URL_NAME


def api_href(url: str | None) -> str:
    """Adres z danych aplikacji w postaci, która działa na ``dj.``.

    - ścieżka strony Wagtaila (``/warsztaty/``) zostaje **względna** – po imporcie istnieje też tam,
    - każda inna ścieżka (``/results/5/``, ``/register/``, ``/documents/…``, ``/media/…``) staje się
      bezwzględna na domenie głównej,
    - adres bezwzględny ``http(s)`` (rendition w publicznym kubełku S3) zostaje bez zmian,
    - każdy inny schemat, adres protokołowo-względny (``//host``) i napis niebędący ścieżką dają
      pusty napis – czyli „bez odnośnika”, a nie odnośnik w nieznane.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return url if parts.scheme.lower() in SAFE_SCHEMES and parts.netloc else ""
    if not url.startswith("/"):
        return ""
    return main_url(url) if _served_by_app(parts.path or "/") else url


def safe_http_url(url: str | None) -> str:
    """Adres wyłącznie ``http(s)://`` – inaczej pusty napis (ta sama reguła, co w sliderze sponsorów)."""
    from apps.cms.sponsor_slider import _safe_url

    return _safe_url(url)


def datetime_fields(name: str, value: datetime | None) -> dict[str, Any]:
    """``X``, ``X_local_time`` i ``X_local_datetime`` dla jednego pola daty i godziny.

    Filtry ``local_time``/``local_datetime`` oczekują wartości **już** przeliczonej na strefę
    serwisu (``expects_localtime=True`` robi to w szablonie), więc tutaj przeliczamy jawnie.
    """
    if value is None:
        return {name: None, f"{name}_local_time": "", f"{name}_local_datetime": ""}
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return {
        name: local.isoformat(),
        f"{name}_local_time": local_time(local),
        f"{name}_local_datetime": local_datetime(local),
    }


def jsonable(value: Any) -> Any:
    """Struktura z ``date``/``datetime`` → JSON; każdy klucz ``url`` przechodzi przez ``api_href``.

    Dla słowników liczonych gdzie indziej (pasek osi czasu) – kopia, a nie przeróbka w miejscu:
    ``timeline_strip`` oddaje obiekt z pamięci podręcznej, który czytają też strony Wagtaila.
    """
    if isinstance(value, dict):
        return {
            key: api_href(item) if key == "url" and isinstance(item, str) else jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, datetime):
        return (timezone.localtime(value) if timezone.is_aware(value) else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


# --- obiekty wspólne (§ 3.2) ----------------------------------------------------------------------


def edition_dto(edition) -> dict | None:
    if edition is None:
        return None
    return {
        "id": edition.pk,
        "year_label": edition.year_label,
        "title": edition_title(edition.year_label, "edycja"),
        "title_cap": edition_title(edition.year_label, "Edycja"),
    }


def published_stage_ids(stages) -> set[int]:
    """Identyfikatory etapów z ogłoszoną tabelą – jedno zapytanie na całą odpowiedź."""
    from apps.results.models import ResultsPublication

    ids = [stage.pk for stage in stages if stage is not None]
    if not ids:
        return set()
    return set(ResultsPublication.objects.filter(stage_id__in=ids).values_list("stage_id", flat=True))


def stage_dto(stage, published: set[int] | frozenset[int] = frozenset()) -> dict | None:
    """Etap z polami, które czytają szablony ``templates/cms/*.html`` – i niczym więcej.

    ``results_url`` stoi wyłącznie przy etapie z ogłoszoną tabelą (``published``): bez publikacji
    widok ``/results/<id>/`` i tak odpowiada 404, a martwy odnośnik sugerowałby schowane wyniki.
    """
    from django.urls import reverse

    if stage is None:
        return None
    event_range = stage.event_range
    return {
        "id": stage.pk,
        "display_name": stage.display_name,
        "kind": stage.kind,
        "is_interview": stage.is_interview,
        "is_training": stage.is_training,
        "location": stage.location,
        "edition_year_label": stage.edition.year_label,
        **datetime_fields("opens_at", stage.opens_at),
        **datetime_fields("deadline_at", stage.deadline_at),
        **datetime_fields("submission_deadline", stage.submission_deadline),
        **datetime_fields("review_deadline_at", stage.review_deadline_at),
        "grace_seconds": stage.grace_seconds,
        "event_range": [event_range[0].isoformat(), event_range[1].isoformat()] if event_range else None,
        "event_dates": event_dates(stage),
        "results_url": api_href(reverse("web:results", args=[stage.pk])) if stage.pk in published else None,
    }


def stage_row_dto(row: dict, published: set[int] | frozenset[int]) -> dict:
    """Jeden wiersz ``apps.cms.timeline.stage_rows`` – stan etapu policzony zegarem serwera."""
    return {
        "stage": stage_dto(row["stage"], published),
        "is_open": row["is_open"],
        "has_opened": row["has_opened"],
        "has_results": row["has_results"],
        "status": row["status"],
        "status_label": row["status_label"],
        "badge_class": row["badge_class"],
        "is_onsite_event": row["is_onsite_event"],
        "date_range": row["date_range"],
    }


def problem_dto(problem) -> dict:
    """Zadanie. PDF zawsze przez widok aplikacji (``ProblemStatementView``), nigdy adres magazynu."""
    from django.urls import reverse

    return {
        "id": problem.pk,
        "number": problem.number,
        "title": problem.title,
        "allowed_formats": [str(item) for item in (problem.allowed_formats or [])],
        "statement_url": (
            api_href(reverse("competitions:problem-statement", args=[problem.pk]))
            if problem.statement_pdf
            else None
        ),
    }


def publication_dto(publication) -> dict:
    return {
        "id": publication.pk,
        **datetime_fields("published_at", publication.published_at),
        "anonymization": publication.anonymization,
        "anonymization_display": publication.get_anonymization_display(),
    }


def _plain(value) -> str | int | None:
    """Wartość skalarna ze snapshotu jako tekst albo liczba – nigdy słownik ani lista."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, str)):
        return value
    return str(value)


def row_dto(row: dict) -> dict:
    """Wiersz tabeli wyników – **biała lista** kluczy snapshotu (reguła 3 z § 7 speca).

    ``district`` i ``category`` przechodzą wyłącznie wtedy, gdy są w snapshotcie: okręg niesie
    tylko tabela po pseudonimach, a kategorię – tylko konkurs z flagą kategorii
    (``apps.results.services.build_snapshot``). Czegokolwiek spoza tej listy (``email``, ``name``,
    identyfikator uczestnika) ta funkcja nie zna, więc nie ma jak tego oddać.
    """
    raw_points = row.get("points")
    points = raw_points if isinstance(raw_points, dict) else {}
    item: dict[str, Any] = {
        "rank": _plain(row.get("rank")),
        "display": str(row.get("display") or ""),
        "points": {str(number): points_json(value) for number, value in points.items()},
        "points_display": {str(number): format_points(value) for number, value in points.items()},
        "total": points_json(row.get("total")),
        "total_display": format_points(row.get("total")),
        "qualified": bool(row.get("qualified")),
        "manual": bool(row.get("manual")),
    }
    if "district" in row:
        item["district"] = _plain(row["district"]) or ""
    if "category" in row:
        item["category"] = _plain(row["category"]) or ""
    return item


def table_dto(table: dict) -> dict:
    """Tabela etapu bieżącej edycji. Maksima tylko dla kolumn, które tabela faktycznie ma."""
    numbers = [str(number) for number in table["problem_numbers"]]
    maxima = table["problem_maxima"] or {}
    stage = table["stage"]
    return {
        "stage": stage_dto(stage, {stage.pk}),
        "publication": publication_dto(table["publication"]),
        "problem_numbers": numbers,
        "problem_maxima": {number: format_points(maxima[number]) for number in numbers if number in maxima},
        "rows": [row_dto(row) for row in table["rows"] if isinstance(row, dict)],
    }


def workshop_dto(row: dict) -> dict:
    """Wiersz harmonogramu warsztatów. Klucz warsztatu (``workshop_key``) celowo nie wychodzi."""
    date_value = row.get("date_value")
    return {
        "topic": row.get("topic", "") or "",
        "date": row.get("date", "") or "",
        "date_value": date_value.isoformat() if isinstance(date_value, date) else None,
        "time": row.get("time", "") or "",
        "lecturer": row.get("lecturer", "") or "",
    }


def announcement_dto(announcement) -> dict:
    """Komunikat organizatora: zwykły tekst, odnośnik wyłącznie ``http(s)``.

    ``has_link`` liczymy z adresu **po** filtrze: komunikat z ``javascript:`` w adresie ma się
    pokazać bez odnośnika, a nie z odnośnikiem prowadzącym donikąd.
    """
    link_url = safe_http_url(announcement.link_url)
    return {
        "id": announcement.pk,
        "text": announcement.text,
        "level": announcement.level,
        "link_url": link_url,
        "link_label": announcement.link_label,
        "has_link": bool(link_url and announcement.link_label),
        "dismissible": announcement.dismissible,
    }
