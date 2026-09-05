"""Modele rozwiązań: zgłoszenie (wersjonowane) i jego plik w prywatnym storage.

Zasady:
- plik nigdy nie jest identyfikowany nazwą od użytkownika – ``object_key`` buduje ``storage.py``
  z identyfikatorów technicznych i sumy sha256 (PROJEKT.md 1.3),
- filtr per rola jest w queryseckie (``Submission.objects.for_user``), nie w widoku (PROJEKT.md 2.2),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import GROUP_COORDINATOR, CommitteeStatus
from apps.competitions.models import Problem, StageEntry


class SubmissionStatus(models.TextChoices):
    """Cykl życia zgłoszenia (PROJEKT.md 2.4). T-04 używa czterech pierwszych wartości."""

    SUBMITTED = "SUBMITTED", "oddane"
    SCANNING = "SCANNING", "skanowanie"
    REJECTED_INFECTED = "REJECTED_INFECTED", "odrzucone (wirus)"
    LOCKED = "LOCKED", "zablokowane"
    IN_REVIEW = "IN_REVIEW", "w ocenie"
    MODERATION = "MODERATION", "moderacja"
    GRADED_PROVISIONAL = "GRADED_PROVISIONAL", "ocena wstępna"
    APPEALED = "APPEALED", "reklamacja"
    FINAL = "FINAL", "ocena ostateczna"


class AvStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    # Stan trwały: skan nie ma jak się udać (brak obiektu w storage, plik ponad StreamMaxLength clamd).
    # Bez niego plik zostawałby na zawsze PENDING, a zgłoszenie w SCANNING – patrz apps.submissions.tasks.
    ERROR = "ERROR", "błąd skanu"


class SubmissionQuerySet(models.QuerySet):
    def for_user(self, user):
        """Widoczność per rola: uczestnik → własne, koordynator → wszystkie, recenzent → przydzielone.

        Członek komisji odwoławczej widzi dodatkowo rozwiązania, na które złożono reklamację –
        ale wyłącznie te, przy których nie ma konfliktu interesów, czyli nie jest autorem
        ``grading.Review`` z rundy 1 ani 2 tego rozwiązania (PROJEKT.md 2.4). Definicja rund jest
        jedna: ``apps.appeals.models.CONFLICTING_ROUNDS``, ta sama, którą sprawdza serwis decyzji.

        Reguła jest domyślnie zamknięta: kto nie ma ani profilu uczestnika, ani aktywnego profilu
        komitetu z przydziałem (``grading.Review``) lub uprawnieniem komisji odwoławczej, nie widzi
        niczego. Relacje ``reviews`` i ``appeals`` są odwrotnymi stronami FK z ``apps.grading``
        i ``apps.appeals`` – celowo przez nazwę, żeby nie robić importu w drugą stronę (te aplikacje
        zależą od submissions, nie odwrotnie).

        Definicja „aktywnego recenzenta” (status ACTIVE **i** grupa ``reviewer``) jest jedna dla
        całego systemu i mieszka w ``apps.accounts.services.active_reviewer_profile`` – widoczność
        plików nie może być luźniejsza niż uprawnienie, które wpuszcza do ``/api/grading/reviews/``.
        """
        # Importy lokalne. ``apps.accounts.services`` ciągnie za sobą warstwę serwisową, a ten moduł
        # jest ładowany podczas rejestrowania aplikacji. ``apps.appeals`` i ``apps.grading`` zależą
        # od ``apps.submissions``, więc import na poziomie modułu byłby cyklem – stąd tutaj, gdzie
        # obie aplikacje są już załadowane.
        from apps.accounts.services import active_reviewer_profile
        from apps.appeals.models import CONFLICTING_ROUNDS
        from apps.grading.models import Review

        if not user or not user.is_authenticated or not user.is_active:
            return self.none()
        if user.groups.filter(name=GROUP_COORDINATOR).exists():
            return self
        conditions = []
        participant = getattr(user, "participant", None)
        if participant is not None:
            conditions.append(Q(entry__participant=participant))
        reviewer = active_reviewer_profile(user)
        if reviewer is not None:
            conditions.append(Q(reviews__reviewer=reviewer))
        member = getattr(user, "committee_member", None)
        appeals_member = (
            member
            if member is not None and member.status == CommitteeStatus.ACTIVE and member.is_appeals_committee
            else None
        )
        if appeals_member is not None:
            # Konflikt interesów wyklucza się podzapytaniem po kluczu głównym, a nie negacją na
            # złączeniu ``reviews``. Dwa powody:
            #
            # - ``~Q(reviews__reviewer=...)`` w tym samym ``filter()`` co gałąź recenzenta reużywa
            #   jej złączenia i Django koreluje negację z *wierszem recenzji*, a nie ze zgłoszeniem
            #   (``NOT EXISTS(... U1.id = grading_review.id)``). Wystarczała wtedy jedna cudza
            #   recenzja tej pracy, żeby warunek był spełniony mimo konfliktu. Efekt maskowała
            #   gałąź recenzenta (która i tak pokazuje własne przydziały), ale poprawność warunku
            #   nie może zależeć od tego, jakie inne gałęzie akurat są w zapytaniu,
            # - rundy konfliktowe mają jedną definicję (``apps.appeals.models.CONFLICTING_ROUNDS``).
            #   Negacja po całej relacji ukrywała reklamację przy recenzji z *dowolnej* rundy, więc
            #   „nie widzę” było szersze niż „nie mogę rozstrzygnąć” z serwisu.
            conflicted = Review.objects.filter(reviewer=appeals_member, round__in=CONFLICTING_ROUNDS).values(
                "submission_id"
            )
            conditions.append(Q(appeals__isnull=False) & ~Q(pk__in=conflicted))
        if not conditions:
            return self.none()
        query = conditions[0]
        for extra in conditions[1:]:
            query |= extra
        queryset = self.filter(query)
        # JOIN po recenzjach i reklamacjach potrafi zwielokrotnić wiersze (dwie recenzje tego samego
        # zgłoszenia w rundach 1 i 2), więc tylko ta gałąź wymaga odsiania duplikatów.
        if reviewer is not None or appeals_member is not None:
            return queryset.distinct()
        return queryset


class Submission(models.Model):
    """Jedno oddanie zadania przez uczestnika. Kolejne oddania to kolejne wersje, nie nadpisanie."""

    uuid = models.UUIDField("uuid", default=uuid.uuid4, unique=True, editable=False)
    entry = models.ForeignKey(StageEntry, on_delete=models.CASCADE, related_name="submissions")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="submissions")
    version = models.PositiveSmallIntegerField("wersja", default=1)
    submitted_at = models.DateTimeField("oddane", default=timezone.now)
    is_late = models.BooleanField("po deadline (w tolerancji)", default=False)
    status = models.CharField(
        "status", max_length=24, choices=SubmissionStatus.choices, default=SubmissionStatus.SUBMITTED
    )

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "rozwiązanie"
        verbose_name_plural = "rozwiązania"
        ordering = ("-submitted_at", "-version", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "problem", "version"], name="submissions_submission_unique_version"
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1), name="submissions_submission_version_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}/zad. {self.problem_id} v{self.version} ({self.status})"

    @property
    def latest_file(self) -> "SubmissionFile | None":
        """Najnowszy plik zgłoszenia. W praktyce jest dokładnie jeden, ale kolejność musi być jawna.

        Porządek malejący po ``id`` zamiast domyślnego: jeśli kiedykolwiek pojawi się drugi plik,
        pobranie ma dać ten świeższy, a nie ten, który akurat wypadł pierwszy w ``Meta.ordering``.

        Gdy wywołujący zrobił ``prefetch_related("files")``, sortujemy w Pythonie po gotowej liście.
        ``order_by(...)`` omijałby cache prefetchu i robił po jednym zapytaniu na każdy wiersz listy
        (N+1 w ``GET /api/grading/reviews/`` i ``GET /api/appeals/``).
        """
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "files" in cache:
            files = sorted(self.files.all(), key=lambda item: item.pk, reverse=True)
            return files[0] if files else None
        return self.files.order_by("-id").first()

    @property
    def participant(self):
        return self.entry.participant


class SubmissionFile(models.Model):
    """Metadane pliku w prywatnym buckecie. Treść żyje wyłącznie w storage, nigdy w bazie."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="files")
    object_key = models.CharField("klucz obiektu", max_length=512, unique=True)
    sha256 = models.CharField("sha256", max_length=64)
    original_name = models.CharField("nazwa od użytkownika", max_length=255)
    mime = models.CharField("typ MIME", max_length=100)
    size_bytes = models.BigIntegerField("rozmiar (B)")
    av_status = models.CharField(
        "status skanu", max_length=16, choices=AvStatus.choices, default=AvStatus.PENDING
    )
    av_signature = models.CharField("sygnatura wirusa", max_length=200, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    scanned_at = models.DateTimeField("zeskanowany", null=True, blank=True)

    class Meta:
        verbose_name = "plik rozwiązania"
        verbose_name_plural = "pliki rozwiązań"
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.object_key} ({self.av_status})"

    @property
    def is_clean(self) -> bool:
        return self.av_status == AvStatus.CLEAN
