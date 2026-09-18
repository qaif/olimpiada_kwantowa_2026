"""Serializery słownika szkół. Wyłącznie odczyt – rejestru nie zmienia się przez API.

**Dwa wykazy, dwa kształty wiersza.** Od etapu 2 (§ 1.3.3) ta sama wyszukiwarka odpowiada
z dwóch tabel: publicznego wykazu SIO (``School``) i słownika wgranego przez organizatora
(``schools.custom.CustomInstitution``). Serializery „łączone” (``Merged…``) dopisują wiersz
rozróżniający – i **wyłącznie** one; klasy podstawowe zostają co do klucza takie, jakie były,
bo konkurs bez własnego słownika ma dostawać odpowiedź bajt w bajt dzisiejszą (§ 0.1).
"""

from rest_framework import serializers

from apps.accounts.models import Voivodeship

from .custom import CustomInstitution
from .models import School
from .normalise import city_label

#: Wartości pola ``source`` w odpowiedzi łączącej oba wykazy. Napis, a nie ``bool``: klient
#: wybiera po nim **kolumnę**, do której zapisuje dowiązanie (``school_id`` albo
#: ``custom_institution_id``, § 1.3.3), a nie „tak/nie”. Tych samych dwóch napisów używa
#: ``static/js/school-picker.js``.
SOURCE_DIRECTORY = "sio"
SOURCE_CUSTOM = "custom"


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


class MergedSchoolSuggestionSerializer(SchoolSuggestionSerializer):
    """Ta sama podpowiedź, z dwoma kluczami dopisanymi **na końcu** – tylko przy dwóch wykazach.

    Podklasa, a nie dwa warunki w klasie wyżej: odpowiedź konkursu bez własnego słownika ma mieć
    dokładnie te klucze i w tej kolejności, co przed etapem 2, a najpewniejszym sposobem, żeby
    tego dowieść, jest **nie dotykać** klasy, która ją składa.

    - ``source`` mówi, z którego wykazu jest wiersz (``sio``),
    - ``school_id`` powtarza ``id`` **nazwą kolumny**, do której formularz zapisuje dowiązanie.
      Powtórzenie jest tu treścią, a nie nadmiarem: wiersz z drugiego wykazu niesie
      ``custom_institution_id``, więc klient czyta zawsze ten sam klucz, co pole, które wypełnia,
      i nie musi trzymać własnego odwzorowania „źródło → kolumna”. ``id`` zostaje, bo jest
      w umowie z klientami API od pierwszego wydania wyszukiwarki.
    """

    school_id = serializers.IntegerField(source="id", read_only=True)
    source = serializers.SerializerMethodField()

    class Meta(SchoolSuggestionSerializer.Meta):
        fields = (*SchoolSuggestionSerializer.Meta.fields, "school_id", "source")

    def get_source(self, school) -> str:
        return SOURCE_DIRECTORY


class CustomInstitutionSuggestionSerializer(serializers.ModelSerializer):
    """Jedna podpowiedź ze słownika organizatora – druga połowa odpowiedzi wyszukiwarki.

    Kształt jest **równoległy** do podpowiedzi z wykazu publicznego, a nie identyczny. Klucze
    wspólne (``id``, ``name``, ``city``, ``city_label``) znaczą to samo i front rysuje z nich ten
    sam wiersz; kluczy, których ta tabela nie ma (``rspo``, ``kind``), nie ma też w odpowiedzi –
    pusty numer RSPO przy placówce organizatora byłby zdaniem o rejestrze ministerialnym,
    którego ta placówka nie dotyczy. Zamiast ``kind_label`` (typ szkoły z wykazu SIO) jedzie
    ``institution_type_label``, czyli etykieta tego, co w tym wierszu naprawdę wiadomo.

    ``region_code`` i ``country`` jadą, bo to jest wszystko, co wiersz mówi o położeniu placówki –
    województwa ta tabela nie zna (§ 1.3.3: region jest kodem podziału konkursu, a nie kolumną
    z listy szesnastu).
    """

    institution_type_label = serializers.CharField(source="get_institution_type_display", read_only=True)
    city_label = serializers.SerializerMethodField()
    custom_institution_id = serializers.IntegerField(source="id", read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = CustomInstitution
        fields = (
            "id",
            "name",
            "institution_type",
            "institution_type_label",
            "city",
            "city_label",
            "region_code",
            "country",
            "custom_institution_id",
            "source",
        )

    def get_city_label(self, institution) -> str:
        """Ta sama rola, co przy szkole z wykazu: napis ułożony pod wybór z listy.

        Dzielnic ta tabela nie zna – rozbicie pięciu największych miast jest własnością wykazu
        SIO (``School.city_parent``) – więc etykietą jest po prostu miejscowość. Klucz zostaje,
        żeby front miał **jedną** ścieżkę odczytu dla obu wykazów.
        """
        return institution.city

    def get_source(self, institution) -> str:
        return SOURCE_CUSTOM


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
        """Etykieta województwa albo pusty napis, gdy wiersz województwa nie ma.

        Pusta wartość ma dokładnie jedno źródło i jest nim słownik organizatora: ``School``
        trzyma województwo w kolumnie ``NOT NULL`` z listy szesnastu, a ``CustomInstitution``
        opisuje położenie **kodem regionu konkursu** (§ 1.3.3) i o województwie nic nie wie.
        ``Voivodeship("")`` byłoby tam ``ValueError``, czyli pięćsetką w podpowiedziach
        miejscowości – a odpowiedzią na „nie wiadomo” jest pusty napis, nie awaria.
        """
        value = row["voivodeship"]
        return Voivodeship(value).label if value else ""


class MergedCitySuggestionSerializer(CitySuggestionSerializer):
    """Podpowiedź miejscowości z dopisanym źródłem – tylko wtedy, gdy wykazy są dwa.

    Podklasa z tego samego powodu, co przy szkołach: konkurs bez własnego słownika ma dostać
    odpowiedź co do klucza dzisiejszą. Źródło czytamy z wiersza, bo obie połowy listy są tu
    słownikami (``values().distinct()``), a nie obiektami dwóch modeli.
    """

    source = serializers.SerializerMethodField()

    def get_source(self, row) -> str:
        return row.get("source") or SOURCE_DIRECTORY


class CitySearchResultsSerializer(serializers.Serializer):
    """Koperta odpowiedzi kroku „Miejscowość” – ten sam kształt, co przy szkołach."""

    results = CitySuggestionSerializer(many=True, read_only=True)
