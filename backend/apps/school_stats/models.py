"""Zamrożona przynależność wpisów do grup w chwili publikacji wyników (STAT-01 § 2a, odstępstwo M3).

Po co, skoro szkoła i województwo są w profilu uczestnika: profil się zmienia. Uczeń zmienia szkołę,
koordynator poprawia dowiązanie do wykazu, konto bywa anonimizowane (szkoła znika). Agregat liczony
z **bieżącego** profilu zmieniałby się wtedy po ogłoszeniu wyników – średnia szkoły z eliminacji
2025 przesuwałaby się od zmian dokonanych w 2027 – a każda taka zmiana byłaby dla opiekuna okazją
do odjęcia „przed” od „po” i odczytania wyniku jednej osoby. Zamrożenie robi z agregatu etapu to,
czym jest ``ResultsPublication.entry_totals`` dla punktów: stan z chwili ogłoszenia.

Dlaczego osobna tabela w tej aplikacji, a nie kolumna w publikacji: aplikacja wyników nie wie
o statystykach szkół i nie powinna (równoległe zmiany w ``apps.results`` mają własne migracje).
Wiersze powstają w odbiorniku sygnału zapisu publikacji (``apps.school_stats.signals``), a dla etapów
ogłoszonych przed wdrożeniem – leniwie, przy pierwszym liczeniu agregatu (``services.build_summary``).

Co tu **nie** jest zapisane: osoba. Wiersz ma identyfikator wpisu (ten sam klucz, co w
``entry_totals``), klucz i podpis grupy (nazwa szkoły), kod regionu i trzy flagi. Po anonimizacji
konta wiersz zostaje i liczy się dalej **wyłącznie** do agregatów – żaden ekran nie pokazuje
pojedynczego wiersza, a ranking i liczby uczestników biorą się z wpisów żywych, bez kont usuniętych.
"""

from django.db import models
from django.utils import timezone

from apps.competitions.models import Stage


class FrozenMembership(models.Model):
    """Przynależność jednego wpisu etapu do grupy i regionu – stan z chwili publikacji wyników."""

    #: Oś grupowania (``apps.school_stats.grouping.GroupAxis.kind``) – dwie osie nie dzielą wierszy.
    axis = models.CharField("oś", max_length=16, default="school")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="+", verbose_name="etap")
    #: Identyfikator wpisu, a nie klucz obcy: wiersz ma przetrwać wszystko, co przetrwa ogłoszona
    #: suma w ``entry_totals`` (też trzymana po samym identyfikatorze), i nie blokować usunięć.
    entry_pk = models.BigIntegerField("wpis")
    #: Pusty klucz = wpis bez grupy (szkoła nieznana albo konto już anonimizowane w chwili zamrożenia).
    group_key = models.CharField("klucz grupy", max_length=300, blank=True)
    group_label = models.CharField("podpis grupy", max_length=255, blank=True)
    group_city = models.CharField("miejscowość grupy", max_length=120, blank=True)
    school_id = models.IntegerField("szkoła z wykazu", null=True, blank=True)
    rspo = models.IntegerField("RSPO", null=True, blank=True)
    region = models.CharField("region", max_length=100, blank=True)
    qualified = models.BooleanField("zakwalifikowany", default=False)
    has_submission = models.BooleanField("praca oddana", default=False)
    has_late = models.BooleanField("praca po terminie", default=False)
    frozen_at = models.DateTimeField("zamrożono", default=timezone.now)

    class Meta:
        verbose_name = "zamrożona przynależność wpisu"
        verbose_name_plural = "zamrożone przynależności wpisów"
        constraints = [
            # Leniwe zamrożenie z dwóch równoległych żądań wstawia te same wiersze – ``ignore_conflicts``
            # przy tym ograniczeniu robi z tego operację idempotentną.
            models.UniqueConstraint(fields=["axis", "stage", "entry_pk"], name="school_stats_frozen_unique"),
        ]
        indexes = [
            # Odczyt wierszy własnych uczniów opiekuna: ``entry_pk IN (...)``.
            models.Index(fields=["entry_pk"], name="school_stats_frozen_entry_idx"),
        ]

    def __str__(self) -> str:
        return f"wpis {self.entry_pk} w etapie {self.stage_id}: {self.group_key or '—'}"
