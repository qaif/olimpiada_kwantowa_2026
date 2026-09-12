"""Serializery API zawodów – deklaratywne, bez logiki domenowej.

Publiczne zasoby (``editions/current/``) nie zawierają żadnych danych uczestników: wyłącznie
terminy, rodzaje etapów i metadane zadań. Treść zadania jest ujawniana dopiero po otwarciu etapu.
"""

from django.urls import reverse
from rest_framework import serializers

from .models import Edition, Problem, Stage, StageEntry


class PublicStageSerializer(serializers.ModelSerializer):
    """Etap w widoku publicznym: tylko oś czasu, bez danych o uczestnikach i wpisach.

    ``kind`` zostaje na miejscu (klienci filtrują po nim etapy), a obok dochodzą ``name``
    (własna nazwa albo pusty tekst) i ``display_name`` – podpis gotowy do wyświetlenia. Klient,
    który dotąd sam mapował ``kind`` na etykietę, działa bez zmian; nowy bierze ``display_name``
    i widzi nazwę nadaną przez organizatora.
    """

    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Stage
        fields = (
            "id",
            "kind",
            "name",
            "display_name",
            "format",
            "opens_at",
            "deadline_at",
            "grace_seconds",
            # Dni wydarzenia są osobnymi polami, a nie gotowym napisem z zakresem: klient API
            # składa własne zdanie (i własny język), a formatowanie „4–7 czerwca 2027” należy do
            # warstwy widoku. ``null`` dla etapów zdalnych – tam po prostu nie ma zjazdu.
            "event_starts_on",
            "event_ends_on",
            "results_published_at",
        )
        read_only_fields = fields


class PublicProblemSerializer(serializers.ModelSerializer):
    """Zadanie w widoku publicznym. ``statement_pdf`` jest ``None``, dopóki etap się nie otworzy."""

    statement_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Problem
        fields = ("id", "number", "title", "allowed_formats", "max_file_mb", "statement_pdf")
        read_only_fields = fields

    def get_statement_pdf(self, obj: Problem) -> str | None:
        stage = self.context.get("stage") or obj.stage
        if not stage.has_opened(self.context.get("now")):
            return None
        if not obj.statement_pdf:
            return None
        request = self.context.get("request")
        url = reverse("competitions:problem-statement", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(url) if request is not None else url


class RegistrationStatusSerializer(serializers.Serializer):
    """Stan rejestracji uczestników – kształt odpowiedzi dla ``RegistrationStatus``.

    Wyłącznie odczyt: rejestrację otwiera i zamyka koordynator w panelu, nie klient API. ``reason``
    jest kodem maszynowym (``open``/``disabled``/``not_yet``/``closed``), a nie zdaniem po polsku –
    tłumaczenie na komunikat należy do warstwy, która go pokazuje.
    """

    is_open = serializers.BooleanField(read_only=True)
    reason = serializers.CharField(read_only=True)
    opens_at = serializers.DateTimeField(read_only=True, allow_null=True)
    closes_at = serializers.DateTimeField(read_only=True, allow_null=True)


class CurrentEditionSerializer(serializers.ModelSerializer):
    """Bieżąca edycja: wszystkie etapy + zadania etapu bieżącego."""

    stages = PublicStageSerializer(many=True, read_only=True)
    current_stage = serializers.SerializerMethodField()
    problems = serializers.SerializerMethodField()
    # Klient (strona główna, aplikacja mobilna) musi wiedzieć, czy w ogóle pokazywać przycisk
    # rejestracji – i od kiedy. Bez tego pola jedyną drogą byłaby próba założenia konta i odczyt
    # kodu 409, czyli zapytanie o stan przez wywołanie skutku ubocznego.
    registration = serializers.SerializerMethodField()

    class Meta:
        model = Edition
        fields = (
            "id",
            "year_label",
            "is_current",
            "registration",
            "stages",
            "current_stage",
            "problems",
        )
        read_only_fields = fields

    def get_registration(self, obj: Edition) -> dict:
        return RegistrationStatusSerializer(obj.registration_status(self.context.get("now"))).data

    def get_current_stage(self, obj: Edition) -> dict | None:
        stage = self.context.get("stage")
        return PublicStageSerializer(stage, context=self.context).data if stage else None

    def get_problems(self, obj: Edition) -> list[dict]:
        stage = self.context.get("stage")
        if stage is None:
            return []
        problems = stage.problems.all().order_by("number")
        return PublicProblemSerializer(problems, many=True, context=self.context).data


class StageEntrySerializer(serializers.ModelSerializer):
    """Wpis uczestnika do etapu – zwracany wyłącznie właścicielowi (queryset filtruje po roli)."""

    stage = PublicStageSerializer(read_only=True)
    edition = serializers.CharField(source="stage.edition.year_label", read_only=True)
    public_code = serializers.CharField(source="participant.public_code", read_only=True)

    class Meta:
        model = StageEntry
        fields = ("id", "edition", "stage", "public_code", "status", "total_points", "created_at")
        read_only_fields = fields
