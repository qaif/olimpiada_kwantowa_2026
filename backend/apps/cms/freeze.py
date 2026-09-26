"""Zamrożenie edycji stron Wagtaila po przełączeniu serwisu na django CMS (DJ-02 § 1.2 D9, S13).

Od chwili przełączenia (``scripts/djcms_cutover.sh``) źródłem prawdy o treści stron publicznych
jest django CMS. Zmiana strony w ``/cms/`` Wagtaila nie trafiłaby już do czytelnika, a przy
wycofaniu (``djcms_switch.sh off``) wróciłaby jako treść, której nikt nie widział – dlatego strony
są w Wagtailu **tylko do odczytu**, egzekwowane po stronie serwera, a nie ukrytymi przyciskami.

**Przełącznik.** Wiersz ``cms.EditingFreeze`` (jeden na instalację), przełączany komendą
``manage.py cms_freeze on|off|status`` – bez restartu i bez zmiany kodu. Każdy proces czyta go
z pamięcią na :data:`CACHE_TTL_SECONDS`, więc zmiana dochodzi do wszystkich workerów ``web``
najpóźniej po tylu sekundach. Brak wiersza (albo tabeli) = edycja otwarta, czyli zachowanie
sprzed DJ-02 co do joty.

**Trzy warstwy, każda z innego powodu:**

1. ``apps.cms.middleware.CmsFreezeMiddleware`` – **granica bezpieczeństwa**: każdy widok panelu,
   który zmienia stan strony (tworzenie, zapis, publikacja, cofnięcie publikacji, przeniesienie,
   kopia, usunięcie, kolejność, prywatność, blokada, rewizje, przepływy, akcje zbiorcze,
   tłumaczenie), odpowiada 403 z komunikatem, zanim widok Wagtaila cokolwiek zrobi,
2. :class:`FrozenPagePermissionTester` – tester uprawnień Wagtaila, na którym stoi **każdy**
   przycisk panelu (lista stron, nagłówek, akcje zbiorcze, wybór celu przeniesienia) i każde
   sprawdzenie w widokach. Przyciski znikają więc same, bez wyliczania szablonów, a widok, do
   którego warstwa 1 by nie sięgnęła, i tak odmówi,
3. :class:`EditingFreezeLock` – blokada strony. Wagtail pokazuje wtedy ekran edycji jako podgląd
   tylko do odczytu (pola nieaktywne, bez przycisków zapisu, bez autozapisu) z komunikatem
   blokady, a ``EditAction`` odmawia zapisu także z kodu, który podaje użytkownika.

**Wyjątki – strony-dane** (:func:`is_exempt`). Dwie strony są dla aplikacji danymi, a nie tylko
treścią: ``ContentPage`` o slugu ``warsztaty`` (harmonogram = obecności i zaświadczenia) i
``PartnersPage`` (slider sponsorów). django CMS pokazuje je na żywo z API, więc ich **treść**
dalej edytuje się w Wagtailu: zapis, publikacja, rewizje, blokada, prywatność, przepływy.
**Drzewa** nie zmienia się także przy nich – przeniesienie, kopia, usunięcie, nowe podstrony
i zmiana sluga zostają zamrożone, bo adresem i miejscem w menu zarządza już django CMS, a strona
„Warsztaty” pod innym slugiem przestałaby być znajdowana przez aplikację. Zamrożone jest też
**cofnięcie publikacji**: aplikacja czyta wyłącznie strony opublikowane (``workshops_page()``
filtruje ``.live()``, slider sponsorów – też), więc dla niej zdjęcie strony-danych to jej
usunięcie. O wyjątku rozstrzyga strona **w bazie** (jej typ i opublikowany slug), a nie
edytowana rewizja – szkic ze starym slugiem nie zamyka strony przed jej redakcją.

**Nie zamrożone** (D9): ustawienia serwisu (``SiteSettings``), komunikaty (snippet), obrazy
i dokumenty (``/documents/`` dalej serwuje ``web``, a django CMS do nich linkuje), kolekcje,
przekierowania, konta i grupy. Komendy zarządzające (``seed_*``, ``replace_page_text``) pracują bez
użytkownika i zamrożenia nie widzą – to narzędzia operatora, nie redakcji.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from django.db import DatabaseError
from django.db.models.signals import post_delete, post_save
from django.urls import NoReverseMatch, get_script_prefix, reverse
from django.utils import timezone
from django.utils.html import format_html
from wagtail.locks import BaseLock
from wagtail.models import PagePermissionTester

from .workshops import WORKSHOPS_SLUG

#: Jak długo proces pamięta odczytany stan. Dziesięć sekund: przełączenie jest rzadkie i zapowiedziane,
#: a odczyt pada przy każdym teście uprawnień strony w panelu – bez pamięci byłoby to zapytanie
#: na każdy wiersz listy stron.
CACHE_TTL_SECONDS = 10

#: Pierwsze zdanie banera, gdy operator nie podał własnego (``cms_freeze on --message``).
DEFAULT_MESSAGE = "Edycja treści stron przeniesiona do django CMS."

#: Strony-dane rozpoznawane po typie (``Model._meta.label_lower``).
EXEMPT_PAGE_MODELS = frozenset({"cms.partnerspage"})
#: Strony-dane typu ``cms.ContentPage`` rozpoznawane po slugu.
EXEMPT_CONTENT_SLUGS = frozenset({WORKSHOPS_SLUG})

#: Nazwa adresu przekazania redaktora do django CMS (SSO, DJ-02g). Dopóki go nie ma, baner
#: prowadzi do panelu django CMS pod :data:`DJCMS_ADMIN_PATH` na tym samym hoście.
DJCMS_HANDOFF_URL_NAME = "cms_djcms_handoff"
DJCMS_ADMIN_PATH = "djcms/admin/"


@dataclass(frozen=True)
class FreezeState:
    active: bool
    message: str = ""
    changed_at: datetime | None = None
    changed_by: str = ""

    @property
    def banner_message(self) -> str:
        return self.message or DEFAULT_MESSAGE


INACTIVE = FreezeState(active=False)

#: ``(monotoniczny znacznik czasu, stan)`` albo ``None``. Pamięć procesu, a nie ``django.core.cache``:
#: Redis byłby tu wywołaniem sieciowym zamiast zapytania, a odczyt pada wiele razy na żądanie.
_memo: list[tuple[float, FreezeState]] = []

#: Zawieszenie zamrożenia w bieżącym kontekście (:func:`ignoring`).
_ignored: ContextVar[bool] = ContextVar("cms_freeze_ignored", default=False)


def freeze_state() -> FreezeState:
    """Stan zamrożenia z pamięcią na :data:`CACHE_TTL_SECONDS`. Błąd bazy = edycja otwarta."""
    if _ignored.get():
        return INACTIVE
    now = time.monotonic()
    if _memo and now - _memo[0][0] < CACHE_TTL_SECONDS:
        return _memo[0][1]

    from .models import EditingFreeze

    try:
        row = EditingFreeze.objects.filter(pk=EditingFreeze.SINGLETON_PK).first()
    except DatabaseError:  # pragma: no cover - baza bez migracji tej tabeli
        row = None
    state = (
        FreezeState(
            active=row.active,
            message=row.message,
            changed_at=row.changed_at,
            changed_by=row.changed_by,
        )
        if row is not None
        else INACTIVE
    )
    _memo[:] = [(now, state)]
    return state


def is_frozen() -> bool:
    return freeze_state().active


def reset_cache(**_kwargs) -> None:
    """Zapomina zapamiętany stan. Woła to zapis wiersza (w tym procesie) oraz testy."""
    _memo.clear()
    _exempt_memo.clear()


def set_frozen(active: bool, *, message: str = "", changed_by: str = "") -> FreezeState:
    """Zapisuje stan (tworzy wiersz przy pierwszym wywołaniu) i od razu widzi go ten proces."""
    from .models import EditingFreeze

    EditingFreeze.objects.update_or_create(
        pk=EditingFreeze.SINGLETON_PK,
        defaults={
            "active": active,
            "message": message,
            "changed_at": timezone.now(),
            "changed_by": changed_by,
        },
    )
    reset_cache()
    return freeze_state()


@contextmanager
def ignoring() -> Iterator[None]:
    """Blok, w którym zamrożenie nie obowiązuje – do liczenia uprawnień **z grup**.

    Macierz możliwości (``apps.cms.permissions.cms_abilities``) porównuje, co konto może zrobić
    przed i po zmianie grup. Zamrożenie odbiera czynności na stronach wszystkim naraz, więc liczone
    w jego trakcie zrobiłoby z tego porównania porównanie dwóch pustych zbiorów.
    """
    token = _ignored.set(True)
    try:
        yield
    finally:
        _ignored.reset(token)


def is_exempt(page) -> bool:
    """Czy strona jest stroną-danymi, której treść zostaje edytowalna mimo zamrożenia.

    Slug – z bazy (:func:`_exempt_content_page_ids`), a nie z ``page``: ekran edycji i przywracanie
    rewizji pracują na obiekcie z najnowszej rewizji (``get_latest_revision_as_object``), którego
    slug bywa inny niż opublikowany. Typ strony rewizja zmienić nie może, więc ten – z obiektu.
    """
    model = page.specific_class
    if model is None:
        return False
    label = model._meta.label_lower
    if label in EXEMPT_PAGE_MODELS:
        return True
    return label == "cms.contentpage" and page.pk is not None and page.pk in _exempt_content_page_ids()


#: ``(monotoniczny znacznik czasu, identyfikatory)`` stron-danych typu ``ContentPage`` – jak ``_memo``.
_exempt_memo: list[tuple[float, frozenset[int]]] = []


def _exempt_content_page_ids() -> frozenset[int]:
    """Strony ``ContentPage`` o slugu z :data:`EXEMPT_CONTENT_SLUGS` – w bazie, z pamięcią na TTL.

    Jedno zapytanie na :data:`CACHE_TTL_SECONDS` zamiast jednego na wiersz listy stron. Slug takiej
    strony w czasie zamrożenia się nie zmienia (``CmsFreezeMiddleware`` odrzuca zmianę sluga).
    """
    now = time.monotonic()
    if _exempt_memo and now - _exempt_memo[0][0] < CACHE_TTL_SECONDS:
        return _exempt_memo[0][1]
    from .models import ContentPage

    ids = frozenset(ContentPage.objects.filter(slug__in=EXEMPT_CONTENT_SLUGS).values_list("pk", flat=True))
    _exempt_memo[:] = [(now, ids)]
    return ids


def djcms_url() -> str:
    """Dokąd baner i ekran odmowy wysyłają redaktora: przekazanie SSO albo panel django CMS.

    Prefiks skryptu, bo ``/cms/`` bywa zamontowany pod prefiksem konkursu, a django CMS jest pod
    ``/<prefiks>/djcms/`` na tym samym hoście (DJ-02 § 1.2 D2).
    """
    try:
        return reverse(DJCMS_HANDOFF_URL_NAME)
    except NoReverseMatch:
        return f"{get_script_prefix()}{DJCMS_ADMIN_PATH}"


# --- tester uprawnień i blokada -------------------------------------------------------------------


class FrozenPagePermissionTester(PagePermissionTester):
    """Tester uprawnień Wagtaila w czasie zamrożenia – obowiązuje **także** superużytkownika.

    ``can_edit`` zostaje jak w Wagtailu: ekran edycji otwiera się jako podgląd tylko do odczytu
    (robi to :class:`EditingFreezeLock`), a sam zapis zamyka ``CmsFreezeMiddleware``. Czynności na
    drzewie są wyłączone zawsze, czynności na treści – poza stronami-danymi.
    """

    def __init__(self, user, page, *, exempt: bool):
        super().__init__(user, page)
        self.exempt = exempt

    # Drzewo: zamrożone bez wyjątków.
    def can_add_subpage(self):
        return False

    def can_delete(self, ignore_bulk=False):
        return False

    def can_move(self):
        return False

    def can_move_to(self, destination):
        return False

    def can_copy(self):
        return False

    def can_copy_to(self, destination, recursive=False):
        return False

    def can_reorder_children(self):
        return False

    def can_publish_subpage(self):
        return False

    # Treść: zamrożona poza stronami-danymi. ``can_set_view_restrictions`` i ``can_unschedule``
    # Wagtail wyprowadza z ``can_publish``, więc idą za nim same.
    def can_publish(self):
        return self.exempt and super().can_publish()

    def can_unpublish(self):
        # Także strona-dane: dla aplikacji zdjęcie z publikacji = usunięcie (docstring modułu).
        return False

    def can_lock(self):
        return self.exempt and super().can_lock()

    def can_unlock(self):
        return self.exempt and super().can_unlock()

    def can_submit_for_moderation(self):
        return self.exempt and super().can_submit_for_moderation()


class EditingFreezeLock(BaseLock):
    """Blokada „tylko do odczytu” – dla każdego konta, bez możliwości zdjęcia jej w panelu."""

    def for_user(self, user):
        return True

    def get_message(self, user):
        return format_html(
            "<b>Strona tylko do odczytu.</b> {} Wagtail pokazuje stan z chwili zamrożenia.",
            freeze_state().banner_message,
        )

    def get_locked_by(self, user):
        return "Zamrożone"

    def get_description(self, user):
        return "Edycja stron przeniesiona do django CMS – tutaj strona jest tylko do odczytu."


def frozen_permission_tester(page, user) -> FrozenPagePermissionTester | None:
    """Tester w czasie zamrożenia albo ``None`` (wtedy obowiązuje tester Wagtaila)."""
    if not is_frozen():
        return None
    return FrozenPagePermissionTester(user, page, exempt=is_exempt(page))


def freeze_lock(page) -> EditingFreezeLock | None:
    """Blokada zamrożenia dla strony, która nie jest stroną-danymi, albo ``None``."""
    if not is_frozen() or is_exempt(page):
        return None
    return EditingFreezeLock(page)


def _reset_on_freeze_change(sender, **kwargs) -> None:
    reset_cache()


# Zapis wiersza w tym procesie ma być widoczny od razu (pozostałe procesy – po TTL).
post_save.connect(_reset_on_freeze_change, sender="cms.EditingFreeze", dispatch_uid="cms.freeze.reset_save")
post_delete.connect(
    _reset_on_freeze_change, sender="cms.EditingFreeze", dispatch_uid="cms.freeze.reset_delete"
)
