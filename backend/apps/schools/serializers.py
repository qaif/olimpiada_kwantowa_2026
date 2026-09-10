"""Serializery słownika szkół. Wyłącznie odczyt – rejestru nie zmienia się przez API."""

from rest_framework import serializers

from .models import School


class SchoolSuggestionSerializer(serializers.ModelSerializer):
    """Jedna podpowiedź w wyszukiwarce szkół.

    ``kind_label`` jedzie obok wartości z tego samego powodu, co ``district_label`` przy
    uczestniku: front nie ma utrzymywać własnej kopii słownika typów szkół, a wartość ``kind``
    zostaje stabilna dla klientów API.
    """

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = School
        fields = ("id", "rspo", "name", "kind", "kind_label", "city", "voivodeship")


class SchoolSearchResultsSerializer(serializers.Serializer):
    """Koperta odpowiedzi. Lista pod kluczem, żeby dało się kiedyś dołożyć np. ``truncated``."""

    results = SchoolSuggestionSerializer(many=True, read_only=True)
