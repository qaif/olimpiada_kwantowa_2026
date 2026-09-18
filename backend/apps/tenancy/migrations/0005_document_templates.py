"""Szablony tekstu dokumentów (§ 1.1.3) i dzisiejsze napisy Konkursu #1 jako wiersze.

Migracja robi dwie rzeczy i obie są z tej samej reguły etapu 2 § 0.1 (2): dane, które dotąd były
stałą w kodzie, wchodzą do bazy migracją wpisującą Konkursowi #1 **dokładnie dzisiejszą wartość** –
co do znaku, wraz z wersją dokumentu i wielkością liter.

Napisy nie są tu przepisane literałem, tylko **zaimportowane** z ``apps/results/certificates.py``
(``DOCUMENT_TITLES``, ``DOCUMENT_STATEMENTS``, ``SIGNATURE_LINE``) – tak samo jak przy zgodach
i z tego samego powodu: dwie kopie tego samego zdania rozjechałyby się przy pierwszej poprawce,
a objawem byłby dyplom, który po włączeniu flagi mówi coś innego niż dyplom sprzed włączenia.
Tego, że obie strony są równe, pilnuje ``test_document_templates_match_the_constants``.

**Wiersze dostaje wyłącznie konkurs o najniższym ``pk``**, czyli ten, który założyła migracja
``tenancy.0002_competition_from_site`` z witryny Wagtaila. Zdania mówią „Olimpiady Kwantowej”
w dopełniaczu, więc wpisanie ich każdemu konkursowi w bazie byłoby wpisaniem cudzej marki do
konfiguracji drugiego organizatora – a konkurs założony po tym wydaniu dostaje swoje teksty
z panelu (``apps.tenancy.documents.set_current_template``), nie z migracji.

**Nic z tego jeszcze nie działa.** Flaga ``document_templates`` jest domyślnie wyłączona, a przy
wyłączonej fladze ``apps.tenancy.documents`` nie pyta tej tabeli ani razu – dokument Olimpiady
Kwantowej składa się z tych samych stałych, co przed tą migracją.

Idempotencja: rodzaj, który ma już w tym konkursie jakikolwiek wiersz, jest pomijany. Powtórzony
przebieg niczego nie duplikuje i – co ważniejsze – nie nadpisuje wersji ustawionej w panelu.
"""

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def forwards(apps_registry, schema_editor):
    """Pięć rodzajów dokumentów Konkursu #1 z dzisiejszymi napisami i wersją początkową."""
    from apps.results.certificates import DOCUMENT_STATEMENTS, DOCUMENT_TITLES, SIGNATURE_LINE
    from apps.tenancy.documents import INITIAL_VERSION

    Competition = apps_registry.get_model("tenancy", "Competition")
    DocumentTemplate = apps_registry.get_model("tenancy", "DocumentTemplate")

    # Tylko klucz główny, a nie cały wiersz: ta sama ostrożność, co w backfillach § 3.2 – zapytanie
    # o komplet kolumn przewraca się przy cofaniu migracji na bazie, w której ``Competition`` ma
    # już inny kształt niż w chwili pisania tego pliku.
    first = Competition.objects.order_by("pk").values_list("pk", flat=True).first()
    if first is None:
        # Instalacja bez ani jednego konkursu (baza sprzed ``tenancy.0002``, test transakcyjny po
        # ``flush``). Nie ma komu wpisać dokumentów i nie ma czego zgadywać.
        return

    for kind, title in DOCUMENT_TITLES.items():
        key = str(kind)
        if DocumentTemplate.objects.filter(competition_id=first, kind=key).exists():
            continue
        DocumentTemplate.objects.create(
            competition_id=first,
            kind=key,
            version=INITIAL_VERSION,
            title=title,
            statement=DOCUMENT_STATEMENTS[kind],
            signature_line=SIGNATURE_LINE,
            footer_note="",
            is_current=True,
            created_at=timezone.now(),
        )


def backwards(apps_registry, schema_editor):
    """Pusto z premedytacją: cofnięcie tej migracji kasuje **całą tabelę** krok niżej.

    Kasowanie wierszy osobno znaczyłoby tu tylko tyle, że trzeba zgadnąć, które z nich są z tej
    migracji, a które dopisał koordynator w panelu – a zaraz potem i tak znika tabela.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(
                    choices=[
                        ("LAUREAT", "dyplom laureata"),
                        ("FINALISTA", "dyplom finalisty"),
                        ("UCZESTNIK", "zaświadczenie uczestnika"),
                        ("OPIEKUN", "zaświadczenie opiekuna"),
                        ("WARSZTATY", "zaświadczenie z warsztatów"),
                        ("GUARDIAN_FORM", "wzór zgody opiekuna"),
                        ("INVOICE", "faktura / rachunek"),
                        ("ATTENDANCE_LIST", "lista obecności"),
                    ],
                    max_length=24,
                    verbose_name="rodzaj",
                )),
                ("version", models.CharField(max_length=100, verbose_name="wersja")),
                ("title", models.CharField(max_length=200, verbose_name="tytuł na dokumencie")),
                ("statement", models.TextField(verbose_name="zdanie główne")),
                ("signature_line", models.CharField(blank=True, max_length=200, verbose_name="linia podpisu")),
                ("footer_note", models.CharField(blank=True, max_length=300, verbose_name="dopisek w stopce")),
                ("is_current", models.BooleanField(default=True, verbose_name="obowiązująca")),
                ("created_at", models.DateTimeField(default=timezone.now, verbose_name="utworzona")),
                # ``CASCADE``, a nie ``PROTECT`` z § 1.1.3: szablon jest konfiguracją konkursu,
                # a nie dowodem – uzasadnienie przy polu w ``apps/tenancy/documents.py``.
                ("competition", models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name="document_templates",
                    to="tenancy.competition",
                    verbose_name="konkurs",
                )),
            ],
            options={
                "verbose_name": "szablon dokumentu",
                "verbose_name_plural": "szablony dokumentów",
                "ordering": ("competition", "kind", "-created_at", "-id"),
            },
        ),
        migrations.AddConstraint(
            model_name="documenttemplate",
            constraint=models.UniqueConstraint(
                condition=Q(is_current=True),
                fields=("competition", "kind"),
                name="tenancy_documenttemplate_single_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="documenttemplate",
            constraint=models.UniqueConstraint(
                fields=("competition", "kind", "version"),
                name="tenancy_documenttemplate_version_per_kind",
            ),
        ),
        migrations.AddConstraint(
            model_name="documenttemplate",
            constraint=models.CheckConstraint(
                condition=~Q(version=""),
                name="tenancy_documenttemplate_version_not_empty",
            ),
        ),
        # ``elidable=False``: jednorazowe przepisanie dzisiejszych napisów Konkursu #1 na wiersze,
        # a nie krok budowy schematu – ``squashmigrations`` nie ma prawa go zwinąć.
        migrations.RunPython(forwards, backwards, elidable=False),
    ]
