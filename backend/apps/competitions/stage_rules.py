"""Zasady Organizacji Zawodów (ZOZ) etapu – dokument z wersjami (ZOZ-01, ``docs/tasks/ZOZ-01.md``).

Regulamin (§ 1 ust. 4) przewiduje, że **dla każdego etapu** wydaje się osobny dokument „Zasady
Organizacji Zawodów”: harmonogram, formę zadań, dozwolone narzędzia, program. Dokument jest więc
własnością etapu, a nie strony redakcyjnej – i dlatego mieszka tutaj, przy ``Stage``, a nie
w drzewie stron Wagtaila (``apps.cms.models.DocumentPage``):

- **zarządza nim koordynator w swoim panelu**, z audytem, tak jak terminami etapu. Strony Wagtaila
  są od przełączenia na django CMS zamrożone (``apps.cms.freeze``), a djcms nie zna etapów,
- **izolacja konkursów** idzie ścieżką ``stage__edition__competition`` – ta sama, co przy zadaniach;
  etap cudzego konkursu daje 404 w każdym widoku,
- **wersje są wierszami**: poprzednie opublikowane wersje zostają dostępne pod własnym adresem
  (``/dokumenty/zasady/<etap>/wersja/<wersja>/``), a „obowiązuje” zawsze ostatnio opublikowana.

Z istniejącego mechanizmu dokumentów bierzemy to, co da się wziąć bez równoległej konstrukcji:
układ strony (``doc-layout``: metryka pod tytułem, „Pobierz PDF” i „Drukuj” nad treścią, spis
rozdziałów z kotwic), prywatny storage plików (``private_media``, plik wydaje wyłącznie widok
aplikacji) i miejsce w sekcji ``/dokumenty/`` (manifest tras djcms – ``dokumenty/zasady`` jest
adresem aplikacji pod stroną redakcyjną, jak ``warsztaty/materialy``).

**Treść jest HTML-em od organizatora**, oczyszczanym przy zapisie (:func:`sanitize_html`): tylko
znaczniki struktury dokumentu (nagłówki, akapity, listy, tabele, odnośniki), bez atrybutów stylu
i zdarzeń, bez skryptów. Oczyszczanie przy **zapisie**, a nie przy wyświetleniu: w bazie leży to,
co zobaczy czytelnik, a koordynator widzi skutek w podglądzie od razu.

**Stany:** projekt → opublikowany → (wycofany). Opublikowanej wersji nie edytuje się – zmiana
dokumentu prawnego to nowa wersja z nowym numerem (ZOZ § 14 ust. 2: zmiany się ogłasza). Wycofanie
zdejmuje wersję z serwisu w całości (także z historii) – na wypadek publikacji pomyłkowej.

Danych osobowych model nie niesie: kto i kiedy zmienił dokument, mówi dziennik audytu.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from html import escape
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError

from .scoping import competition_scoped_manager
from .storage import private_media_storage

#: Tytuł domyślny – nazwa dokumentu z Regulaminu (§ 1 ust. 4). Nazwę etapu dokłada widok.
DEFAULT_TITLE = "Zasady Organizacji Zawodów (ZOZ)"

#: Numer wersji: „1.0”, „1.1”, „2.0-a”. Bez ukośnika i odstępu, bo stoi w adresie.
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]{0,19}$")

#: Górny limit pliku PDF – ten sam, co przy treści zadania (``web.forms.MAX_STATEMENT_MB``).
MAX_PDF_MB = 20

#: Górny limit treści HTML. Sześciostronicowy ZOZ Etapu I to ok. 30 kB; limit chroni bazę
#: i stronę przed omyłkowo wklejonym plikiem z osadzonymi obrazami.
MAX_HTML_CHARS = 400_000


class StageRulesStatus(models.TextChoices):
    DRAFT = "DRAFT", "projekt"
    PUBLISHED = "PUBLISHED", "opublikowany"
    WITHDRAWN = "WITHDRAWN", "wycofany"


def rules_upload_to(instance, filename: str) -> str:
    """``stage-rules/<uuid4 hex>/<nazwa>`` – klucz, którego nie da się odgadnąć z nazwy pliku."""
    return f"stage-rules/{uuid.uuid4().hex}/{filename}"


class StageRules(models.Model):
    """Jedna wersja dokumentu ZOZ jednego etapu."""

    stage = models.ForeignKey(
        "competitions.Stage", on_delete=models.CASCADE, related_name="rules_versions", verbose_name="etap"
    )
    version = models.CharField("wersja", max_length=20)
    document_date = models.DateField("data dokumentu")
    title = models.CharField("tytuł", max_length=200, default=DEFAULT_TITLE)
    status = models.CharField(
        "status", max_length=16, choices=StageRulesStatus.choices, default=StageRulesStatus.DRAFT
    )
    body_html = models.TextField("treść (HTML)")
    # Prywatny storage: plik wydaje widok aplikacji, który sprawdza status wersji. Projekt
    # wgrany z PDF-em nie może mieć publicznego adresu przed publikacją.
    pdf = models.FileField(
        "plik PDF",
        upload_to=rules_upload_to,
        storage=private_media_storage,
        blank=True,
        max_length=255,
    )
    published_at = models.DateTimeField("opublikowano", null=True, blank=True)
    created_at = models.DateTimeField("utworzono", default=timezone.now)
    updated_at = models.DateTimeField("zmieniono", auto_now=True)

    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "Zasady Organizacji Zawodów (wersja)"
        verbose_name_plural = "Zasady Organizacji Zawodów"
        ordering = ("stage", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["stage", "version"], name="competitions_stagerules_unique_version"
            ),
            # Opublikowana wersja bez chwili publikacji nie dałaby się uporządkować w historii,
            # a „obowiązuje ostatnio opublikowana” byłoby pytaniem bez odpowiedzi.
            models.CheckConstraint(
                condition=~Q(status=StageRulesStatus.PUBLISHED) | Q(published_at__isnull=False),
                name="competitions_stagerules_published_has_date",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} – {self.stage.display_name}, wersja {self.version}"

    @property
    def is_published(self) -> bool:
        return self.status == StageRulesStatus.PUBLISHED

    def clean(self) -> None:
        super().clean()
        if self.version and not VERSION_RE.match(self.version):
            raise ValidationError(
                {"version": "Wersja: cyfry, litery, kropka lub myślnik (np. „1.0”), do 20 znaków."}
            )


# --- oczyszczanie HTML-a ---------------------------------------------------------------------

#: Znaczniki struktury dokumentu. Wszystko inne znika (treść zostaje – poza ``DROP_WITH_CONTENT``).
ALLOWED_TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "caption",
        "dd",
        "div",
        "dl",
        "dt",
        "em",
        "h2",
        "h3",
        "h4",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "section",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
VOID_TAGS = frozenset({"br", "hr"})
#: Znaczniki usuwane **razem z treścią** – kod i metadane, nie tekst dokumentu.
DROP_WITH_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "embed", "template", "svg", "math", "noscript", "head", "title"}
)
#: Atrybuty dozwolone na każdym znaczniku i dodatkowe dla wybranych.
GLOBAL_ATTRS = frozenset({"id", "class", "lang", "title"})
TAG_ATTRS = {
    "a": frozenset({"href"}),
    "ol": frozenset({"type", "start"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan", "scope"}),
}
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,80}$")
_CLASS_RE = re.compile(r"^[A-Za-z0-9_ -]{1,120}$")
_NUMBER_RE = re.compile(r"^[0-9]{1,3}$")
_OL_TYPES = frozenset({"1", "a", "A", "i", "I"})
_SCOPES = frozenset({"row", "col", "rowgroup", "colgroup"})
#: Adresy odnośników: ``http(s)``, ``mailto`` i względne od korzenia albo kotwice. Bez
#: ``javascript:``, ``data:`` i adresów względnych do bieżącej strony (``../``).
_HREF_RE = re.compile(r"^(?:https?://|mailto:|/(?!/)|#)", re.IGNORECASE)


def _clean_attr(tag: str, name: str, value: str | None) -> str | None:
    """Oczyszczona wartość atrybutu albo ``None`` (atrybut znika)."""
    if value is None:
        return None
    value = value.strip()
    if name == "href":
        return value if _HREF_RE.match(value) and not any(ch.isspace() for ch in value) else None
    if name == "id":
        return value if _ID_RE.match(value) else None
    if name == "class":
        return value if _CLASS_RE.match(value) else None
    if name in {"start", "colspan", "rowspan"}:
        return value if _NUMBER_RE.match(value) else None
    if name == "type":
        return value if value in _OL_TYPES else None
    if name == "scope":
        return value if value in _SCOPES else None
    return value[:200]


class _Sanitizer(HTMLParser):
    """Przepisuje HTML znacznik po znaczniku, zostawiając wyłącznie listę dozwolonych.

    Wynik powstaje **od nowa** z danych parsera (nazwy znaczników, atrybuty, tekst z ucieczką), a nie
    przez wycinanie fragmentów źródła – więc nie ma czego „przemycić” w nietypowym zapisie.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0
        self.chapters: list[tuple[str, str]] = []
        self._chapter: list | None = None

    def handle_starttag(self, tag, attrs):
        if tag in DROP_WITH_CONTENT:
            self.skip_depth += 1
            return
        if self.skip_depth or tag not in ALLOWED_TAGS:
            return
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, frozenset())
        parts = []
        for name, value in attrs:
            if name not in allowed:
                continue
            cleaned = _clean_attr(tag, name, value)
            if cleaned is not None:
                parts.append(f' {name}="{escape(cleaned, quote=True)}"')
        if tag == "a" and any(part.startswith(' href="http') for part in parts):
            # Odnośnik zewnętrzny bez przekazania ``Referer`` z adresem dokumentu.
            parts.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(parts)}>")
        if tag in VOID_TAGS:
            return
        self.stack.append(tag)
        if tag == "h2":
            anchor = dict(attrs).get("id")
            anchor = _clean_attr(tag, "id", anchor)
            self._chapter = [anchor, []] if anchor else None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and not self.skip_depth and tag in ALLOWED_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self.out.append(f"</{open_tag}>")
            if open_tag == "h2" and self._chapter is not None:
                anchor, text = self._chapter
                self.chapters.append((anchor, " ".join("".join(text).split())))
                self._chapter = None
            if open_tag == tag:
                break

    def handle_data(self, data):
        if self.skip_depth:
            return
        self.out.append(escape(data, quote=False))
        if self._chapter is not None:
            self._chapter[1].append(data)

    def close(self):
        super().close()
        while self.stack:
            self.handle_endtag(self.stack[-1])
        return "".join(self.out).strip()


def sanitize_html(source: str) -> str:
    """HTML gotowy do wyświetlenia: dozwolone znaczniki i atrybuty, reszta wycięta."""
    parser = _Sanitizer()
    parser.feed(source or "")
    return parser.close()


def chapters(body_html: str) -> list[dict]:
    """Spis rozdziałów: nagłówki ``<h2 id=…>`` w kolejności – jak ``DocumentPage.chapters``."""
    parser = _Sanitizer()
    parser.feed(body_html or "")
    parser.close()
    return [{"anchor": anchor, "text": text} for anchor, text in parser.chapters]


def body_digest(body_html: str) -> str:
    """Krótki skrót treści do audytu – sama treść jest w wierszu, w dzienniku tylko ślad zmiany."""
    return hashlib.sha256((body_html or "").encode("utf-8")).hexdigest()[:16]


# --- odczyt ---------------------------------------------------------------------------------


def published(queryset=None):
    """Wersje opublikowane, najnowsza publikacja pierwsza."""
    queryset = StageRules.objects.all() if queryset is None else queryset
    return queryset.filter(status=StageRulesStatus.PUBLISHED).order_by("-published_at", "-pk")


def current_rules(stage) -> StageRules | None:
    """Wersja obowiązująca etapu: ostatnio opublikowana. ``None`` – etap nie ma ZOZ."""
    if stage is None or stage.pk is None:
        return None
    return published(StageRules.objects.filter(stage_id=stage.pk)).first()


def current_rules_by_stage(stage_ids) -> dict[int, StageRules]:
    """Wersja obowiązująca dla wielu etapów naraz – **jedno** zapytanie (oś czasu, panel)."""
    stage_ids = [pk for pk in stage_ids if pk is not None]
    if not stage_ids:
        return {}
    result: dict[int, StageRules] = {}
    for rules in published(StageRules.objects.filter(stage_id__in=stage_ids)).only(
        "pk", "stage_id", "version", "document_date", "title", "published_at", "status"
    ):
        result.setdefault(rules.stage_id, rules)
    return result


def public_url(stage_id: int) -> str:
    from django.urls import reverse

    return reverse("web:stage-rules", args=[stage_id])


# --- menu „Dokumenty” (ZOZ-02) ---------------------------------------------------------------

#: Wpis pamięci podręcznej „etapy z opublikowanym ZOZ” – per konkurs, unieważniany sygnałami niżej.
MENU_CACHE_PREFIX = "stage_rules:menu"
#: Godzina, jak przy plakatach (``apps.promo.availability``): unieważnianie jest jawne, TTL zostaje
#: wyłącznie zabezpieczeniem dla zapisów z pominięciem sygnałów (``QuerySet.update``).
MENU_CACHE_TTL_SECONDS = 60 * 60


def menu_cache_key(competition_id) -> str:
    return f"{MENU_CACHE_PREFIX}:{competition_id or 'none'}"


def _menu_stages_from_db(competition_id: int) -> list[dict]:
    """Jedno zapytanie: etapy bieżącej edycji **tego** konkursu, które mają opublikowany ZOZ.

    Projekt i wersja wycofana nie liczą się (``Exists`` po statusie opublikowanym), trening nie ma
    ZOZ (jak w spisie ``/dokumenty/`` i w panelu koordynatora). Kolejność = kolejność etapów
    (``opens_at``, potem ``id``) – ta sama, co w spisie dokumentów i na osi czasu.
    """
    from django.db.models import Exists, OuterRef

    from .models import Stage, StageKind

    has_published = StageRules.objects.filter(stage_id=OuterRef("pk"), status=StageRulesStatus.PUBLISHED)
    return list(
        Stage.objects.filter(edition__competition_id=competition_id, edition__is_current=True)
        .exclude(kind=StageKind.TRAINING)
        .filter(Exists(has_published))
        .order_by("opens_at", "id")
        .values("pk", "name", "kind")
    )


def menu_stages(competition) -> list[dict]:
    """Etapy do pozycji „Zasady Organizacji Zawodów – <etap>” w menu „Dokumenty” (ZOZ-02).

    Menu stoi na **każdej** stronie serwisu, więc wynik leży w pamięci podręcznej per konkurs
    (wzorzec ``apps.promo.availability``): na ciepło zero zapytań, na zimno jedno. W pamięci są
    wyłącznie surowe pola (``pk``, ``name``, ``kind``) – napis pozycji składa menu w języku żądania.
    Konkurs ``None`` (host bez konkursu) nie ma ZOZ.
    """
    from django.core.cache import cache

    if competition is None or getattr(competition, "pk", None) is None:
        return []
    key = menu_cache_key(competition.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached
    value = _menu_stages_from_db(competition.pk)
    cache.set(key, value, MENU_CACHE_TTL_SECONDS)
    return value


def forget_menu(competition_id) -> None:
    """Kasuje wpis menu konkursu **teraz i po zatwierdzeniu transakcji**.

    Drugie skasowanie z tego samego powodu, co w ``apps.web.page_cache.invalidate_on_commit``: gość,
    który trafi między sygnał a ``COMMIT``, przeczytałby jeszcze stary stan i odłożył go w pamięci
    na godzinę.
    """
    from django.core.cache import cache

    key = menu_cache_key(competition_id)
    cache.delete(key)
    transaction.on_commit(lambda: cache.delete(key))


def _reset_menu_on_rules_change(sender, instance, **kwargs) -> None:
    from django.core.exceptions import ObjectDoesNotExist

    try:
        forget_menu(instance.stage.edition.competition_id)
    except ObjectDoesNotExist:  # pragma: no cover - kaskada skasowała już etap; jego sygnał zrobi to samo
        pass


def _reset_menu_on_stage_change(sender, instance, **kwargs) -> None:
    from django.core.exceptions import ObjectDoesNotExist

    # Nazwa, rodzaj i kolejność etapu stoją w pozycji menu; skasowanie etapu zabiera jego ZOZ.
    try:
        forget_menu(instance.edition.competition_id)
    except ObjectDoesNotExist:  # pragma: no cover - kaskada skasowała już edycję; jej sygnał zrobi to samo
        pass


def _reset_menu_on_edition_change(sender, instance, **kwargs) -> None:
    # Zmiana edycji bieżącej (``is_current``) zmienia zestaw etapów w menu.
    forget_menu(instance.competition_id)


def _connect_menu_signals() -> None:
    """Sygnały kasujące wpis menu. ``sender`` jako napis – model ``Stage``/``Edition`` jest w module,
    który importuje ten moduł, więc odniesienie wprost byłoby importem cyklicznym."""
    from django.db.models.signals import post_delete, post_save

    post_save.connect(_reset_menu_on_rules_change, sender=StageRules, dispatch_uid="stage_rules.menu.save")
    post_delete.connect(
        _reset_menu_on_rules_change, sender=StageRules, dispatch_uid="stage_rules.menu.delete"
    )
    for signal, uid in ((post_save, "save"), (post_delete, "delete")):
        signal.connect(
            _reset_menu_on_stage_change,
            sender="competitions.Stage",
            dispatch_uid=f"stage_rules.menu.stage_{uid}",
        )
        signal.connect(
            _reset_menu_on_edition_change,
            sender="competitions.Edition",
            dispatch_uid=f"stage_rules.menu.edition_{uid}",
        )


_connect_menu_signals()


# --- czynności koordynatora -------------------------------------------------------------------


def _validation_error(exc: ValidationError) -> DomainError:
    messages = []
    if hasattr(exc, "message_dict"):
        for field_messages in exc.message_dict.values():
            messages.extend(field_messages)
    else:  # pragma: no cover - ``full_clean`` zawsze daje słownik
        messages.extend(exc.messages)
    return DomainError(" ".join(messages), "STAGE_RULES_INVALID", status.HTTP_400_BAD_REQUEST)


def _duplicate(version: str) -> DomainError:
    return DomainError(
        f"Wersja {version} tego dokumentu już istnieje – nadaj nowej wersji inny numer.",
        "STAGE_RULES_DUPLICATE_VERSION",
        status.HTTP_409_CONFLICT,
    )


def _check_html(body_html: str) -> str:
    cleaned = sanitize_html(body_html)
    if not cleaned:
        raise DomainError("Treść dokumentu jest pusta.", "STAGE_RULES_EMPTY", status.HTTP_400_BAD_REQUEST)
    if len(cleaned) > MAX_HTML_CHARS:
        raise DomainError(
            "Treść dokumentu jest za długa.", "STAGE_RULES_TOO_LONG", status.HTTP_400_BAD_REQUEST
        )
    return cleaned


def check_pdf(upload) -> None:
    """PDF rozpoznany po zawartości (``%PDF-``), nie po nazwie – jak treść zadania."""
    if upload is None:
        return
    if upload.size > MAX_PDF_MB * 1024 * 1024:
        raise DomainError(
            f"Plik PDF jest za duży (limit {MAX_PDF_MB} MB).",
            "STAGE_RULES_PDF_TOO_LARGE",
            status.HTTP_400_BAD_REQUEST,
        )
    position = upload.tell() if hasattr(upload, "tell") else None
    upload.seek(0)
    head = upload.read(5)
    upload.seek(position or 0)
    if head != b"%PDF-":
        raise DomainError(
            "Plik nie jest dokumentem PDF.", "STAGE_RULES_PDF_INVALID", status.HTTP_400_BAD_REQUEST
        )


def _audit(actor, action: str, rules: StageRules, request=None, **extra) -> None:
    from apps.core.models import audit

    audit(
        actor,
        action,
        rules,
        {
            "stage": rules.stage_id,
            "version": rules.version,
            "status": rules.status,
            "body_sha256": body_digest(rules.body_html),
            "has_pdf": bool(rules.pdf),
            **extra,
        },
        request=request,
    )


def create_rules(
    stage,
    actor,
    *,
    version: str,
    document_date,
    body_html: str,
    title: str = DEFAULT_TITLE,
    pdf=None,
    request=None,
) -> StageRules:
    """Nowa wersja ZOZ etapu – zawsze jako projekt. Publikacja jest osobną czynnością."""
    check_pdf(pdf)
    rules = StageRules(
        stage=stage,
        version=(version or "").strip(),
        document_date=document_date,
        title=(title or DEFAULT_TITLE).strip(),
        body_html=_check_html(body_html),
    )
    if pdf is not None:
        rules.pdf = pdf
    try:
        rules.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    try:
        with transaction.atomic():
            rules.save()
    except IntegrityError as exc:
        raise _duplicate(rules.version) from exc
    _audit(actor, "stage_rules.created", rules, request)
    return rules


@transaction.atomic
def update_rules(
    rules: StageRules,
    actor,
    *,
    document_date=None,
    body_html: str | None = None,
    title: str | None = None,
    pdf=None,
    remove_pdf: bool = False,
    allow_published: bool = False,
    request=None,
) -> tuple[StageRules, bool]:
    """Zmiana wersji. Zwraca ``(wersja, czy_coś_się_zmieniło)``.

    Opublikowanej wersji nie zmienia się z panelu (``allow_published=False``): dokument prawny
    zmienia się nową wersją. Wyjątek ma wyłącznie komenda importu z jawną flagą operatora.
    """
    locked = StageRules.objects.select_for_update().get(pk=rules.pk)
    if locked.status == StageRulesStatus.PUBLISHED and not allow_published:
        raise DomainError(
            "Opublikowanej wersji nie zmienia się – dodaj nową wersję dokumentu.",
            "STAGE_RULES_PUBLISHED_LOCKED",
            status.HTTP_409_CONFLICT,
        )
    check_pdf(pdf)
    changed: list[str] = []
    if document_date is not None and document_date != locked.document_date:
        locked.document_date = document_date
        changed.append("document_date")
    if title is not None and title.strip() and title.strip() != locked.title:
        locked.title = title.strip()
        changed.append("title")
    if body_html is not None:
        cleaned = _check_html(body_html)
        if cleaned != locked.body_html:
            locked.body_html = cleaned
            changed.append("body_html")
    if pdf is not None:
        locked.pdf = pdf
        changed.append("pdf")
    elif remove_pdf and locked.pdf:
        locked.pdf = ""
        changed.append("pdf")
    if not changed:
        return locked, False
    try:
        locked.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    locked.save()
    _audit(actor, "stage_rules.updated", locked, request, fields=changed)
    return locked, True


@transaction.atomic
def publish_rules(rules: StageRules, actor, *, request=None, now=None) -> StageRules:
    """Publikacja wersji: od tej chwili jest obowiązująca (ostatnio opublikowana)."""
    locked = StageRules.objects.select_for_update().get(pk=rules.pk)
    if locked.status == StageRulesStatus.PUBLISHED:
        return locked
    locked.status = StageRulesStatus.PUBLISHED
    locked.published_at = now or timezone.now()
    locked.save(update_fields=["status", "published_at", "updated_at"])
    _audit(actor, "stage_rules.published", locked, request)
    return locked


@transaction.atomic
def withdraw_rules(rules: StageRules, actor, *, request=None) -> StageRules:
    """Wycofanie wersji: znika z serwisu (także z historii). Obowiązuje poprzednia opublikowana."""
    locked = StageRules.objects.select_for_update().get(pk=rules.pk)
    if locked.status != StageRulesStatus.PUBLISHED:
        raise DomainError(
            "Wycofać można wyłącznie wersję opublikowaną.",
            "STAGE_RULES_NOT_PUBLISHED",
            status.HTTP_409_CONFLICT,
        )
    locked.status = StageRulesStatus.WITHDRAWN
    locked.save(update_fields=["status", "updated_at"])
    _audit(actor, "stage_rules.withdrawn", locked, request)
    return locked


@transaction.atomic
def delete_rules(rules: StageRules, actor, *, request=None) -> None:
    """Usunięcie projektu. Wersji, która była opublikowana, nie usuwa się – zostaje w audycie i bazie."""
    locked = StageRules.objects.select_for_update().get(pk=rules.pk)
    if locked.status != StageRulesStatus.DRAFT:
        raise DomainError(
            "Usunąć można wyłącznie projekt. Opublikowaną wersję można wycofać.",
            "STAGE_RULES_NOT_DRAFT",
            status.HTTP_409_CONFLICT,
        )
    _audit(actor, "stage_rules.deleted", locked, request)
    locked.delete()
