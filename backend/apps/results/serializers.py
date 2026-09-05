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
    """Jeden wiersz ogłoszonej tabeli. Etykieta ``display`` jest już zanonimizowana w snapshocie.

    ``district`` jest opcjonalny: snapshot niesie okręg wyłącznie w trybie ``CODE`` (przy inicjałach
    ze szkołą i przy nazwiskach dokładałby cechę quasi-identyfikującą). Brak klucza to pominięte
    pole w odpowiedzi, nie błąd – stąd ``required=False``.
    """

    rank = serializers.IntegerField(read_only=True)
    display = serializers.CharField(read_only=True)
    district = serializers.CharField(read_only=True, required=False, allow_blank=True)
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


class AnnotationSerializer(serializers.Serializer):
    """Adnotacja na pracy: strona, prostokąt i treść.

    Kształt jest jawny, a nie „dowolny słownik”: do uczestnika ma iść wyłącznie to, co narysowano
    i napisano na jego pracy. Flaga ``public`` jest kryterium filtrowania po stronie serwisu, więc
    w odpowiedzi nie ma czego szukać, a przyszłe pola techniczne recenzenta (np. autor adnotacji)
    nie wyciekną tylko dlatego, że ktoś dopisał je do JSON-a.
    """

    page = serializers.IntegerField(read_only=True)
    rect = serializers.ListField(child=serializers.FloatField(), read_only=True)
    text = serializers.CharField(read_only=True, allow_blank=True)


class ResultFeedbackSerializer(serializers.Serializer):
    """Informacja zwrotna jednej recenzji – bez ``comment_internal`` i bez autora."""

    comment_for_participant = serializers.CharField(read_only=True, allow_blank=True)
    annotations = AnnotationSerializer(many=True, read_only=True)


class MyProblemResultSerializer(serializers.Serializer):
    """Wynik uczestnika na jednym zadaniu."""

    problem_id = serializers.IntegerField(read_only=True)
    number = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    score = serializers.IntegerField(read_only=True)
    feedback = ResultFeedbackSerializer(many=True, read_only=True)


class MyStageResultSerializer(serializers.Serializer):
    """Własne wyniki uczestnika w jednym opublikowanym etapie.

    ``total_points`` jest liczone na żywo, ``published_total`` pochodzi z ogłoszonej tabeli, a
    ``differs_from_published`` mówi wprost, że komisja zmieniła ocenę po publikacji.
    """

    stage_id = serializers.IntegerField(read_only=True)
    stage_kind = serializers.CharField(read_only=True)
    edition = serializers.CharField(read_only=True)
    results_published_at = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    qualified = serializers.BooleanField(read_only=True)
    total_points = serializers.IntegerField(read_only=True)
    published_total = serializers.IntegerField(read_only=True, allow_null=True)
    differs_from_published = serializers.BooleanField(read_only=True)
    problems = MyProblemResultSerializer(many=True, read_only=True)
