"""Ręczne unieważnienie cache'a całych stron publicznych (``apps/web/page_cache.py``).

Po co osobna komenda, skoro cache i tak wygasa po 120 sekundach i sam się czyści po publikacji
strony, zapisie ustawień czy komunikacie organizatora (patrz odbiorniki sygnałów w tamtym module):
operator platformy czasem musi zgasić bufor **natychmiast** i **wszędzie** – po awaryjnej migracji
danych z ominięciem sygnałów Django (import hurtowy, naprawa w powłoce), albo po prostu na wszelki
wypadek przy diagnozie „czemu strona pokazuje stare dane”. Komenda nie zna żadnej logiki poza samym
``INCR`` globalnej wersji – dokładnie to, co robi ``invalidate_all()``, którego woła każdy inny
odbiornik tego modułu.
"""

from django.core.management.base import BaseCommand

from apps.web.page_cache import invalidate_all


class Command(BaseCommand):
    help = "Unieważnia cache całych stron publicznych (wszystkie witryny naraz)."

    def handle(self, *args, **options):
        invalidate_all()
        self.stdout.write(self.style.SUCCESS("Cache stron publicznych unieważniony."))
