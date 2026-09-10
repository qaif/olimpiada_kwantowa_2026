"""Panel administracyjny słownika szkół.

Wiersze są danymi referencyjnymi wgrywanymi komendą ``seed_schools``, więc admin służy tu do
**oglądania i wygaszania**, a nie do redagowania rejestru: ręczna poprawka nazwy i tak zniknie
przy najbliższym wdrożeniu (upsert po ``rspo`` nadpisuje pola z fixture'u). Jedynym trwałym
wyjątkiem jest dodanie szkoły spoza wykazu – taki wiersz zostanie wygaszony przy pierwszym
przebiegu seeda, dlatego prawa drogą dla szkół spoza rejestru jest wolny tekst w rejestracji.
"""

from django.contrib import admin

from .models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "kind", "voivodeship", "is_public", "is_active", "rspo")
    list_filter = ("kind", "voivodeship", "is_active", "is_public")
    search_fields = ("name", "city", "rspo")
    readonly_fields = ("search_text", "source_year")
    ordering = ("name", "id")
