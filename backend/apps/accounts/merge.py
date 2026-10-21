"""Scalanie zdublowanych kont uczestnika jednego konkursu (ACC-MERGE-01).

Skąd ten moduł. Ekran „Zdublowane konta” (``apps.accounts.duplicates``) umiał wyłącznie usunąć kopię.
To wystarcza, gdy kopia jest pusta, i zawodzi, gdy **oba** konta tej samej osoby mają aktywność:
zgody i zaświadczenie na pierwszym, praca z etapu na drugim. Usunięcie któregokolwiek zabiera
część historii (albo anonimizuje kopię z pracą i zostawia osobie dwa kody publiczne).

Scalenie przenosi aktywność konta **źródłowego** (``drop``, „kopia”) na konto **docelowe**
(``keep``), a potem usuwa kopię tą samą drogą co dotąd – ``delete_account_by_coordinator``. Druga
ścieżka kasowania rozjechałaby się z pierwszą przy pierwszej zmianie reguły „ślad → anonimizacja”.

Trzy zasady, których pilnuje ten moduł, a nie ekran:

- **każda relacja ma regułę** (``RULES`` + ``rule_for``). Test ``test_every_relation_is_classified``
  przegląda wszystkie modele instalacji i zatrzymuje CI na nowym kluczu do ``Participant``/``User``
  bez reguły. W chwili scalenia relacja bez reguły, na której kopia ma wiersze, jest odmową – lepiej
  nie scalić niż zgubić dane po cichu,
- **konflikt blokuje** zamiast zgadywać: dwie aktywności w tym samym etapie, dwie rozliczane
  należności z jednego cennika, dwa profile absolwenta – rozstrzyga koordynator, nie serwis,
- **plan i wykonanie to ten sam kod** (``_Run`` z ``apply=False``/``True``). Podgląd pokazuje dokładnie
  to, co zrobi przycisk, a wykonanie liczy plan od nowa na zablokowanych wierszach.

Dziennik zdarzeń (``core.AuditLog``) nie jest przepinany: to dowód tego, co się działo, a nie stan.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.apps import apps
from django.db import models, transaction
from django.db.models import Q
from rest_framework import status

from apps.core.api import DomainError
from apps.core.models import audit

from .models import COORDINATOR_GROUPS, CompetitionRole, Participant, User

__all__ = [
    "RULES",
    "MergePlan",
    "MergeResult",
    "merge_participants",
    "plan_merge",
    "relations",
    "rule_for",
]

# --- reguły --------------------------------------------------------------------------------------

MOVE = "move"
MOVE_UNIQUE = "move_unique"
NEWER = "newer"
TARGET = "target"
CONFLICT = "conflict"
CUSTOM = "custom"
ROLE = "role"
STAYS = "stays"
AUDIT_LOG = "audit"
PROFILE = "profile"

RULE_LABELS = {
    MOVE: "przepięte",
    MOVE_UNIQUE: "przepięte bez duplikatów",
    NEWER: "zostaje nowszy",
    TARGET: "zostaje docelowy",
    CONFLICT: "konflikt blokuje",
    CUSTOM: "reguła własna",
    ROLE: "inna rola – odmowa",
    STAYS: "znika z kopią",
    AUDIT_LOG: "nie ruszamy",
    PROFILE: "profil",
}


@dataclass(frozen=True)
class Rule:
    """Reguła jednej relacji. ``key`` – pola, po których dwa wiersze to „ten sam fakt”."""

    kind: str
    key: tuple[str, ...] = ()
    stamp: str = ""
    #: Treść konfliktu (``CONFLICT``) albo nazwa roli (``ROLE``) – dla człowieka.
    note: str = ""


#: Reguły jawne, klucz ``app_label.Model.pole``. Tabela z opisem: ``docs/tasks/ACC-MERGE-01.md`` § 1.
RULES: dict[str, Rule] = {
    # --- profil uczestnika ---
    "competitions.StageEntry.participant": Rule(CUSTOM),
    "competitions.TeamMember.participant": Rule(CUSTOM),
    "tenancy.ParticipantFee.participant": Rule(CUSTOM),
    "student_status.StudentStatusCertificate.participant": Rule(CUSTOM),
    "chat.Conversation.participant": Rule(CUSTOM),
    "chat.Conversation.participant_low": Rule(CUSTOM),
    "chat.Conversation.participant_high": Rule(CUSTOM),
    "chat.ChatBlock.blocker": Rule(CUSTOM),
    "chat.ChatBlock.blocked": Rule(CUSTOM),
    "chat.ChatKey.participant": Rule(CUSTOM),
    "alumni.Mentorship.mentor": Rule(CUSTOM),
    "alumni.Mentorship.mentee": Rule(CUSTOM),
    "proctoring.ProctoringSession.participant": Rule(
        CONFLICT, key=("stage",), note="Oba konta mają sesję nadzoru (proctoring) w tym samym etapie."
    ),
    "delegation_logistics.DelegationMember.participant": Rule(
        CONFLICT,
        key=("delegation",),
        note="Oba konta są członkami tej samej delegacji (dane podróży, odprawa) – zostaw jeden wpis.",
    ),
    "alumni.AlumniProfile.participant": Rule(
        CONFLICT, note="Oba konta mają profil absolwenta – usuń jeden z nich w panelu absolwentów."
    ),
    "accounts.ConsentRecord.participant": Rule(MOVE),
    "appeals.Appeal.filed_by": Rule(MOVE),
    "alumni.AlumniConsentEvent.participant": Rule(MOVE),
    "chat.Conversation.started_by": Rule(MOVE),
    "cms.WorkshopAttendance.participant": Rule(MOVE_UNIQUE, key=("workshop_key",)),
    "chat.ConversationMember.participant": Rule(MOVE_UNIQUE, key=("conversation",)),
    "chat.ChatProfile.participant": Rule(TARGET),
    "time_windows.ParticipantWindow.participant": Rule(NEWER, key=("plan",), stamp="set_at"),
    "time_windows.ParticipantTimezone.participant": Rule(NEWER, stamp="set_at"),
    "problem_translations.StudentLanguage.participant": Rule(NEWER, stamp="set_at"),
    "payments.BillingProfile.participant": Rule(NEWER, stamp="updated_at"),
    # --- konto ---
    "accounts.Participant.user": Rule(PROFILE),
    "accounts.Membership.user": Rule(CUSTOM),
    "accounts.CommitteeMember.user": Rule(ROLE, note="profil komitetu"),
    "accounts.SchoolSupervisor.user": Rule(ROLE, note="opiekun szkolny"),
    "accounts.DelegationLeader.user": Rule(ROLE, note="opiekun drużyny"),
    "delegation_logistics.LogisticsAccess.user": Rule(ROLE, note="dostęp do logistyki delegacji"),
    "delegation_logistics.DelegationMember.user": Rule(ROLE, note="opiekun w składzie delegacji"),
    "proctoring.ProctorAssignment.user": Rule(ROLE, note="osoba nadzorująca etap"),
    "translation_review.TranslatorGrant.user": Rule(ROLE, note="tłumacz"),
    "webinars.Webinar.co_moderators": Rule(ROLE, note="współprowadzący webinaru"),
    "accounts.UserPreference.user": Rule(NEWER, stamp="updated_at"),
    "forum.ForumNotificationSettings.user": Rule(NEWER, stamp="updated_at"),
    "chat.ChatNotificationSettings.user": Rule(NEWER, stamp="updated_at"),
    "webinars.WebinarNotificationSettings.user": Rule(NEWER, stamp="updated_at"),
    "forum.ForumSubscription.user": Rule(MOVE_UNIQUE, key=("thread",)),
    "chat.MessageReport.reporter": Rule(MOVE_UNIQUE, key=("message",)),
    "translation_review.TranslationVote.user": Rule(MOVE_UNIQUE, key=("suggestion",)),
    "forum.ForumDecisionNotice.user": Rule(MOVE),
    "support.SupportTicket.user": Rule(MOVE),
    "translation_review.TranslationReport.reporter": Rule(MOVE),
    # Poświadczenia kopii: przeniesienie logowania zewnętrznego albo 2FA dałoby dostęp do konta
    # docelowego drogą, której nikt nie potwierdzał. Znikają z kopią (``_drop_credentials``).
    "authtoken.Token.user": Rule(STAYS),
    "accounts.TwoFactorDevice.user": Rule(STAYS),
    "staff_mfa.TwoFactorGrace.user": Rule(STAYS),
    "staff_mfa.PreviousEmail.user": Rule(STAYS),
    "staff_mfa.TrustRevocation.user": Rule(STAYS),
    "account.EmailAddress.user": Rule(STAYS),
    # Powiadomienia w panelu (UX-PANEL-01) są drogowskazem do aktywności, a nie aktywnością: rozmowy,
    # decyzje i wyniki przechodzą na konto docelowe własnymi regułami, a przepięte powiadomienia kopii
    # zderzałyby się z więzem „jedno nieprzeczytane na klucz” i prowadziły do zdarzeń już widocznych.
    "notifications.Notification.user": Rule(STAYS),
    "socialaccount.SocialAccount.user": Rule(STAYS),
    "core.AuditLog.actor": Rule(AUDIT_LOG),
}

#: Aplikacje personelu (panel Django, Wagtail): konto czysto uczestnickie nie ma tam wierszy, a gdyby
#: miało – zostają z kopią tak samo jak przy zwykłym usunięciu (``SET_NULL``/kaskada).
STAFF_APPS = frozenset(
    {"admin", "wagtailcore", "wagtailadmin", "wagtailusers", "wagtaildocs", "wagtailimages"}
)
#: Pola stron i dokumentów CMS (właściciel strony, blokada edycji, kto wgrał) – ślad redakcji.
CMS_STAFF_FIELDS = frozenset({"owner", "locked_by", "uploaded_by_user"})


def relations() -> list[tuple[type[models.Model], models.Field]]:
    """Wszystkie relacje (FK/O2O/M2M) do ``Participant`` i ``User`` w instalacji.

    Bez modeli proxy (``TokenProxy`` to ta sama tabela co ``Token``) i bez relacji odwrotnych.
    """
    found = []
    for model in apps.get_models():
        if model._meta.proxy:
            continue
        for fld in model._meta.get_fields():
            if not getattr(fld, "is_relation", False) or fld.auto_created:
                continue
            if fld.related_model in (Participant, User):
                found.append((model, fld))
    return found


def rule_for(model: type[models.Model], fld: models.Field) -> Rule | None:
    """Reguła relacji: jawna z ``RULES``, inaczej domyślna; ``None`` – relacja bez reguły."""
    label = f"{model._meta.label}.{fld.name}"
    if label in RULES:
        return RULES[label]
    app_label = model._meta.app_label
    if app_label in STAFF_APPS or (app_label == "cms" and fld.name in CMS_STAFF_FIELDS):
        return Rule(STAYS)
    # Kolumny „kto to zrobił” w aplikacjach projektu (autor, zgłaszający, zatwierdzający): to ta sama
    # osoba, więc podpis przechodzi na konto docelowe. Kaskadowe relacje muszą być w ``RULES`` jawnie –
    # tam „przepnij” nie jest oczywiste (wiersz kopii mógłby zderzyć się z wierszem docelowego).
    on_delete = getattr(getattr(fld, "remote_field", None), "on_delete", None)
    if (
        fld.related_model is User
        and model.__module__.startswith("apps.")
        and on_delete in (models.SET_NULL, models.PROTECT)
    ):
        return Rule(MOVE)
    return None


# --- plan ----------------------------------------------------------------------------------------


@dataclass
class PlanLine:
    """Wiersz podglądu: co, ile i z jakim skutkiem."""

    key: str
    label: str
    count: int
    effect: str
    detail: str = ""


@dataclass
class MergePlan:
    keep: Participant
    drop: Participant
    moved: list[PlanLine] = field(default_factory=list)
    discarded: list[PlanLine] = field(default_factory=list)
    #: Pola profilu docelowego uzupełnione z kopii (nazwy, bez wartości – idą też do audytu).
    profile_fields: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.refusals or self.conflicts)

    @property
    def profile_labels(self) -> list[str]:
        """Uzupełnione pola profilu nazwami z modelu – na ekran (audyt dostaje nazwy techniczne)."""
        return [str(Participant._meta.get_field(name).verbose_name) for name in self.profile_fields]

    @property
    def can_take_email(self) -> bool:
        """Adres kopii wolno wziąć wyłącznie, gdy docelowe swojego **nie potwierdziło**."""
        keep, drop = self.keep.user, self.drop.user
        return keep.email_verified_at is None and keep.email.lower() != drop.email.lower()

    def counts(self, lines: list[PlanLine]) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for line in lines:
            totals[line.key] += line.count
        return dict(totals)


@dataclass
class MergeResult:
    plan: MergePlan
    #: Skutek usunięcia kopii: ``"deleted"`` (zwykle) albo ``"anonymised"``.
    outcome: str
    email_taken: bool


class _Run:
    """Jeden przebieg reguł: ``apply=False`` liczy plan, ``apply=True`` dodatkowo zapisuje."""

    def __init__(self, keep: Participant, drop: Participant, *, apply: bool, actor=None):
        self.keep, self.drop, self.apply, self.actor = keep, drop, apply, actor
        self.plan = MergePlan(keep=keep, drop=drop)

    def conflict(self, text: str) -> None:
        # Wykonanie liczy plan na nowo **przed** zapisami i przerywa się na pierwszym konflikcie, więc
        # tutaj w trybie zapisu konflikt jest niemożliwy – a gdyby jednak padł, transakcja się cofa.
        if self.apply:
            raise DomainError(text, "MERGE_CONFLICT", status.HTTP_409_CONFLICT)
        if text not in self.plan.conflicts:
            self.plan.conflicts.append(text)

    def refuse(self, text: str) -> None:
        if self.apply:
            raise DomainError(text, "MERGE_REFUSED", status.HTTP_400_BAD_REQUEST)
        if text not in self.plan.refusals:
            self.plan.refusals.append(text)

    def moved(
        self, key: str, label: str, count: int, effect: str = RULE_LABELS[MOVE], detail: str = ""
    ) -> None:
        if count:
            self.plan.moved.append(PlanLine(key, label, count, effect, detail))

    def discarded(self, key: str, label: str, count: int, effect: str, detail: str = "") -> None:
        if count:
            self.plan.discarded.append(PlanLine(key, label, count, effect, detail))


def _qs(model) -> models.QuerySet:
    """Menedżer bazowy – bez filtrów menedżerów konkursowych (``CompetitionScopedManager``)."""
    return model._base_manager.all()


def _model(label: str):
    return apps.get_model(label)


def _label(model, fld) -> str:
    """Nazwa relacji dla człowieka: liczba mnoga modelu, a przy kilku kluczach do konta – i pole."""
    name = str(model._meta.verbose_name_plural)
    siblings = [
        other
        for other in model._meta.get_fields()
        if getattr(other, "is_relation", False)
        and not other.auto_created
        and other.related_model is fld.related_model
    ]
    return f"{name} ({fld.verbose_name})" if len(siblings) > 1 else name


def _code(participant: Participant) -> str:
    return participant.public_code


# --- kto może być scalony ------------------------------------------------------------------------


def _assert_pair(keep: Participant, drop: Participant) -> None:
    """Twarde warunki pary – bez nich nie ma czego planować."""
    from .anonymised import is_anonymised

    if keep.pk == drop.pk or keep.user_id == drop.user_id:
        raise DomainError("Nie można scalić konta z nim samym.", "MERGE_SAME", status.HTTP_400_BAD_REQUEST)
    if keep.competition_id != drop.competition_id:
        raise DomainError(
            "Scalać można wyłącznie konta uczestników tego samego konkursu.",
            "MERGE_OTHER_COMPETITION",
            status.HTTP_400_BAD_REQUEST,
        )
    if is_anonymised(keep.user) or is_anonymised(drop.user):
        raise DomainError(
            "Jedno z kont zostało już usunięte (zanonimizowane).",
            "MERGE_ANONYMISED",
            status.HTTP_400_BAD_REQUEST,
        )


def _check_accounts(run: _Run) -> None:
    """Odmowy z powodu **kont** (nie danych): chronione, z innymi rolami, własne konto wykonawcy."""
    from .shared_accounts import active_elsewhere

    competition = run.keep.competition
    for participant, side in ((run.keep, "docelowe"), (run.drop, "kopia")):
        user = participant.user
        code = _code(participant)
        if run.actor is not None and user.pk == run.actor.pk:
            run.refuse(f"Konto {code} ({side}) to Twoje własne konto – tego ekranu nie używa się na sobie.")
        if user.is_superuser or user.is_staff or user.groups.filter(name__in=COORDINATOR_GROUPS).exists():
            run.refuse(
                f"Konto {code} ({side}) jest kontem koordynatora albo personelu – scalanie niemożliwe."
            )
        if active_elsewhere(user, competition):
            run.refuse(
                f"Konto {code} ({side}) ma rolę albo profil w innym konkursie – scala wyłącznie "
                "administrator serwisu."
            )
        Membership = _model("accounts.Membership")
        roles = (
            _qs(Membership)
            .filter(user=user, competition=competition)
            .exclude(role=CompetitionRole.PARTICIPANT)
            .values_list("role", flat=True)
        )
        for role in roles:
            run.refuse(f"Konto {code} ({side}) ma w tym konkursie inną rolę: {CompetitionRole(role).label}.")


# --- reguły własne -------------------------------------------------------------------------------


def _stage_name(stage) -> str:
    from apps.competitions.models import StageKind

    return stage.name or StageKind(stage.kind).label


def _entry_activity(entry, participant) -> list[str]:
    """Co wpis do etapu niesie poza samym zapisem. Pusta lista – wpis „pusty” (można go odrzucić).

    Dzieci wpisu bierzemy z relacji odwrotnych ``StageEntry`` (prace, test online, rozmowa, formularz
    przyjazdu, obecność, dyplom, ręczna nagroda …), a nie z listy w kodzie: nowy model przypięty do
    wpisu automatycznie robi z niego wpis „z aktywnością”, czyli konflikt zamiast cichej straty.
    """
    from apps.competitions.models import StageEntry, StageEntryStatus

    found = []
    for rel in StageEntry._meta.related_objects:
        related = rel.related_model
        rows = _qs(related).filter(**{rel.field.name: entry})
        if related._meta.label == "submissions.Submission":
            works = rows.values("problem_id").distinct().count()
            if works:
                found.append(f"prace: {works}")
        elif rows.exists():
            found.append(str(related._meta.verbose_name_plural))
    if (
        _qs(_model("proctoring.ProctoringSession"))
        .filter(stage_id=entry.stage_id, participant=participant)
        .exists()
    ):
        found.append("sesja nadzoru")
    if entry.status != StageEntryStatus.REGISTERED:
        found.append(f"status: {entry.get_status_display()}")
    if entry.manual_qualification:
        found.append("decyzja komitetu o kwalifikacji")
    if entry.total_points is not None:
        found.append("punkty")
    return found


def _entries(run: _Run) -> None:
    """Wpisy do etapów (razem z pracami i wszystkim, co wisi na wpisie)."""
    from apps.competitions.models import StageEntry
    from apps.submissions.models import Submission

    drop_entries = list(_qs(StageEntry).filter(participant=run.drop).select_related("stage").order_by("pk"))
    if not drop_entries:
        return
    keep_by_stage = {
        entry.stage_id: entry
        for entry in _qs(StageEntry).filter(
            participant=run.keep, stage_id__in=[entry.stage_id for entry in drop_entries]
        )
    }
    move: list[int] = []
    discard: list[int] = []
    replace: list[tuple[int, int]] = []
    names: list[str] = []
    for entry in drop_entries:
        other = keep_by_stage.get(entry.stage_id)
        name = _stage_name(entry.stage)
        if other is None:
            move.append(entry.pk)
            names.append(name)
            continue
        mine, theirs = _entry_activity(entry, run.drop), _entry_activity(other, run.keep)
        if mine and theirs:
            run.conflict(
                f"Oba konta mają aktywność w etapie „{name}” (kopia: {', '.join(mine)}; docelowe: "
                f"{', '.join(theirs)}). Zostaw oba konta albo usuń kopię."
            )
        elif mine:
            replace.append((other.pk, entry.pk))
            names.append(name)
        else:
            discard.append(entry.pk)
    carried = move + [entry_pk for _other, entry_pk in replace]
    works = _qs(Submission).filter(entry_id__in=carried).values("entry_id", "problem_id").distinct().count()
    run.moved("competitions.StageEntry.participant", "wpisy do etapów", len(carried), detail=", ".join(names))
    run.moved("submissions.Submission", "prace (razem z wpisem)", works, effect="z wpisem")
    run.discarded(
        "competitions.StageEntry.participant",
        "puste wpisy kopii w etapach, w których docelowe już jest",
        len(discard),
        "odpadają",
    )
    run.discarded(
        "competitions.StageEntry.participant:keep",
        "puste wpisy docelowego zastąpione wpisem kopii z aktywnością",
        len(replace),
        "odpadają",
    )
    if run.apply:
        _qs(StageEntry).filter(pk__in=discard + [other_pk for other_pk, _ in replace]).delete()
        _qs(StageEntry).filter(pk__in=carried).update(participant=run.keep)


def _teams(run: _Run) -> None:
    TeamMember = _model("competitions.TeamMember")
    rows = list(_qs(TeamMember).filter(participant=run.drop).select_related("team"))
    keep_editions = set(
        _qs(TeamMember).filter(participant=run.keep).values_list("team__edition_id", flat=True)
    )
    ok = []
    for member in rows:
        if member.team.edition_id in keep_editions:
            run.conflict(
                f"Oba konta są w drużynach tej samej edycji (drużyna kopii: „{member.team.name}”) – "
                "najpierw usuń kopię z drużyny."
            )
        else:
            ok.append(member.pk)
    run.moved("competitions.TeamMember.participant", "członkostwa w drużynach", len(ok))
    if run.apply and ok:
        _qs(TeamMember).filter(pk__in=ok).update(participant=run.keep)


def _fee_touched(fee) -> bool:
    """Należność „z ruchem”: nie jest już „do zapłaty” albo ma zamówienie lub zdarzenie płatności."""
    from apps.tenancy.fees import FeeStatus

    return (
        fee.status != FeeStatus.DUE
        or _qs(_model("payments.Order")).filter(participant_fee=fee).exists()
        or _qs(_model("integrations.PaymentEvent")).filter(fee=fee).exists()
    )


def _fees(run: _Run) -> None:
    """Należności. Rejestr rozliczeń nie może zgubić wpłaty – dwie rozliczane należności to konflikt."""
    from apps.competitions.models import StageEntry

    Fee = _model("tenancy.ParticipantFee")
    drop_fees = list(_qs(Fee).filter(participant=run.drop).select_related("schedule"))
    if not drop_fees:
        return
    keep_by_schedule = {
        fee.schedule_id: fee
        for fee in _qs(Fee).filter(
            participant=run.keep, schedule_id__in=[fee.schedule_id for fee in drop_fees]
        )
    }
    move, discard, replace = [], [], []
    for fee in drop_fees:
        other = keep_by_schedule.get(fee.schedule_id)
        if other is None:
            move.append(fee)
            continue
        mine, theirs = _fee_touched(fee), _fee_touched(other)
        if mine and theirs:
            run.conflict(
                f"Oba konta mają rozliczaną należność z tego samego cennika („{fee.schedule}”) – "
                "rozlicz ją (zwrot, umorzenie) przed scaleniem."
            )
        elif mine:
            replace.append((other, fee))
        else:
            discard.append((fee, other))
    run.moved("tenancy.ParticipantFee.participant", "należności (wpisowe)", len(move) + len(replace))
    run.discarded(
        "tenancy.ParticipantFee.participant",
        "nierozliczone należności-duplikaty z tego samego cennika",
        len(discard) + len(replace),
        "odpadają (zostaje jedna)",
    )
    if not run.apply:
        return
    for fee, other in discard:
        _qs(StageEntry).filter(fee=fee).update(fee=other)
        fee.delete()
    for other, fee in replace:
        _qs(StageEntry).filter(fee=other).update(fee=fee)
        other.delete()
    _qs(Fee).filter(pk__in=[fee.pk for fee in move] + [fee.pk for _o, fee in replace]).update(
        participant=run.keep
    )


_CERT_RANK = {"ACCEPTED": 3, "PENDING": 2, "REJECTED": 1, "SUPERSEDED": 0}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _cert_rank(cert) -> tuple:
    return (_CERT_RANK.get(cert.status, 0), cert.uploaded_at or _EPOCH, cert.pk)


def _certificates(run: _Run) -> None:
    """Zaświadczenia o statusie ucznia: wersje kopii dopisane po wersjach docelowego, bieżące – lepsze."""
    from apps.student_status.models import CertificateStatus

    Cert = _model("student_status.StudentStatusCertificate")
    by_edition: dict[int, list] = defaultdict(list)
    for cert in (
        _qs(Cert).filter(participant=run.drop).select_related("edition").order_by("edition_id", "version")
    ):
        by_edition[cert.edition_id].append(cert)
    total = 0
    for rows in by_edition.values():
        total += len(rows)
        keep_rows = list(_qs(Cert).filter(participant=run.keep, edition_id=rows[0].edition_id))
        if not keep_rows:
            if run.apply:
                _qs(Cert).filter(pk__in=[cert.pk for cert in rows]).update(participant=run.keep)
            continue
        offset = max(cert.version for cert in keep_rows)
        keep_current = next((cert for cert in keep_rows if cert.is_current), None)
        drop_current = next((cert for cert in rows if cert.is_current), None)
        loser = None
        if keep_current is not None and drop_current is not None:
            winner = max((keep_current, drop_current), key=_cert_rank)
            loser = drop_current if winner is keep_current else keep_current
            run.plan.notes.append(
                f"Zaświadczenie ({rows[0].edition}): bieżącą wersją zostaje plik "
                f"{'konta docelowego' if winner is keep_current else 'kopii'} "
                f"({winner.get_status_display()})."
            )
        if run.apply:
            if loser is not None:
                loser.is_current = False
                fields = ["is_current"]
                if loser.status == CertificateStatus.PENDING:
                    # Ta sama wartość, co przy zwykłym wgraniu nowszego pliku: historia z „oczekuje”
                    # pod wierszem bieżącym mówiłaby, że czekają dwa pliki.
                    loser.status = CertificateStatus.SUPERSEDED
                    fields.append("status")
                loser.save(update_fields=fields)
            for cert in rows:
                _qs(Cert).filter(pk=cert.pk).update(participant=run.keep, version=cert.version + offset)
    run.moved(
        "student_status.StudentStatusCertificate.participant",
        "zaświadczenia o statusie ucznia (wersje)",
        total,
    )


def _chat(run: _Run) -> None:
    """Czat: rozmowa z organizatorem (scalana), rozmowy prywatne (przepinane), klucz, blokady."""
    Conversation = _model("chat.Conversation")
    Message = _model("chat.Message")
    ChatKey = _model("chat.ChatKey")
    ChatBlock = _model("chat.ChatBlock")
    Mentorship = _model("alumni.Mentorship")
    keep, drop = run.keep, run.drop

    # Rozmowa z organizatorem – jedna na uczestnika. U obu: wiadomości kopii idą do rozmowy docelowego.
    moved_conversations = merged_messages = 0
    for conversation in _qs(Conversation).filter(kind="ORGANIZER", participant=drop):
        target = (
            _qs(Conversation)
            .filter(kind="ORGANIZER", participant=keep, competition_id=conversation.competition_id)
            .first()
        )
        if target is None:
            moved_conversations += 1
            if run.apply:
                _qs(Conversation).filter(pk=conversation.pk).update(participant=keep)
            continue
        merged_messages += _qs(Message).filter(conversation=conversation).count()
        if run.apply:
            _qs(Message).filter(conversation=conversation).update(conversation=target)
            _qs(Mentorship).filter(conversation=conversation).update(conversation=target)
            stamps = [s for s in (target.last_message_at, conversation.last_message_at) if s is not None]
            unread = [s for s in (target.organizer_unread_since, conversation.organizer_unread_since) if s]
            target.last_message_at = max(stamps) if stamps else None
            target.organizer_unread_since = min(unread) if unread else None
            target.save(update_fields=["last_message_at", "organizer_unread_since"])
            conversation.delete()

    # Rozmowy prywatne – para (low < high) po przepięciu musi pozostać parą i pozostać jedyną.
    for conversation in (
        _qs(Conversation).filter(kind="PEER").filter(Q(participant_low=drop) | Q(participant_high=drop))
    ):
        drop_is_low = conversation.participant_low_id == drop.pk
        other_id = conversation.participant_high_id if drop_is_low else conversation.participant_low_id
        if other_id == keep.pk:
            run.conflict("Kopia i konto docelowe mają rozmowę prywatną ze sobą nawzajem.")
            continue
        if other_id is not None:
            low, high = sorted((keep.pk, other_id))
            if (
                _qs(Conversation)
                .filter(kind="PEER", participant_low_id=low, participant_high_id=high)
                .exists()
            ):
                run.conflict("Oba konta mają rozmowę prywatną z tym samym uczestnikiem.")
                continue
            update = {"participant_low_id": low, "participant_high_id": high}
        else:
            update = {"participant_low_id" if drop_is_low else "participant_high_id": keep.pk}
        moved_conversations += 1
        if run.apply:
            _qs(Conversation).filter(pk=conversation.pk).update(**update)
    run.moved("chat.Conversation", "rozmowy", moved_conversations)
    run.moved(
        "chat.Message.conversation",
        "wiadomości z rozmowy kopii z organizatorem",
        merged_messages,
        effect="dołączone do rozmowy docelowego",
    )

    # Klucz szyfrowania: szyfrogramy kopii są związane z jej kluczem – dwa klucze to nieczytelna historia.
    drop_key = _qs(ChatKey).filter(participant=drop).first()
    if drop_key is not None:
        if not _qs(ChatKey).filter(participant=keep).exists():
            run.moved("chat.ChatKey.participant", "klucz szyfrowania wiadomości", 1)
            if run.apply:
                _qs(ChatKey).filter(pk=drop_key.pk).update(participant=keep)
        elif (
            _qs(Conversation)
            .filter(kind="PEER", is_encrypted=True)
            .filter(Q(participant_low=drop) | Q(participant_high=drop))
            .exists()
        ):
            run.conflict(
                "Oba konta mają klucz szyfrowania wiadomości, a kopia ma rozmowy szyfrowane – po scaleniu "
                "nie dałoby się ich odczytać."
            )
        else:
            run.discarded("chat.ChatKey.participant", "klucz szyfrowania kopii", 1, RULE_LABELS[TARGET])

    # Blokady: przepięte bez duplikatów; blokada „sam siebie” (kopia ↔ docelowe) odpada.
    moved_blocks = dropped_blocks = 0
    for side, other_side in (("blocker", "blocked"), ("blocked", "blocker")):
        existing = set(_qs(ChatBlock).filter(**{side: keep}).values_list(f"{other_side}_id", flat=True))
        for pk, other_id in _qs(ChatBlock).filter(**{side: drop}).values_list("pk", f"{other_side}_id"):
            if other_id in (keep.pk, drop.pk) or other_id in existing:
                dropped_blocks += 1
                if run.apply:
                    _qs(ChatBlock).filter(pk=pk).delete()
                continue
            existing.add(other_id)
            moved_blocks += 1
            if run.apply:
                _qs(ChatBlock).filter(pk=pk).update(**{side: keep})
    run.moved("chat.ChatBlock", "blokady w wiadomościach", moved_blocks)
    run.discarded("chat.ChatBlock", "blokady-duplikaty", dropped_blocks, "odpadają")


def _mentorships(run: _Run) -> None:
    Mentorship = _model("alumni.Mentorship")
    open_statuses = ("REQUESTED", "ACCEPTED")
    moved = 0
    for mentorship in _qs(Mentorship).filter(Q(mentor=run.drop) | Q(mentee=run.drop)):
        mentor = run.keep.pk if mentorship.mentor_id == run.drop.pk else mentorship.mentor_id
        mentee = run.keep.pk if mentorship.mentee_id == run.drop.pk else mentorship.mentee_id
        if mentor == mentee:
            run.conflict("Kopia i konto docelowe są ze sobą w relacji mentoringu.")
            continue
        if (
            mentorship.status in open_statuses
            and _qs(Mentorship)
            .filter(mentor_id=mentor, mentee_id=mentee, status__in=open_statuses)
            .exclude(pk=mentorship.pk)
            .exists()
        ):
            run.conflict("Oba konta mają otwartą relację mentoringu z tą samą osobą.")
            continue
        moved += 1
        if run.apply:
            _qs(Mentorship).filter(pk=mentorship.pk).update(mentor_id=mentor, mentee_id=mentee)
    run.moved("alumni.Mentorship", "relacje mentoringu", moved)


def _profile(run: _Run) -> dict:
    """Pola profilu docelowego uzupełniane z kopii (``docs/tasks/ACC-MERGE-01.md`` § 1.1)."""
    keep, drop = run.keep, run.drop
    changes: dict = {}
    # Zgody: dowody (``ConsentRecord``) przechodzą w całości; projekcje – późniejsza deklaracja.
    for name in ("gdpr_consent_at", "terms_accepted_at"):
        mine, theirs = getattr(drop, name), getattr(keep, name)
        if mine is not None and (theirs is None or mine > theirs):
            changes[name] = mine
    # Zgoda opiekuna dotyczy osoby, a nie konta – jest, jeśli ma ją którekolwiek z dwóch.
    if drop.guardian_consent and not keep.guardian_consent:
        changes["guardian_consent"] = True
        if drop.guardian_email:
            changes["guardian_email"] = drop.guardian_email
    elif drop.guardian_email and not keep.guardian_email:
        changes["guardian_email"] = drop.guardian_email
    newer_declaration = drop.gdpr_consent_at is not None and (
        keep.gdpr_consent_at is None or drop.gdpr_consent_at > keep.gdpr_consent_at
    )
    if drop.publish_full_name != keep.publish_full_name and newer_declaration:
        changes["publish_full_name"] = drop.publish_full_name
    for name in ("phone", "supervisor_email", "grade"):
        if not getattr(keep, name) and getattr(drop, name):
            changes[name] = getattr(drop, name)
    if keep.birth_date is None and drop.birth_date is not None and drop.birth_date.year == keep.birth_year:
        changes["birth_date"] = drop.birth_date
    if drop.delegation_id != keep.delegation_id:
        run.conflict(
            "Konta należą do różnych delegacji – przenieś ucznia w panelu delegacji przed scaleniem."
        )
    run.plan.profile_fields = sorted(changes)
    if run.apply and changes:
        for name, value in changes.items():
            setattr(keep, name, value)
        keep.save(update_fields=list(changes))
    return changes


# --- reguły ogólne -------------------------------------------------------------------------------


def _rows(queryset, key: tuple[str, ...], extra: tuple[str, ...] = ()) -> list[tuple]:
    """``[(pk, klucz, dodatki)]`` – klucz jako krotka (pusta przy regule bez klucza)."""
    out = []
    for row in queryset.values_list("pk", *key, *extra):
        out.append((row[0], tuple(row[1 : 1 + len(key)]), tuple(row[1 + len(key) :])))
    return out


def _generic(run: _Run, model, fld, rule: Rule) -> None:
    """Reguła ogólna jednej relacji (wszystko poza ``CUSTOM``)."""
    if rule.kind in (CUSTOM, PROFILE, AUDIT_LOG):
        return
    to_user = fld.related_model is User
    source = run.drop.user if to_user else run.drop
    target = run.keep.user if to_user else run.keep
    key = f"{model._meta.label}.{fld.name}"
    label = _label(model, fld)
    manager = _qs(model)
    if rule.kind == ROLE:
        for participant, side in ((run.keep, "docelowe"), (run.drop, "kopia")):
            subject = participant.user if to_user else participant
            if manager.filter(**{fld.name: subject}).exists():
                run.refuse(f"Konto {_code(participant)} ({side}) ma inną rolę: {rule.note}.")
        return
    rows = manager.filter(**{fld.name: source})
    if rule.kind == STAYS:
        run.discarded(key, label, rows.count(), RULE_LABELS[STAYS])
        return
    if rule.kind == MOVE:
        count = rows.count()
        run.moved(key, label, count)
        if run.apply and count:
            rows.update(**{fld.name: target})
        return
    if rule.kind == CONFLICT:
        mine = _rows(rows, rule.key)
        if not mine:
            return
        theirs = {row_key for _pk, row_key, _ in _rows(manager.filter(**{fld.name: target}), rule.key)}
        if any(row_key in theirs for _pk, row_key, _ in mine):
            run.conflict(rule.note or f"Oba konta mają: {label}.")
            return
        run.moved(key, label, len(mine))
        if run.apply:
            rows.update(**{fld.name: target})
        return
    if rule.kind == TARGET:
        count = rows.count()
        if count and manager.filter(**{fld.name: target}).exists():
            run.discarded(key, label, count, RULE_LABELS[TARGET])
            return
        run.moved(key, label, count)
        if run.apply and count:
            rows.update(**{fld.name: target})
        return
    if rule.kind == MOVE_UNIQUE:
        theirs = {row_key for _pk, row_key, _ in _rows(manager.filter(**{fld.name: target}), rule.key)}
        duplicates, movable = [], []
        for pk, row_key, _ in _rows(rows, rule.key):
            (duplicates if row_key in theirs else movable).append(pk)
            theirs.add(row_key)
        run.moved(key, label, len(movable), RULE_LABELS[MOVE_UNIQUE])
        run.discarded(key, label, len(duplicates), "duplikaty odpadają")
        if run.apply:
            manager.filter(pk__in=duplicates).delete()
            manager.filter(pk__in=movable).update(**{fld.name: target})
        return
    if rule.kind == NEWER:
        theirs = {
            row_key: (pk, extra[0])
            for pk, row_key, extra in _rows(manager.filter(**{fld.name: target}), rule.key, (rule.stamp,))
        }
        movable, replaced, older = [], [], []
        for pk, row_key, extra in _rows(rows, rule.key, (rule.stamp,)):
            other = theirs.get(row_key)
            if other is None:
                movable.append(pk)
            elif extra[0] is not None and (other[1] is None or extra[0] > other[1]):
                replaced.append(other[0])
                movable.append(pk)
            else:
                older.append(pk)
        run.moved(key, label, len(movable), RULE_LABELS[NEWER])
        run.discarded(key, label, len(older) + len(replaced), "starsze odpadają")
        if run.apply:
            manager.filter(pk__in=replaced + older).delete()
            manager.filter(pk__in=movable).update(**{fld.name: target})
        return
    raise AssertionError(f"Nieznana reguła {rule.kind} dla {key}")


def _analyse(run: _Run) -> dict:
    """Wszystkie reguły w ustalonej kolejności. Zwraca zmiany pól profilu (do audytu)."""
    _check_accounts(run)
    changes = _profile(run)
    # Kolejność ma znaczenie przy zapisie: należności przed wpisami (wpis wskazuje należność), czat
    # przed ogólnym przepięciem członków rozmów (scalona rozmowa kopii znika razem z jej członkami).
    _fees(run)
    _entries(run)
    _teams(run)
    _certificates(run)
    _chat(run)
    _mentorships(run)
    for model, fld in relations():
        rule = rule_for(model, fld)
        if rule is None:
            source = run.drop.user if fld.related_model is User else run.drop
            if _qs(model).filter(**{fld.name: source}).exists():
                run.refuse(
                    "Kopia ma dane, których scalanie jeszcze nie obsługuje "
                    f"({model._meta.verbose_name_plural}) – zgłoś to administratorowi serwisu."
                )
            continue
        _generic(run, model, fld, rule)
    return changes


def plan_merge(keep: Participant, drop: Participant, *, actor=None) -> MergePlan:
    """Podgląd scalenia ``drop`` → ``keep`` bez żadnego zapisu (ekran „Scal…”)."""
    _assert_pair(keep, drop)
    run = _Run(keep, drop, apply=False, actor=actor)
    _analyse(run)
    return run.plan


def blocked_message(plan: MergePlan) -> str:
    return "Scalenie niemożliwe: " + " ".join(plan.refusals + plan.conflicts)


# --- wykonanie -----------------------------------------------------------------------------------

#: Domena adresu zastępczego kopii na czas zamiany adresów (``.invalid`` – RFC 2606, nie istnieje).
PLACEHOLDER_EMAIL_DOMAIN = "scalanie.invalid"


def _take_email(keep_user: User, drop_user: User, actor: User) -> None:
    """Konto docelowe dostaje adres kopii. Adres jest unikalny – najpierw zwalniamy go na kopii."""
    from allauth.account.models import EmailAddress

    from .activation import _record_allauth_verification
    from .profile import (
        _assert_may_change_protected_email,
        _forget_allauth_addresses,
        _remember_previous_email,
    )

    _assert_may_change_protected_email(keep_user, actor)
    old_email, new_email, verified_at = keep_user.email, drop_user.email, drop_user.email_verified_at
    EmailAddress.objects.filter(user=drop_user).delete()
    drop_user.email = f"scalanie-{drop_user.pk}@{PLACEHOLDER_EMAIL_DOMAIN}"
    drop_user.save(update_fields=["email"])
    keep_user.email = new_email
    fields = ["email"]
    if verified_at is not None and keep_user.email_verified_at is None:
        # Kopia potwierdziła ten adres kliknięciem w list – to samo potwierdzenie przechodzi z adresem.
        # Bez ``is_active`` osoba nie zalogowałaby się na żaden z dwóch adresów (docelowe czekało na
        # aktywację). Konta zablokowanego przez organizatora scalenie nie odblokowuje.
        keep_user.email_verified_at = verified_at
        fields.append("email_verified_at")
        if not keep_user.is_active and keep_user.blocked_at is None:
            keep_user.is_active = True
            fields.append("is_active")
    keep_user.save(update_fields=fields)
    _forget_allauth_addresses(keep_user, old_email)
    _remember_previous_email(keep_user, old_email)
    if keep_user.email_verified_at is not None:
        _record_allauth_verification(keep_user)


@transaction.atomic
def merge_participants(
    keep: Participant, drop: Participant, *, actor: User, request=None, take_email: bool = False
) -> MergeResult:
    """Scala konto ``drop`` w ``keep`` i usuwa ``drop``. Jedna transakcja – błąd cofa wszystko.

    Plan liczymy **od nowa** na zablokowanych wierszach (``select_for_update`` obu kont i obu profili,
    w kolejności kluczy – dwa scalenia tej samej pary w przeciwnych kierunkach nie zakleszczą się):
    między podglądem a kliknięciem kopia mogła oddać pracę albo docelowe – potwierdzić adres.
    """
    from apps.tenancy.context import competition_context

    from .profile import delete_account_by_coordinator

    _assert_pair(keep, drop)
    list(User._base_manager.select_for_update().filter(pk__in=[keep.user_id, drop.user_id]).order_by("pk"))
    list(Participant._base_manager.select_for_update().filter(pk__in=[keep.pk, drop.pk]).order_by("pk"))
    fresh = Participant._base_manager.select_related("user", "competition").in_bulk([keep.pk, drop.pk])
    if keep.pk not in fresh or drop.pk not in fresh:
        # Drugi koordynator scalił albo usunął jedno z kont, zanim dostaliśmy blokadę.
        raise DomainError(
            "Jedno z kont już nie istnieje – odśwież listę zdublowanych kont.",
            "MERGE_GONE",
            status.HTTP_409_CONFLICT,
        )
    keep, drop = fresh[keep.pk], fresh[drop.pk]
    _assert_pair(keep, drop)

    plan = plan_merge(keep, drop, actor=actor)
    if plan.blocked:
        raise DomainError(blocked_message(plan), "MERGE_BLOCKED", status.HTTP_409_CONFLICT)
    if take_email and not plan.can_take_email:
        raise DomainError(
            "Adres kopii można wziąć wyłącznie, gdy konto docelowe nie potwierdziło swojego adresu.",
            "MERGE_EMAIL_NOT_ALLOWED",
            status.HTTP_400_BAD_REQUEST,
        )

    run = _Run(keep, drop, apply=True, actor=actor)
    changes = _analyse(run)
    if take_email:
        _take_email(keep.user, drop.user, actor)
    kept_code, merged_code, merged_user_id = keep.public_code, drop.public_code, drop.user_id
    # Kopia jest już pusta, więc ``competition_footprint`` nie widzi śladu i konto znika w całości.
    # Kontekst konkursu jawnie: serwis usuwania pyta o „rolę gdzie indziej” względem konkursu z kontekstu.
    with competition_context(keep.competition):
        outcome = delete_account_by_coordinator(drop.user, actor=actor, request=request, via="merge")
    audit(
        actor,
        "account.merged",
        keep.user,
        {
            "kept": kept_code,
            "merged": merged_code,
            "merged_user_id": merged_user_id,
            "moved": run.plan.counts(run.plan.moved),
            "discarded": run.plan.counts(run.plan.discarded),
            "profile_fields": sorted(changes),
            "email_taken": take_email,
            "result": outcome,
        },
        request=request,
    )
    return MergeResult(plan=run.plan, outcome=outcome, email_taken=take_email)
