"""Serializery słownika szkół. Wyłącznie odczyt – rejestru nie zmienia się przez API."""

from rest_framework import serializers

from apps.accounts.models import Voivodeship

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
    """Koperta odpowiedzi. Lista pod kluczem – dzięki temu zmieściło się obok ``has_more``."""

    results = SchoolSuggestionSerializer(many=True, read_only=True)
    # Czy za tą stroną są jeszcze wiersze. Pełna lista szkół dużego miasta ma ich blisko sto,
    # a klient doczytuje ją przewijaniem – potrzebuje odpowiedzi „czy pytać dalej”, a nie dokładnej
    # liczby (ta kosztowałaby drugie przejście po tym samym zbiorze).
    has_more = serializers.BooleanField(read_only=True)


class CitySuggestionSerializer(serializers.Serializer):
    """Jedna podpowiedź miejscowości: nazwa i województwo.

    Nie jest to ``ModelSerializer``, bo wiersz nie jest szkołą – to wynik ``values().distinct()``.
    Województwo jedzie obok nazwy, bo nazwy miast się powtarzają („Brzeg” jest w opolskiem
    i w dolnośląskiem), a etykieta z diakrytykami pozwala pokazać je człowiekowi bez własnej kopii
    słownika województw po stronie klienta.
    """

    city = serializers.CharField(read_only=True)
    voivodeship = serializers.CharField(read_only=True)
    voivodeship_label = serializers.SerializerMethodField()

    def get_voivodeship_label(self, row) -> str:
        return Voivodeship(row["voivodeship"]).label


class CitySearchResultsSerializer(serializers.Serializer):
    """Koperta odpowiedzi kroku „Miejscowość” – ten sam kształt, co przy szkołach."""

    results = CitySuggestionSerializer(many=True, read_only=True)
