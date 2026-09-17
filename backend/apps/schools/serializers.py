"""Serializery słownika szkół. Wyłącznie odczyt – rejestru nie zmienia się przez API."""

from rest_framework import serializers

from apps.accounts.models import Voivodeship

from .models import School
from .normalise import city_label


class SchoolSuggestionSerializer(serializers.ModelSerializer):
    """Jedna podpowiedź w wyszukiwarce szkół.

    ``kind_label`` jedzie obok wartości z tego samego powodu, co ``district_label`` przy
    uczestniku: front nie ma utrzymywać własnej kopii słownika typów szkół, a wartość ``kind``
    zostaje stabilna dla klientów API.

    ``city_label`` dochodzi **obok** ``city``, a nie zamiast niego: ``city`` to miejscowość
    z rejestru (adres szkoły – tak jak stoi w wykazie, i taki napis ma prawo trafić do profilu),
    a ``city_label`` to ta sama informacja ułożona pod wybór z listy – „Wrocław (Krzyki)” zamiast
    „Wrocław-Krzyki”. Różnią się tylko w dzielnicach pięciu największych miast; wszędzie indziej
    są tym samym napisem. Pole liczy serwer, bo reguła „co jest gminą, a co dzielnicą” siedzi
    w jednym module (``apps.schools.normalise``) i front nie ma jej powtarzać.
    """

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    city_label = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = (
            "id",
            "rspo",
            "name",
            "kind",
            "kind_label",
            "city",
            "city_parent",
            "city_label",
            "voivodeship",
        )

    def get_city_label(self, school) -> str:
        return city_label(school.city, school.city_parent)


class SchoolSearchResultsSerializer(serializers.Serializer):
    """Koperta odpowiedzi. Lista pod kluczem – dzięki temu zmieściło się obok ``has_more``."""

    results = SchoolSuggestionSerializer(many=True, read_only=True)
    # Czy za tą stroną są jeszcze wiersze. Pełna lista szkół dużego miasta ma ich blisko sto,
    # a klient doczytuje ją przewijaniem – potrzebuje odpowiedzi „czy pytać dalej”, a nie dokładnej
    # liczby (ta kosztowałaby drugie przejście po tym samym zbiorze).
    has_more = serializers.BooleanField(read_only=True)


class CitySuggestionSerializer(serializers.Serializer):
    """Jedna podpowiedź miejscowości: nazwa **gminy** i województwo.

    Nie jest to ``ModelSerializer``, bo wiersz nie jest szkołą – to wynik ``values().distinct()``.
    Województwo jedzie obok nazwy, bo nazwy miast się powtarzają („Brzeg” jest w opolskiem
    i w dolnośląskiem), a etykieta z diakrytykami pozwala pokazać je człowiekowi bez własnej kopii
    słownika województw po stronie klienta.

    Klucz został ``city`` mimo zmiany znaczenia (gmina zamiast miejscowości z wykazu): to ta sama
    odpowiedź na to samo pytanie – „w jakim jesteś mieście” – tylko wreszcie prawdziwa dla
    Warszawy i czterech pozostałych miast rozbitych w wykazie na dzielnice. Osobny klucz dokładał
    drugą nazwę do umowy z klientem, nie dokładając ani jednej informacji.
    """

    city = serializers.CharField(read_only=True)
    voivodeship = serializers.CharField(read_only=True)
    voivodeship_label = serializers.SerializerMethodField()

    def get_voivodeship_label(self, row) -> str:
        return Voivodeship(row["voivodeship"]).label


class CitySearchResultsSerializer(serializers.Serializer):
    """Koperta odpowiedzi kroku „Miejscowość” – ten sam kształt, co przy szkołach."""

    results = CitySuggestionSerializer(many=True, read_only=True)
