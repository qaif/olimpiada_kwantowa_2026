"""Serializery rozwiązań – deklaratywne, bez logiki domenowej.

Uczestnik nie dostaje ``object_key``: klucz w prywatnym buckecie jest szczegółem infrastruktury,
a pobranie odbywa się wyłącznie przez ``submissions/{id}/download/`` po sprawdzeniu uprawnień.
"""

from rest_framework import serializers

from .models import Submission, SubmissionFile


class SubmissionFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubmissionFile
        fields = ("original_name", "size_bytes", "sha256", "mime", "av_status")
        read_only_fields = fields


class SubmissionSerializer(serializers.ModelSerializer):
    """Zgłoszenie wraz z metadanymi pliku – kształt odpowiedzi ``POST .../submissions/``."""

    file = SubmissionFileSerializer(source="latest_file", read_only=True)
    problem_number = serializers.IntegerField(source="problem.number", read_only=True)
    stage_id = serializers.IntegerField(source="entry.stage_id", read_only=True)

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
        )
        read_only_fields = fields


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
