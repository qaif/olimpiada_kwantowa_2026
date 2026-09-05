"""Serializery oceniania – deklaratywne, bez logiki domenowej.

Najważniejsza reguła kształtu odpowiedzi: recenzent **nigdy** nie dostaje imienia, nazwiska,
e-maila ani szkoły uczestnika. Jedynym identyfikatorem jest ``participant_public_code``
(PROJEKT.md 2.2, ocenianie ślepe). Dlatego serializery recenzenta budują pola jawnie – nie ma tu
żadnego zagnieżdżonego serializera uczestnika, który mógłby kiedyś „urosnąć” o dane osobowe.

Druga reguła: w rundzie 1 recenzent nie widzi cudzych ocen. Serializer przydziału opisuje wyłącznie
własną recenzję i nie ma pola z ocenami pozostałych recenzentów.
"""

from django.urls import reverse
from rest_framework import serializers

from .models import FinalGrade, Review


class ReviewSerializer(serializers.ModelSerializer):
    """Własny przydział recenzenta. Zawiera wyłącznie dane, które wolno pokazać przy ślepej ocenie."""

    submission_id = serializers.IntegerField(read_only=True)
    participant_public_code = serializers.CharField(
        source="submission.entry.participant.public_code", read_only=True
    )
    stage_id = serializers.IntegerField(source="submission.entry.stage_id", read_only=True)
    stage_kind = serializers.CharField(source="submission.entry.stage.kind", read_only=True)
    problem_id = serializers.IntegerField(source="submission.problem_id", read_only=True)
    problem_number = serializers.IntegerField(source="submission.problem.number", read_only=True)
    problem_title = serializers.CharField(source="submission.problem.title", read_only=True)
    submission_status = serializers.CharField(source="submission.status", read_only=True)
    download_url = serializers.SerializerMethodField()
    file_available = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = (
            "id",
            "submission_id",
            "participant_public_code",
            "stage_id",
            "stage_kind",
            "problem_id",
            "problem_number",
            "problem_title",
            "submission_status",
            "round",
            "status",
            "score",
            "comment_internal",
            "comment_for_participant",
            "annotations",
            "assigned_at",
            "submitted_at",
            "download_url",
            "file_available",
        )
        read_only_fields = fields

    def get_download_url(self, obj: Review) -> str:
        return reverse("submissions:submission-download", kwargs={"pk": obj.submission_id})

    def get_file_available(self, obj: Review) -> bool:
        """Czy plik jest już do pobrania: recenzent dostaje wyłącznie plik po czystym skanie."""
        submission_file = obj.submission.latest_file
        return submission_file is not None and submission_file.is_clean


class ReviewDraftSerializer(serializers.Serializer):
    """Zapis szkicu (PATCH): każde pole opcjonalne, bez walidacji finalnej oceny."""

    score = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=1000)
    comment_internal = serializers.CharField(required=False, allow_blank=True)
    comment_for_participant = serializers.CharField(required=False, allow_blank=True)
    annotations = serializers.ListField(child=serializers.DictField(), required=False)


class ReviewSubmitSerializer(serializers.Serializer):
    """Wystawienie oceny. ``score`` jest obowiązkowy – zgodność ze skalą sprawdza serwis."""

    score = serializers.IntegerField()
    comment_internal = serializers.CharField(required=False, allow_blank=True, default="")
    comment_for_participant = serializers.CharField(required=False, allow_blank=True, default="")
    annotations = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class AssignReviewersSerializer(serializers.Serializer):
    """Parametr przydziału. Domyślnie dwóch niezależnych recenzentów na rozwiązanie.

    ``min_value=2``: ocena rundy 1 z jednym recenzentem nie ma jak się rozjechać, więc znikają
    i konsensus, i moderacja – procedura z PROJEKT.md 2.4 przestaje istnieć. Serwis przyjmuje
    ``per_submission=1`` (scenariusz awaryjny, wołany z shella), ale API tego nie oferuje.
    """

    per_submission = serializers.IntegerField(required=False, default=2, min_value=2, max_value=10)


class SkippedSubmissionSerializer(serializers.Serializer):
    """Rozwiązanie pominięte przy przydziale – do ręcznego załatwienia przez koordynatora."""

    submission_id = serializers.IntegerField(read_only=True)
    public_code = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)


class AssignmentResultSerializer(serializers.Serializer):
    submissions = serializers.IntegerField(read_only=True)
    assignments = serializers.IntegerField(read_only=True)
    skipped = SkippedSubmissionSerializer(many=True, read_only=True)


class ModerationReviewSerializer(serializers.ModelSerializer):
    """Recenzja w widoku koordynatora. Koordynator widzi wszystko, w tym tożsamość recenzenta."""

    reviewer_id = serializers.IntegerField(read_only=True)
    reviewer_email = serializers.EmailField(source="reviewer.user.email", read_only=True)

    class Meta:
        model = Review
        fields = (
            "id",
            "reviewer_id",
            "reviewer_email",
            "round",
            "status",
            "score",
            "comment_internal",
            "comment_for_participant",
            "submitted_at",
        )
        read_only_fields = fields


class ModerationSubmissionSerializer(serializers.Serializer):
    """Rozwiązanie w moderacji wraz z obiema ocenami rundy 1."""

    id = serializers.IntegerField(read_only=True)
    participant_public_code = serializers.CharField(source="entry.participant.public_code", read_only=True)
    stage_id = serializers.IntegerField(source="entry.stage_id", read_only=True)
    problem_id = serializers.IntegerField(read_only=True)
    problem_number = serializers.IntegerField(source="problem.number", read_only=True)
    status = serializers.CharField(read_only=True)
    reviews = ModerationReviewSerializer(many=True, read_only=True)


class ResolveModerationSerializer(serializers.Serializer):
    score = serializers.IntegerField()
    rationale = serializers.CharField(required=False, allow_blank=True, default="")


class AssignThirdReviewerSerializer(serializers.Serializer):
    reviewer_id = serializers.IntegerField()


class FinalGradeSerializer(serializers.ModelSerializer):
    submission_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = FinalGrade
        fields = ("id", "submission_id", "score", "method", "decided_at", "rationale")
        read_only_fields = fields


class DisputeReviewSerializer(serializers.Serializer):
    """Jedna ocena rundy 1 w materiale rozjemczym.

    Celowo **nie** ma tu pola z recenzentem (ani id, ani e-maila) i nie ma pola ``id`` recenzji:
    trzeci recenzent ma zobaczyć rozjazd, a nie osoby. Kształt jest budowany od zera, więc
    dopisanie kiedykolwiek pola do ``ReviewSerializer`` nie przecieknie do tej odpowiedzi.
    """

    score = serializers.IntegerField(read_only=True)
    comment_internal = serializers.CharField(read_only=True, allow_blank=True)
