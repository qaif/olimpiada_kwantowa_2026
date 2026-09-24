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

from apps.core.points_api import PointsField

from .models import FinalGrade, ProblemReviewerRule, Review

#: Techniczne granice punktów na wejściu API – te same, co dawne ``IntegerField(max_value=1000)``.
#: Dolna jest symetryczna z tego samego powodu, co w formularzu szkicu: kształt, a nie skala.
SCORE_LIMIT = 1000


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
    # Liczba JSON (``5`` albo ``4.25``), a nie tekst ``"5.00"`` domyślnego pola dziesiętnego –
    # kontrakt w ``apps.core.points_api`` i ``docs/API.md`` (wydanie 0.35.0).
    score = PointsField(read_only=True, allow_null=True)
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
            # Powód anulowania obok statusu: „anulowana” znaczy co innego dla pracy odebranej przez
            # koordynatora, a co innego dla pracy, której uczestnik wysłał nową wersję. Pusty napis
            # dla recenzji nieanulowanych i dla tych sprzed wprowadzenia pola.
            "cancel_reason",
            "score",
            "comment_internal",
            "comment_for_participant",
            "annotations",
            # Punkty cząstkowe za kryteria zadania. Pusta lista dla zadań bez rubryki i dla ocen
            # sprzed jej wprowadzenia – klient nie musi rozróżniać tych dwóch sytuacji, bo w obu
            # ocena jest w ``score``, który pozostaje źródłem prawdy.
            "rubric",
            "assigned_at",
            # Termin **tej** recenzji, wyliczony przy przydziale (apps.grading.deadlines).
            # ``null`` dla przydziałów sprzed wprowadzenia terminów.
            "due_at",
            "submitted_at",
            # ``revised_at`` obok ``submitted_at``, a nie zamiast niego: recenzent i koordynator
            # muszą widzieć, że ocena była poprawiana, bez zaglądania do audytu.
            "revised_at",
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

    score = PointsField(required=False, allow_null=True, min_value=0, max_value=SCORE_LIMIT)
    comment_internal = serializers.CharField(required=False, allow_blank=True)
    comment_for_participant = serializers.CharField(required=False, allow_blank=True)
    annotations = serializers.ListField(child=serializers.DictField(), required=False)
    # Rubryka bez ``default``: brak pola znaczy „nie ruszaj”, a nie „wyczyść” – dokładnie jak przy
    # adnotacjach. Kształt pozycji sprawdza ``apps.grading.rubric.validate_rubric``, bo to reguła
    # domenowa (kryteria tego zadania), a nie kwestia typów w ładunku.
    rubric = serializers.ListField(child=serializers.DictField(), required=False)


class ReviewSubmitSerializer(serializers.Serializer):
    """Wystawienie oceny. ``score`` jest obowiązkowy – zgodność ze skalą sprawdza serwis.

    ``score`` zostaje obowiązkowy także przy rubryce: zadanie bez kryteriów ocenia się wyłącznie
    nim, a gdy rubryka przyjdzie, serwis i tak liczy sumę sam (przysłana ocena jest wtedy
    ignorowana – patrz ``services._score_from_rubric``).

    Od wydania 0.35.0 ``score`` przyjmuje liczbę dziesiętną (``4.25``) albo tekst (``"4,25"``);
    w etapie „tylko ze skali” serwis nadal przyjmie wyłącznie wartość skali.
    """

    score = PointsField()
    comment_internal = serializers.CharField(required=False, allow_blank=True, default="")
    comment_for_participant = serializers.CharField(required=False, allow_blank=True, default="")
    annotations = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    rubric = serializers.ListField(child=serializers.DictField(), required=False)


class AssignReviewersSerializer(serializers.Serializer):
    """Parametr przydziału. Domyślnie dwóch niezależnych recenzentów na rozwiązanie.

    ``min_value=2``: ocena rundy 1 z jednym recenzentem nie ma jak się rozjechać, więc znikają
    i konsensus, i moderacja – procedura z PROJEKT.md 2.4 przestaje istnieć. Serwis przyjmuje
    ``per_submission=1`` (scenariusz awaryjny, wołany z shella), ale API tego nie oferuje.
    """

    per_submission = serializers.IntegerField(required=False, default=2, min_value=2, max_value=10)


class SkippedSubmissionSerializer(serializers.Serializer):
    """Rozwiązanie pominięte przy przydziale – do ręcznego załatwienia przez koordynatora.

    ``reviewer_id`` jest wypełniony tylko dla ``RULE_REVIEWER_CONFLICT``: wtedy pominięcie dotyczy
    konkretnej reguły i koordynator musi wiedzieć, której. Przy ``NOT_ENOUGH_REVIEWERS`` nie ma
    jednej osoby do wskazania – zabrakło ich w ogóle.
    """

    submission_id = serializers.IntegerField(read_only=True)
    public_code = serializers.CharField(read_only=True)
    reason = serializers.CharField(read_only=True)
    reviewer_id = serializers.IntegerField(read_only=True, allow_null=True)


class AssignmentResultSerializer(serializers.Serializer):
    submissions = serializers.IntegerField(read_only=True)
    assignments = serializers.IntegerField(read_only=True)
    # Termin, który dostały wszystkie recenzje z tego przebiegu – najczęstsze pytanie koordynatora
    # tuż po przydziale („do kiedy mają czas?”), a odpowiedź zna wyłącznie serwis.
    due_at = serializers.DateTimeField(read_only=True, allow_null=True)
    skipped = SkippedSubmissionSerializer(many=True, read_only=True)


class ModerationReviewSerializer(serializers.ModelSerializer):
    """Recenzja w widoku koordynatora. Koordynator widzi wszystko, w tym tożsamość recenzenta."""

    reviewer_id = serializers.IntegerField(read_only=True)
    reviewer_email = serializers.EmailField(source="reviewer.user.email", read_only=True)
    score = PointsField(read_only=True, allow_null=True)

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
    score = PointsField()
    rationale = serializers.CharField(required=False, allow_blank=True, default="")


class AssignThirdReviewerSerializer(serializers.Serializer):
    reviewer_id = serializers.IntegerField()


class AssignReviewerSerializer(serializers.Serializer):
    """Ręczny przydział jednej pracy jednemu recenzentowi."""

    reviewer_id = serializers.IntegerField()


class ProblemRuleCreateSerializer(serializers.Serializer):
    """Reguła „to zadanie recenzuje ta osoba”. Etap wynika z adresu, zadanie musi do niego należeć."""

    problem_id = serializers.IntegerField()
    reviewer_id = serializers.IntegerField()


class ProblemReviewerRuleSerializer(serializers.ModelSerializer):
    """Reguła w widoku koordynatora – z tożsamością recenzenta, bo to jego ekran."""

    problem_id = serializers.IntegerField(read_only=True)
    problem_number = serializers.IntegerField(source="problem.number", read_only=True)
    reviewer_id = serializers.IntegerField(read_only=True)
    reviewer_email = serializers.EmailField(source="reviewer.user.email", read_only=True)

    class Meta:
        model = ProblemReviewerRule
        fields = ("id", "problem_id", "problem_number", "reviewer_id", "reviewer_email", "created_at")
        read_only_fields = fields


class ProblemRuleResultSerializer(serializers.Serializer):
    """Wynik utworzenia reguły: sama reguła plus liczniki zastosowania jej do prac już zablokowanych."""

    rule = ProblemReviewerRuleSerializer(read_only=True)
    assigned = serializers.IntegerField(read_only=True)
    conflicts = serializers.IntegerField(read_only=True)
    already = serializers.IntegerField(read_only=True)


class FinalGradeSerializer(serializers.ModelSerializer):
    submission_id = serializers.IntegerField(read_only=True)
    score = PointsField(read_only=True)

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

    score = PointsField(read_only=True)
    comment_internal = serializers.CharField(read_only=True, allow_blank=True)


class SetReviewScoreSerializer(serializers.Serializer):
    """Korekta punktów pojedynczej recenzji przez koordynatora.

    ``rationale`` jest opcjonalne i **nie** jest uzasadnieniem oceny końcowej – to notatka, która
    trafia do komentarza wewnętrznego recenzji wpisanej za recenzenta i do audytu.
    """

    score = PointsField()
    rationale = serializers.CharField(required=False, allow_blank=True, default="")


class OverrideFinalGradeSerializer(serializers.Serializer):
    """Korekta oceny końcowej. Uzasadnienie jest obowiązkowe – długość sprawdza serwis."""

    score = PointsField()
    rationale = serializers.CharField()


class OverrideResultSerializer(serializers.Serializer):
    """Wynik korekty: ocena oraz ostrzeżenie, że ogłoszona tabela wyników jest już nieaktualna."""

    grade = FinalGradeSerializer(read_only=True)
    results_stale = serializers.BooleanField(read_only=True)
    cancelled_reviews = serializers.IntegerField(read_only=True)
