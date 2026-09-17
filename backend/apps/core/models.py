"""Ślad audytowy wspólny dla całego systemu (PROJEKT.md 2.2).

Zasady:
- wpis jest niemodyfikowalny (admin tylko do odczytu, brak API zapisu),
- ``diff`` zawiera wyłącznie dane techniczne i punktowe – nigdy imion, nazwisk, e-maili ani szkół,
  bo audyt jest czytany także przez osoby, które nie mają prawa do danych osobowych uczestnika,
- czas zawsze przez ``django.utils.timezone.now()``.
"""

import ipaddress
import logging
from functools import lru_cache

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.tenancy.managers import CompetitionScopedQuerySet

logger = logging.getLogger(__name__)

#: Nagłówek, w którym proxy (Caddy) podaje adres klienta. Czytany wyłącznie od zaufanego nadawcy.
REAL_IP_HEADER = "HTTP_X_REAL_IP"


def default_diff() -> dict:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return {}


class AuditLogQuerySet(CompetitionScopedQuerySet):
    """Dwa różne pytania o widoczność wpisu – i dwie różne odpowiedzi.

    ``for_competition(c)`` jest **ścisłe** i dziedziczone bez zmian: oddaje wpisy tego konkursu
    i niczego więcej. Tak wygląda eksport śladu jednego organizatora albo zliczenie jego zdarzeń –
    tam wpis platformowy byłby cudzym wierszem w cudzym raporcie.

    ``visible_to(c)`` jest tym, czego potrzebuje **przeglądarka audytu** koordynatora: wpisy jego
    konkursu **oraz** wpisy bez konkursu. Te drugie opisują obiekty platformowe – konto,
    witrynę, zgłoszenie do operatora, alert infrastruktury – których nie da się przypisać do
    żadnego organizatora, a które koordynator Olimpiady Kwantowej czyta od zawsze. Schowanie ich
    byłoby zmianą widoczną i niezamówioną (``docs/UNIWERSALNY-ETAP-1.md`` § 0).

    Dlaczego to są dwie metody, a nie jedna rozluźniona: gdyby ``for_competition`` samo dopuszczało
    ``NULL``, każdy przyszły odczyt audytu – raport, eksport, licznik – dziedziczyłby po cichu
    regułę ekranu przeglądarki. Rozdzielenie sprawia, że dopuszczenie wpisów platformowych jest
    **decyzją wołającego**, widoczną w nazwie metody.
    """

    def visible_to(self, competition):
        """Wpisy tego konkursu plus wpisy platformowe (bez konkursu).

        ``None`` nie widzi niczego – tak samo jak ``for_competition``. „Nie wiadomo, o który
        konkurs chodzi” nie może znaczyć „wszystkie”, a wpisy platformowe są dodatkiem do
        czyjegoś śladu, a nie osobnym uprawnieniem.
        """
        if competition is None:
            return self.none()
        return self.filter(models.Q(competition=competition) | models.Q(competition__isnull=True))


class AuditLog(models.Model):
    """Kto, co i na czym zrobił. Zapisywany wyłącznie helperem :func:`audit`."""

    # Konkurs, którego dotyczy zdarzenie. ``SET_NULL``, bo ślad audytowy nie może zniknąć razem
    # z konkursem (§ 3.2) – wpis o skasowanym konkursie jest dokładnie tym, po co audyt istnieje.
    #
    # ``null=True`` na stałe: wpisy o obiektach **platformowych** (``accounts.user``,
    # ``wagtailcore.site``, alert infrastruktury, zgłoszenie do operatora) nie mają konkursu i to
    # jest odpowiedź poprawna, a nie brak danych (§ 3.9).
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="audit_entries",
        verbose_name="konkurs",
        help_text="Puste = zdarzenie dotyczy obiektu platformy, a nie konkursu.",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name="wykonawca",
    )
    action = models.CharField("akcja", max_length=64)
    target_type = models.CharField("typ obiektu", max_length=64)
    target_id = models.CharField("id obiektu", max_length=64)
    diff = models.JSONField("zmiana", default=default_diff, blank=True)
    ip = models.GenericIPAddressField("adres IP", null=True, blank=True)
    at = models.DateTimeField("kiedy", default=timezone.now, db_index=True)

    objects = models.Manager.from_queryset(AuditLogQuerySet)()

    class Meta:
        verbose_name = "wpis audytu"
        verbose_name_plural = "wpisy audytu"
        ordering = ("-at", "-id")
        indexes = [models.Index(fields=["target_type", "target_id"], name="core_audit_target_idx")]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}#{self.target_id}"


@lru_cache(maxsize=8)
def _parse_networks(entries: tuple[str, ...]) -> tuple:
    """Parsuje listę adresów/CIDR z ustawień na obiekty ``ip_network``.

    Wynik jest memoizowany po *wartości* ustawienia, a nie na stałe – dzięki temu testy podmieniające
    ``settings.TRUSTED_PROXY_IPS`` widzą nową listę, a produkcja parsuje ją raz.
    """
    networks = []
    for entry in entries:
        text = (entry or "").strip()
        if not text:
            continue
        try:
            networks.append(ipaddress.ip_network(text, strict=False))
        except ValueError:
            # Zła konfiguracja nie może wywrócić żądania – wpis jest pomijany, ale zostaje w logu.
            logger.warning("TRUSTED_PROXY_IPS: pomijam nieprawidłowy wpis %r", text)
    return tuple(networks)


def _is_trusted_proxy(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    entries = tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()) or ())
    return any(address in network for network in _parse_networks(entries))


def _parse_address(value: str | None):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def client_ip(request) -> str | None:
    """Adres klienta z żądania.

    ``REMOTE_ADDR`` jest domyślnym i jedynym bezwarunkowo wiarygodnym źródłem. ``X-Real-IP``
    (ustawiany przez Caddy na ``{remote_host}``) jest brany pod uwagę **wyłącznie**, gdy samo
    połączenie przyszło z adresu wymienionego w ``settings.TRUSTED_PROXY_IPS`` – w innym wypadku
    byłby to adres podany przez klienta, więc atakujący wpisywałby sobie dowolne IP do audytu.
    Nagłówek o nieprawidłowej treści jest ignorowany (audyt woli adres prawdziwy niż podstawiony).
    """
    if request is None:
        return None
    meta = getattr(request, "META", None) or {}
    remote = _parse_address(meta.get("REMOTE_ADDR"))
    if remote is None:
        return None
    if not _is_trusted_proxy(remote):
        return str(remote)
    forwarded = _parse_address(meta.get(REAL_IP_HEADER))
    return str(forwarded) if forwarded is not None else str(remote)


def audit_competition(request=None):
    """Konkurs, którego dotyczy zapisywane właśnie zdarzenie (§ 3.9).

    Kolejność: ``request.competition`` (ustawia go ``CompetitionMiddleware``) → kontekst
    (``competition_context`` w zadaniu Celery, komendzie, migracji) → ``None``.

    ``None`` jest **odpowiedzią**, a nie brakiem odpowiedzi: zdarzenia o obiektach platformowych
    – założenie konta, zmiana witryny, alert infrastruktury – nie należą do żadnego konkursu.
    Zgadywanie „pierwszy z brzegu” dopisywałoby je w tabeli przypadkowego organizatora.

    Konkurs bierzemy z **kontekstu żądania**, a nie z obiektu, którego wpis dotyczy. Obiekt bywa
    w tym miejscu już skasowany (``webhook.deleted``, ``account.erased``), a jego droga do
    konkursu wiedzie czasem przez pięć kluczy obcych – audyt nie może kosztować pięciu złączeń
    przy każdym zdarzeniu ani wywracać się na obiekcie bez ``pk``.
    """
    from apps.tenancy.context import current_competition

    competition = getattr(request, "competition", None) if request is not None else None
    return competition if competition is not None else current_competition()


def audit(actor, action: str, obj, diff: dict | None = None, request=None) -> AuditLog:
    """Zapisuje wpis audytowy dla obiektu ``obj``.

    ``actor`` może być ``None`` (decyzja systemowa, np. konsensus dwóch zgodnych ocen).
    Konkurs rozstrzyga :func:`audit_competition` – patrz jego docstring.
    """
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        competition=audit_competition(request),
        actor=actor,
        action=action,
        target_type=f"{obj._meta.app_label}.{obj._meta.model_name}",
        target_id=str(obj.pk),
        diff=diff or {},
        ip=client_ip(request),
        at=timezone.now(),
    )
