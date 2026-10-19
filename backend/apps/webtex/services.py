"""Reguły edytora LaTeX po stronie platformy: flaga, bramki uczestnika, token, złożenie, udostępnianie.

Bramki sprawdzamy **zawsze tutaj**, przy otwarciu edytora i przy złożeniu – webTEX nie decyduje
o niczym, co dotyczy zawodów (TEX-01 § „Cel”). Termin, format, rozmiar, skan, wersje i nadzór przy
złożeniu egzekwuje i tak ``create_submission``; tu stoją bramki, które muszą zadziałać **przed**
wystawieniem tokenu albo przed dwuminutową kompilacją.
"""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from rest_framework import status as http

from apps.core.api import DomainError

from . import client, keys
from .models import StageLatexSettings

#: Flaga konkursu z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS``.
FEATURE = "latex_editor"
#: Nazwa pliku składanego rozwiązania (TEX-01 § „Zachowanie platformy”).
PDF_NAME = "rozwiazanie.pdf"


def is_enabled(competition) -> bool:
    """Flaga konkursu – odczyt pola wiersza, który wołający już trzyma (zero zapytań)."""
    return competition is not None and competition.has_feature(FEATURE)


def available(competition) -> bool:
    """Flaga **i** skonfigurowana integracja. Bez klucza funkcja jest wyłączona jak bez flagi."""
    return is_enabled(competition) and keys.configured()


# --- klucze projektu i zakresu (kontrakt) ---------------------------------------------------------


def scope_key(competition, stage) -> str:
    return f"{competition.slug}/s{stage.pk}"


def project_key(competition, stage, problem) -> str:
    return f"{scope_key(competition, stage)}/p{problem.number}"


# --- przełączniki etapu: udostępnianie linków (D5) i asystent AI (D8) -----------------------------

#: Pole ``StageLatexSettings`` → (akcja audytu, nazwa zadania Celery wysyłającego stan do webTEX).
OPTIONS = {
    "sharing_enabled": ("webtex.sharing_changed", "push_stage_sharing"),
    "ai_enabled": ("webtex.ai_changed", "push_stage_ai"),
}


def stage_options(stage) -> dict[str, bool]:
    """Oba przełączniki etapu jednym zapytaniem; brak wiersza = oba wyłączone (stan domyślny)."""
    row = StageLatexSettings.objects.filter(stage=stage).values(*OPTIONS).first()
    return {name: bool(row and row[name]) for name in OPTIONS}


def sharing_enabled(stage) -> bool:
    return stage_options(stage)["sharing_enabled"]


def ai_enabled(stage) -> bool:
    return stage_options(stage)["ai_enabled"]


def set_option(stage, name: str, enabled: bool, *, actor, request=None) -> bool:
    """Zapisuje przełącznik etapu; przy zmianie – wpis audytu i zadanie Celery do webTEX.

    Zwraca, czy stan się zmienił. Zadanie idzie po commicie i czyta stan **z bazy** (nie z argumentu),
    więc dwa szybkie przełączenia kończą się w webTEX stanem z bazy, niezależnie od kolejności zadań.
    """
    from apps.core.models import audit

    action, task_name = OPTIONS[name]
    enabled = bool(enabled)
    with transaction.atomic():
        row, _created = StageLatexSettings.objects.select_for_update().get_or_create(stage=stage)
        previous = getattr(row, name)
        if previous == enabled:
            return False
        setattr(row, name, enabled)
        row.updated_by = actor if getattr(actor, "is_authenticated", False) else None
        row.save(update_fields=[name, "updated_by", "updated_at"])
        audit(actor, action, row, {"stage": stage.pk, name: [previous, enabled]}, request=request)

        def _push(stage_id: int = stage.pk) -> None:
            from . import tasks

            getattr(tasks, task_name).delay(stage_id)

        transaction.on_commit(_push)
    return True


def set_sharing(stage, enabled: bool, *, actor, request=None) -> bool:
    return set_option(stage, "sharing_enabled", enabled, actor=actor, request=request)


def set_ai(stage, enabled: bool, *, actor, request=None) -> bool:
    return set_option(stage, "ai_enabled", enabled, actor=actor, request=request)


# --- bramki uczestnika ----------------------------------------------------------------------------


def _not_found() -> DomainError:
    return DomainError(_("Nie ma takiego zadania."), "LATEX_NOT_FOUND", http.HTTP_404_NOT_FOUND)


def staff_refusal() -> DomainError:
    return DomainError(
        _(
            "Twoje konto ma rolę personelu, więc edytor LaTeX jako uczestnik jest dla niego wyłączony. "
            "Do rozwiązywania zadań używaj osobnego konta uczestnika."
        ),
        "LATEX_STAFF_ACCOUNT",
        http.HTTP_403_FORBIDDEN,
    )


def check_access(user, participant, stage, problem, competition, *, now=None):
    """Bramki otwarcia i złożenia: flaga, rola kadry, wpis na etap, forma etapu, treść zadań jawna.

    Zwraca wpis na etap. Odmowa to ``DomainError``: 404, gdy dla tej osoby zadania „nie ma”
    (bez flagi, bez wpisu, przed startem jej okna), 403 – konto kadry albo wpis zdyskwalifikowany.
    Nadzór zdalny sprawdza warstwa adresów (``apps.proctoring.middleware.GATED_VIEWS``), a przy
    złożeniu – drugi raz – ``create_submission``.
    """
    from apps.competitions.models import StageEntry, StageEntryStatus
    from apps.notebooks.services import has_staff_role
    from apps.time_windows.access import enabled as time_windows_enabled
    from apps.time_windows.access import statements_visible

    if not available(competition) or participant is None:
        raise _not_found()
    # D7: konto z jakąkolwiek rolą personelu nie wchodzi do edytora jako uczestnik (jak QC-02).
    if has_staff_role(user):
        raise staff_refusal()
    entry = StageEntry.objects.filter(participant=participant, stage=stage).first()
    if entry is None or stage.is_interview or problem.stage_id != stage.pk:
        raise _not_found()
    if entry.status == StageEntryStatus.DISQUALIFIED:
        raise DomainError(
            _("Wpis do etapu jest zdyskwalifikowany."), "ENTRY_DISQUALIFIED", http.HTTP_403_FORBIDDEN
        )
    now = now or timezone.now()
    if time_windows_enabled(competition):
        visible = statements_visible(stage, participant=participant, competition=competition, now=now)
    else:
        visible = stage.has_opened(now)
    if not visible:
        raise _not_found()
    return entry


# --- token SSO ------------------------------------------------------------------------------------


def _lang() -> str:
    return "pl" if (translation.get_language() or "pl").lower().startswith("pl") else "en"


def tasks_tab_url(request, problem) -> str:
    """Absolutny adres karty zadania na zakładce „Zadania” panelu uczestnika (link „Wróć do olimpiady”)."""
    from apps.web.views.participant import TAB_TASKS

    return request.build_absolute_uri(f"{reverse('web:me')}?tab={TAB_TASKS}#problem-{problem.pk}")


def open_url(request, participant, stage, problem, competition) -> str:
    """Adres ``{WEBTEX_URL}/sso#token=…`` z jednorazowym tokenem – po sprawdzeniu bramek przez wołającego."""
    user = request.user
    options = stage_options(stage)
    claims = keys.claims_for(
        sub=str(user.pk),
        name=user.get_full_name() or participant.public_code,
        lang=_lang(),
        project=project_key(competition, stage, problem),
        scope=scope_key(competition, stage),
        share=options["sharing_enabled"],
        ai=options["ai_enabled"],
        title=_("Zadanie %(number)s. %(title)s") % {"number": problem.number, "title": problem.display_title},
        code=participant.public_code,
        return_url=tasks_tab_url(request, problem),
    )
    return keys.sso_url(keys.issue(claims))


# --- złożenie -------------------------------------------------------------------------------------


def precheck_submission(stage, problem, *, now=None) -> None:
    """Odmowy, które znamy przed kompilacją – nie ma po co czekać dwóch minut na PDF, którego serwis
    i tak nie przyjmie. ``stage`` to etap w oknie ucznia (``personal_stage``). Ostateczne słowo ma
    ``create_submission``."""
    now = now or timezone.now()
    if "pdf" not in (problem.allowed_formats or []):
        raise DomainError(
            _("To zadanie nie przyjmuje rozwiązań w PDF – edytor LaTeX nie złoży go za Ciebie."),
            "LATEX_PDF_NOT_ALLOWED",
            http.HTTP_409_CONFLICT,
        )
    if not stage.is_open_for_submissions(now) or stage.closed_at is not None:
        raise DomainError(_("Termin oddania rozwiązań minął."), "DEADLINE_PASSED", http.HTTP_403_FORBIDDEN)


def submit_from_editor(request, stage, problem, competition):
    """Pobiera świeży PDF z webTEX i składa go zwykłą ścieżką uploadu.

    Odmowa edytora to ``client.WebtexError``, odmowa serwisu (termin, format, nadzór) – ``DomainError``.
    """
    from apps.submissions.services import create_submission

    pdf = client.build_pdf(
        str(request.user.pk),
        project_key(competition, stage, problem),
        max_bytes=int(problem.max_file_mb) * 1024 * 1024,
    )
    return create_submission(
        request.user, stage, problem.number, ContentFile(pdf, name=PDF_NAME), request=request
    )
