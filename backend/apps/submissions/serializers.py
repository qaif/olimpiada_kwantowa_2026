"""Serializery rozwiązań – deklaratywne, bez logiki domenowej.

Uczestnik nie dostaje ``object_key``: klucz w prywatnym buckecie jest szczegółem infrastruktury,
a pobranie odbywa się wyłącznie przez ``submissions/{id}/download/`` po sprawdzeniu uprawnień.

Ocena i reklamacja są tu dołączone w kształcie, jaki wolno pokazać uczestnikowi: punkty,
uzasadnienie decyzji i status. Nigdy ``comment_internal`` i nigdy tożsamość recenzenta ani składu
komisji (PROJEKT.md 2.4) – także pośrednio, przez ``FinalGrade.rationale``, które dla trybu
``THIRD_REVIEW`` jest kopią komentarza wewnętrznego (patrz ``SubmissionFinalGradeSerializer``).

Relacje ``final_grade`` i ``appeals`` są odwrotnymi stronami FK z ``apps.grading`` i ``apps.appeals``
– celowo przez nazwę, bez importu w drugą stronę.

**Punkty dopiero po ogłoszeniu wyników etapu** (v0.38.7, decyzja właściciela platformy: „na razie
uczeń widzi oceny dopiero po ostatecznym zatwierdzeniu”). Panel HTML trzymał tę regułę od zawsze –
wynik pokazuje wyłącznie zakładka „Wyniki” (``results_for_participant``), czyli etapy z
``Stage.results_published_at`` – a API oddawało ``final_grade.score`` od chwili, w której
``FinalGrade`` powstał, czyli w trakcie oceniania innych prac, a ``method`` zdradzał przy okazji
rozbieżność recenzentów (``THIRD_REVIEW``/``MODERATION``). Teraz przed publikacją ``score``,
``decided_at`` i ``new_score`` reklamacji mają wartość ``None`` (klucze zostają – klient API się nie
wywraca), a ``method`` jest zawsze sprowadzony do wartości bezpiecznej dla uczestnika
(:data:`PARTICIPANT_GRADE_METHODS`). Sygnał publikacji jest **ten sam**, co w panelu:
``entry.stage.results_published_at``, bez wyjątku dla etapu treningowego (panel go nie robi).
"""

from rest_framework import serializers

from apps.core.points import points_json
from apps.core.points_api import PointsField

from .models import Submission, SubmissionFile

#: Jedyny tryb ustalenia oceny, przy którym ``FinalGrade.rationale`` powstaje z tekstu *pisanego do
#: uczestnika* – to uzasadnienie decyzji komisji odwoławczej (``AppealDecision.justification``).
#: Wartość jest wpisana literałem, a nie zaimportowana z ``apps.grading``: zależność idzie w drugą
#: stronę (grading zna submissions), a odwrotny import zamknąłby cykl. Rozjazd wartości łapie test.
GRADE_METHOD_APPEAL = "APPEAL"

#: Tryb oceny w wersji dla uczestnika. Wewnętrzne tryby (``CONSENSUS``, ``THIRD_REVIEW``,
#: ``MODERATION``, ``OVERRIDE``) mówią, **jak** komitet doszedł do liczby – czy recenzenci się
#: zgodzili, czy trzeba było trzeciego, czy posiedzenia albo korekty koordynatora – i to jest wiedza
#: o przebiegu oceniania, a nie o pracy. Uczestnik dostaje jedną neutralną wartość ``REVIEW``.
#: Wyjątkiem jest ``APPEAL``: to tryb, w którym ``rationale`` jest pisane wprost do uczestnika
#: (uzasadnienie komisji odwoławczej), więc musi być rozpoznawalny, żeby klient wiedział, czemu
#: obok liczby stoi tekst.
PARTICIPANT_GRADE_METHOD_REVIEW = "REVIEW"
PARTICIPANT_GRADE_METHODS = (PARTICIPANT_GRADE_METHOD_REVIEW, GRADE_METHOD_APPEAL)


def participant_grade_method(method: str) -> str:
    """Tryb oceny w wersji dla uczestnika – ``APPEAL`` albo neutralne ``REVIEW``."""
    return GRADE_METHOD_APPEAL if method == GRADE_METHOD_APPEAL else PARTICIPANT_GRADE_METHOD_REVIEW


def results_published(submission) -> bool:
    """Czy etap tej pracy ma ogłoszone wyniki – jedyna bramka punktów w API uczestnika.

    Ten sam sygnał, co zakładka „Wyniki” panelu (``apps.results.services.results_for_participant``
    filtruje po ``stage__results_published_at``) i lista wyników ``GET /api/me/results/``. Znacznik
    na etapie, a nie samo istnienie ``ResultsPublication``, bo to znacznik zdejmuje koordynator,
    wycofując ogłoszenie (``apps.cms.live_data.results_state``) – a wycofane ogłoszenie ma znów
    chować punkty. Etap jest w ``select_related`` każdego querysetu, który tu trafia
    (``submissions_for_user``), więc pytanie nie kosztuje zapytania.
    """
    return submission.entry.stage.results_published_at is not None


class SubmissionFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubmissionFile
        fields = ("original_name", "size_bytes", "sha256", "mime", "av_status")
        read_only_fields = fields


class SubmissionFinalGradeSerializer(serializers.Serializer):
    """Ocena uzgodniona w wersji dla uczestnika: punkty, tryb i – warunkowo – uzasadnienie.

    ``context["published"]`` (domyślnie ``False`` – reguła jest domknięta) mówi, czy etap ma
    ogłoszone wyniki. Przed ogłoszeniem ``score`` i ``decided_at`` są ``None``: data decyzji, która
    przychodzi później niż reszta, mówiłaby „ta ocena się zmieniła”, czyli połowę tego, co liczba.
    """

    score = PointsField(read_only=True, allow_null=True)
    method = serializers.SerializerMethodField()
    decided_at = serializers.DateTimeField(read_only=True, allow_null=True)
    rationale = serializers.SerializerMethodField()

    def to_representation(self, instance) -> dict:
        data = super().to_representation(instance)
        if not self.context.get("published"):
            data["score"] = None
            data["decided_at"] = None
        return data

    def get_method(self, obj) -> str:
        return participant_grade_method(obj.method)

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
    """Reklamacja uczestnika wraz z rozstrzygnięciem, jeśli już zapadło.

    Status i uzasadnienie komisji idą zawsze – tak samo jak w zakładce „Reklamacje” panelu, która
    pokazuje oba od chwili decyzji. Nowa punktacja (``new_score``) dopiero po ogłoszeniu wyników
    etapu (``context["published"]``), z tego samego powodu, co ``final_grade.score``.
    """

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

    def get_new_score(self, obj) -> int | float | None:
        decision = self._decision(obj)
        if decision is None or not self.context.get("published"):
            return None
        return points_json(decision.new_score)

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
        return SubmissionFinalGradeSerializer(grade, context={"published": results_published(obj)}).data

    def get_appeal(self, obj: Submission) -> dict | None:
        appeal = next(iter(obj.appeals.all()), None)
        if appeal is None:
            return None
        return SubmissionAppealSerializer(appeal, context={"published": results_published(obj)}).data


class SubmissionUploadSerializer(serializers.Serializer):
    """Wejście uploadu. Pole jest dokładnie jedno – ``entry`` bierze się z ``request.user``."""

    file = serializers.FileField(write_only=True)


class LockForReviewResultSerializer(serializers.Serializer):
    """Wynik blokady prac do oceny: ile wersji faktycznie przeszło w ``LOCKED``.

    Zero jest poprawną odpowiedzią, a nie błędem – etap zamknięty albo już zablokowany nie ma tu
    nic do zrobienia, a operacja jest idempotentna.
    """

    locked = serializers.IntegerField(read_only=True)


class SubmissionGroupSerializer(serializers.Serializer):
    """Rozwiązania jednego zadania: najnowsza wersja i historia starszych wersji."""

    stage_id = serializers.IntegerField(read_only=True)
    problem_id = serializers.IntegerField(read_only=True)
    problem_number = serializers.IntegerField(read_only=True)
    latest = SubmissionSerializer(read_only=True)
    history = SubmissionSerializer(many=True, read_only=True)
