"""Panel administracyjny słownika szkół.

Wiersze są danymi referencyjnymi wgrywanymi komendą ``seed_schools``, więc admin służy tu do
**oglądania i wygaszania**, a nie do redagowania rejestru: ręczna poprawka nazwy i tak zniknie
przy najbliższym wdrożeniu (upsert po ``rspo`` nadpisuje pola z fixture'u). Jedynym trwałym
wyjątkiem jest dodanie szkoły spoza wykazu – taki wiersz zostanie wygaszony przy pierwszym
przebiegu seeda, dlatego prawa drogą dla szkół spoza rejestru jest wolny tekst w rejestracji.
"""

from django.contrib import admin

from .custom import CustomInstitution
from .models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    # ``city_parent`` obok ``city``, bo w pięciu największych miastach różnią się i to właśnie ta
    # różnica bywa przyczyną zgłoszenia („nie widzę swojej szkoły w Warszawie”): kolumna pokazuje
    # od razu, do jakiej gminy wiersz został przypisany.
    list_display = ("name", "city", "city_parent", "kind", "voivodeship", "is_public", "is_active", "rspo")
    list_filter = ("kind", "voivodeship", "is_active", "is_public")
    search_fields = ("name", "city", "city_parent", "rspo")
    readonly_fields = ("search_text", "source_year")
    ordering = ("name", "id")


@admin.register(CustomInstitution)
class CustomInstitutionAdmin(admin.ModelAdmin):
    """Słownik własny organizatora – narzędzie **operatora platformy**, nie koordynatora.

    Koordynator dostaje własny ekran z importem CSV i wygaszaniem (``/coordinator/institutions/``,
    zadanie T23); tutaj wiersz da się obejrzeć i poprawić po imporcie. Konkurs jest po założeniu
    tylko do odczytu: przeniesienie placówki do innego konkursu zmieniałoby wykaz organizatorowi,
    który o tym nie wie, i to bez śladu w audycie – dokładnie tak samo, jak przy ``Region``.

    Kasowania nie odbieramy, ale i nie zachęcamy do niego: wygaszenie (``is_active``) zostawia
    dowiązania profili nietknięte, a ``Participant.custom_institution_ref`` jest ``PROTECT``, więc
    wiersz wskazany przez uczestnika i tak się nie skasuje.
    """

    list_display = ("name", "city", "institution_type", "competition", "external_id", "is_active")
    list_filter = ("competition", "institution_type", "is_active", "country")
    search_fields = ("name", "city", "external_id")
    readonly_fields = ("search_text", "city_search", "created_at")
    ordering = ("competition", "name", "id")

    def get_readonly_fields(self, request, obj=None):
        return (*self.readonly_fields, "competition") if obj is not None else self.readonly_fields
