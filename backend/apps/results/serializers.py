"""Serializery wyników – deklaratywne, bez logiki domenowej.

Trzy kształty odpowiedzi, trzy różne zakresy danych:

- publiczny (``PublicResultsSerializer``): wyłącznie zamrożony snapshot. Wiersz przechodzi przez
  jawną listę pól, więc nawet snapshot zapisany przez starszą wersję kodu nie wypuści niczego
  poza ``rank``, ``display``, ``district``, ``points``, ``total``, ``qualified``,
- uczestnika (``MyStageResultSerializer``): własne punkty, komentarze dla uczestnika i adnotacje
  publiczne. Nigdy ``comment_internal``, nigdy tożsamości recenzenta,
- koordynatora (``StageResultRowSerializer``): pełna tabela z danymi osobowymi – wyłącznie podgląd
  spod ``IsCoordinator``, nigdy odpowiedź publiczna.
"""

from rest_framework import serializers

from .models import Anonymization, ResultsPublication


class PublicResultRowSerializer(serializers.Serializer):
    """Jeden wiersz ogłoszonej tabeli. Etykieta ``display`` jest już zanonimizowana w snapshocie."""

    rank = serializers.IntegerField(read_only=True)
    display = serializers.CharField(read_only=True)
    district = serializers.CharField(read_only=True, allow_blank=True)
    points = serializers.DictField(child=serializers.IntegerField(), read_only=True)
    total = serializers.IntegerField(read_only=True)
    qualified = serializers.BooleanField(read_only=True)


class PublicResultsSerializer(serializers.ModelSerializer):
    """Publikacja wyników etapu. ``rows`` pochodzą wyłącznie ze snapshotu, nigdy z bazy ocen."""

    stage_id = serializers.IntegerField(read_only=True)
    stage_kind = serializers.CharField(source="stage.kind", read_only=True)
    # Bez ``source``: pole czyta właściwość ``ResultsPublication.rows``, która pilnuje kształtu
    # snapshotu (lista, także dla etapu bez wpisów).
    rows = PublicResultRowSerializer(many=True, read_only=True)

    class Meta:
        model = ResultsPublication
        fields = ("stage_id", "stage_kind", "anonymization", "published_at", "rows")
        read_only_fields = fields


class StageResultRowSerializer(serializers.Serializer):
    """Wiersz podglądu koordynatora: pełne dane potrzebne do weryfikacji tabeli przed publikacją."""

    rank = serializers.IntegerField(read_only=True)
    entry_id = serializers.IntegerField(read_only=True)
    public_code = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True, allow_blank=True)
    last_name = serializers.CharField(read_only=True, allow_blank=True)
    school = serializers.CharField(read_only=True, allow_blank=True)
    district = serializers.CharField(read_only=True, allow_blank=True)
    status = serializers.CharField(read_only=True)
    points = serializers.DictField(child=serializers.IntegerField(), read_only=True)
    total = serializers.IntegerField(read_only=True)


class StageResultsPreviewSerializer(serializers.Serializer):
    """Podgląd tabeli bez publikacji – wynik ``POST stages/{id}/results/compute/``."""

    stage_id = serializers.IntegerField(read_only=True)
    count = serializers.IntegerField(read_only=True)
    rows = StageResultRowSerializer(many=True, read_only=True)


class PublishResultsSerializer(serializers.Serializer):
    """Wejście publikacji. Tryb anonimizacji jest jedynym parametrem decyzji koordynatora."""

    anonymization = serializers.ChoiceField(choices=Anonymization.choices, default=Anonymization.CODE)


class ResultFeedbackSerializer(serializers.Serializer):
    """Informacja zwrotna jednej recenzji – bez ``comment_internal`` i bez autora."""

    comment_for_participant = serializers.CharField(read_only=True, allow_blank=True)
    annotations = serializers.ListField(child=serializers.DictField(), read_only=True)


class MyProblemResultSerializer(serializers.Serializer):
    """Wynik uczestnika na jednym zadaniu."""

    problem_id = serializers.IntegerField(read_only=True)
    number = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    score = serializers.IntegerField(read_only=True)
    feedback = ResultFeedbackSerializer(many=True, read_only=True)


class MyStageResultSerializer(serializers.Serializer):
    """Własne wyniki uczestnika w jednym opublikowanym etapie."""

    stage_id = serializers.IntegerField(read_only=True)
    stage_kind = serializers.CharField(read_only=True)
    edition = serializers.CharField(read_only=True)
    results_published_at = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    qualified = serializers.BooleanField(read_only=True)
    total_points = serializers.IntegerField(read_only=True)
    problems = MyProblemResultSerializer(many=True, read_only=True)
