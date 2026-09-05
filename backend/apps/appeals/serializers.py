"""Serializery reklamacji – deklaratywne, bez logiki domenowej.

Dwie reguły kształtu odpowiedzi:

- komisja odwoławcza dostaje uczestnika wyłącznie jako ``participant_public_code`` – żadnego
  imienia, nazwiska, e-maila ani szkoły (ocenianie i rozpatrywanie reklamacji jest ślepe).
  Widzi za to obie oceny rundy 1 z ``comment_internal``, bo bez nich nie da się ocenić zarzutu;
  tożsamość recenzentów nie jest przy tym ujawniana,
- uczestnik dostaje wyłącznie własną reklamację: status, uzasadnienie decyzji i nową punktację.
  Nigdy ``comment_internal`` i nigdy tożsamości recenzenta (PROJEKT.md 2.4).
"""

from django.urls import reverse
from rest_framework import serializers

from .models import DECIDABLE_STATUSES, MIN_ARGUMENT_LENGTH, Appeal


class AppealDecisionSerializer(serializers.Serializer):
    """Decyzja tak, jak wolno ją pokazać uczestnikowi i komisji.

    Statusu nie ma tutaj celowo – trzyma go ``Appeal`` i podaje serializer nadrzędny, żeby
    odczyt decyzji nie dociągał reklamacji dodatkowym zapytaniem.
    """

    new_score = serializers.IntegerField(read_only=True, allow_null=True)
    justification = serializers.CharField(read_only=True)
    decided_at = serializers.DateTimeField(read_only=True)


class MyAppealSerializer(serializers.ModelSerializer):
    """Własna reklamacja uczestnika wraz z rozstrzygnięciem, jeśli już zapadło."""

    submission_id = serializers.IntegerField(read_only=True)
    stage_id = serializers.IntegerField(source="submission.entry.stage_id", read_only=True)
    problem_number = serializers.IntegerField(source="submission.problem.number", read_only=True)
    submission_status = serializers.CharField(source="submission.status", read_only=True)
    decision = serializers.SerializerMethodField()

    class Meta:
        model = Appeal
        fields = (
            "id",
            "submission_id",
            "stage_id",
            "problem_number",
            "submission_status",
            "status",
            "argument",
            "filed_at",
            "decision",
        )
        read_only_fields = fields

    def get_decision(self, obj: Appeal) -> dict | None:
        decision = getattr(obj, "decision", None)
        if decision is None:
            return None
        return {"status": obj.status, **AppealDecisionSerializer(decision).data}


class AppealFileSerializer(serializers.Serializer):
    """Wejście złożenia reklamacji. ``submission`` bierze się ze ścieżki, nigdy z body."""

    argument = serializers.CharField(min_length=MIN_ARGUMENT_LENGTH, trim_whitespace=True)


class AppealReviewSerializer(serializers.Serializer):
    """Ocena rundy 1 w widoku komisji: punkty i komentarz wewnętrzny, bez tożsamości recenzenta."""

    id = serializers.IntegerField(read_only=True)
    round = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    score = serializers.IntegerField(read_only=True, allow_null=True)
    comment_internal = serializers.CharField(read_only=True)
    comment_for_participant = serializers.CharField(read_only=True)
    submitted_at = serializers.DateTimeField(read_only=True, allow_null=True)


class AppealFinalGradeSerializer(serializers.Serializer):
    score = serializers.IntegerField(read_only=True)
    method = serializers.CharField(read_only=True)
    decided_at = serializers.DateTimeField(read_only=True)
    rationale = serializers.CharField(read_only=True)


class AppealQueueSerializer(serializers.ModelSerializer):
    """Reklamacja w kolejce komisji odwoławczej."""

    submission_id = serializers.IntegerField(read_only=True)
    participant_public_code = serializers.CharField(source="filed_by.public_code", read_only=True)
    stage_id = serializers.IntegerField(source="submission.entry.stage_id", read_only=True)
    problem_id = serializers.IntegerField(source="submission.problem_id", read_only=True)
    problem_number = serializers.IntegerField(source="submission.problem.number", read_only=True)
    problem_title = serializers.CharField(source="submission.problem.title", read_only=True)
    submission_status = serializers.CharField(source="submission.status", read_only=True)
    reviews = serializers.SerializerMethodField()
    final_grade = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    file_available = serializers.SerializerMethodField()

    class Meta:
        model = Appeal
        fields = (
            "id",
            "submission_id",
            "participant_public_code",
            "stage_id",
            "problem_id",
            "problem_number",
            "problem_title",
            "submission_status",
            "status",
            "argument",
            "filed_at",
            "reviews",
            "final_grade",
            "download_url",
            "file_available",
        )
        read_only_fields = fields

    def get_reviews(self, obj: Appeal) -> list:
        reviews = sorted(obj.submission.reviews.all(), key=lambda review: (review.round, review.pk))
        return AppealReviewSerializer(reviews, many=True).data

    def get_final_grade(self, obj: Appeal) -> dict | None:
        grade = getattr(obj.submission, "final_grade", None)
        if grade is None:
            return None
        return AppealFinalGradeSerializer(grade).data

    def get_download_url(self, obj: Appeal) -> str:
        return reverse("submissions:submission-download", kwargs={"pk": obj.submission_id})

    def get_file_available(self, obj: Appeal) -> bool:
        """Plik jest do pobrania dopiero po czystym skanie antywirusowym."""
        submission_file = obj.submission.latest_file
        return submission_file is not None and submission_file.is_clean


class AppealDecideSerializer(serializers.Serializer):
    """Wejście decyzji komisji. Zgodność ``new_score`` ze skalą sprawdza serwis."""

    status = serializers.ChoiceField(choices=[(item.value, item.label) for item in DECIDABLE_STATUSES])
    new_score = serializers.IntegerField(required=False, allow_null=True, default=None)
    justification = serializers.CharField(trim_whitespace=True)
