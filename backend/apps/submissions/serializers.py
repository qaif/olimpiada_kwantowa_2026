"""Serializery rozwiązań – deklaratywne, bez logiki domenowej.

Uczestnik nie dostaje ``object_key``: klucz w prywatnym buckecie jest szczegółem infrastruktury,
a pobranie odbywa się wyłącznie przez ``submissions/{id}/download/`` po sprawdzeniu uprawnień.

Ocena i reklamacja są tu dołączone w kształcie, jaki wolno pokazać uczestnikowi: punkty,
uzasadnienie decyzji i status. Nigdy ``comment_internal`` i nigdy tożsamość recenzenta ani składu
komisji (PROJEKT.md 2.4) – także pośrednio, przez ``FinalGrade.rationale``, które dla trybu
``THIRD_REVIEW`` jest kopią komentarza wewnętrznego (patrz ``SubmissionFinalGradeSerializer``).

Relacje ``final_grade`` i ``appeals`` są odwrotnymi stronami FK z ``apps.grading`` i ``apps.appeals``
– celowo przez nazwę, bez importu w drugą stronę.
"""

from rest_framework import serializers

from .models import Submission, SubmissionFile

#: Jedyny tryb ustalenia oceny, przy którym ``FinalGrade.rationale`` powstaje z tekstu *pisanego do
#: uczestnika* – to uzasadnienie decyzji komisji odwoławczej (``AppealDecision.justification``).
#: Wartość jest wpisana literałem, a nie zaimportowana z ``apps.grading``: zależność idzie w drugą
#: stronę (grading zna submissions), a odwrotny import zamknąłby cykl. Rozjazd wartości łapie test.
GRADE_METHOD_APPEAL = "APPEAL"


class SubmissionFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubmissionFile
        fields = ("original_name", "size_bytes", "sha256", "mime", "av_status")
        read_only_fields = fields


class SubmissionFinalGradeSerializer(serializers.Serializer):
    """Ocena uzgodniona w wersji dla uczestnika: punkty, tryb i – warunkowo – uzasadnienie."""

    score = serializers.IntegerField(read_only=True)
    method = serializers.CharField(read_only=True)
    decided_at = serializers.DateTimeField(read_only=True)
    rationale = serializers.SerializerMethodField()

    def get_rationale(self, obj) -> str | None:
        """``rationale`` tylko dla oceny po reklamacji – w pozostałych trybach ``None``.

        ``FinalGrade.rationale`` nie jest jednorodne: dla ``APPEAL`` to uzasadnienie decyzji
        komisji, pisane wprost do uczestnika, ale dla ``THIRD_REVIEW`` to dosłowna kopia
        ``Review.comment_internal`` trzeciego recenzenta, a dla ``MODERATION`` – notatka
        z posiedzenia. Komentarz wewnętrzny nie może trafić do uczestnika (PROJEKT.md 2.4:
        „``comment_internal`` i tożsamość recenzenta nie są ujawniane”), więc lista własnych
        rozwiązań oddaje to pole wyłącznie w trybie, w którym z definicji jest jawne.
        Uzasadnienia dla pozostałych trybów uczestnik dostaje przez ``comment_for_participant``
        (T-07), a nie tędy.
        """
        return obj.rationale if obj.method == GRADE_METHOD_APPEAL else None


class SubmissionAppealSerializer(serializers.Serializer):
    """Reklamacja uczestnika wraz z rozstrzygnięciem, jeśli już zapadło."""

    id = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    filed_at = serializers.DateTimeField(read_only=True)
    argument = serializers.CharField(read_only=True)
    justification = serializers.SerializerMethodField()
    new_score = serializers.SerializerMethodField()
    decided_at = serializers.SerializerMethodField()

    def _decision(self, obj):
        return getattr(obj, "decision", None)

    def get_justification(self, obj) -> str | None:
        decision = self._decision(obj)
        return decision.justification if decision is not None else None

    def get_new_score(self, obj) -> int | None:
        decision = self._decision(obj)
        return decision.new_score if decision is not None else None

    def get_decided_at(self, obj) -> str | None:
        decision = self._decision(obj)
        if decision is None:
            return None
        # Pole metody nie przechodzi przez DateTimeField, więc format ISO trzeba nadać jawnie.
        return serializers.DateTimeField().to_representation(decision.decided_at)


class SubmissionSerializer(serializers.ModelSerializer):
    """Zgłoszenie wraz z metadanymi pliku – kształt odpowiedzi ``POST .../submissions/``."""

    file = SubmissionFileSerializer(source="latest_file", read_only=True)
    problem_number = serializers.IntegerField(source="problem.number", read_only=True)
    stage_id = serializers.IntegerField(source="entry.stage_id", read_only=True)
    final_grade = serializers.SerializerMethodField()
    appeal = serializers.SerializerMethodField()

    class Meta:
        model = Submission
        fields = (
            "id",
            "stage_id",
            "problem_id",
            "problem_number",
            "version",
            "status",
            "submitted_at",
            "is_late",
            "file",
            "final_grade",
            "appeal",
        )
        read_only_fields = fields

    def get_final_grade(self, obj: Submission) -> dict | None:
        # Odwrotna strona OneToOne rzuca wyjątek dziedziczący po AttributeError, więc getattr
        # z domyślną wartością jest tu poprawnym sposobem na „oceny jeszcze nie ma”.
        grade = getattr(obj, "final_grade", None)
        if grade is None:
            return None
        return SubmissionFinalGradeSerializer(grade).data

    def get_appeal(self, obj: Submission) -> dict | None:
        appeal = next(iter(obj.appeals.all()), None)
        if appeal is None:
            return None
        return SubmissionAppealSerializer(appeal).data


class SubmissionUploadSerializer(serializers.Serializer):
    """Wejście uploadu. Pole jest dokładnie jedno – ``entry`` bierze się z ``request.user``."""

    file = serializers.FileField(write_only=True)


class SubmissionGroupSerializer(serializers.Serializer):
    """Rozwiązania jednego zadania: najnowsza wersja i historia starszych wersji."""

    stage_id = serializers.IntegerField(read_only=True)
    problem_id = serializers.IntegerField(read_only=True)
    problem_number = serializers.IntegerField(read_only=True)
    latest = SubmissionSerializer(read_only=True)
    history = SubmissionSerializer(many=True, read_only=True)
