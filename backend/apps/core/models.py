"""Ślad audytowy wspólny dla całego systemu (PROJEKT.md 2.2).

Zasady:
- wpis jest niemodyfikowalny (admin tylko do odczytu, brak API zapisu),
- ``diff`` zawiera wyłącznie dane techniczne i punktowe – nigdy imion, nazwisk, e-maili ani szkół,
  bo audyt jest czytany także przez osoby, które nie mają prawa do danych osobowych uczestnika,
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


def default_diff() -> dict:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return {}


class AuditLog(models.Model):
    """Kto, co i na czym zrobił. Zapisywany wyłącznie helperem :func:`audit`."""

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

    class Meta:
        verbose_name = "wpis audytu"
        verbose_name_plural = "wpisy audytu"
        ordering = ("-at", "-id")
        indexes = [models.Index(fields=["target_type", "target_id"], name="core_audit_target_idx")]

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}#{self.target_id}"


def client_ip(request) -> str | None:
    """Adres klienta z żądania. ``REMOTE_ADDR`` jest jedynym źródłem, któremu można ufać.

    ``X-Forwarded-For`` jest nagłówkiem od klienta – proxy (Caddy) ma go nadpisywać, a nie
    doklejać, więc nie czytamy go tutaj, żeby nie wpisywać do audytu adresu podanego przez atakującego.
    """
    if request is None:
        return None
    return getattr(request, "META", {}).get("REMOTE_ADDR") or None


def audit(actor, action: str, obj, diff: dict | None = None, request=None) -> AuditLog:
    """Zapisuje wpis audytowy dla obiektu ``obj``.

    ``actor`` może być ``None`` (decyzja systemowa, np. konsensus dwóch zgodnych ocen).
    """
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=f"{obj._meta.app_label}.{obj._meta.model_name}",
        target_id=str(obj.pk),
        diff=diff or {},
        ip=client_ip(request),
        at=timezone.now(),
    )
