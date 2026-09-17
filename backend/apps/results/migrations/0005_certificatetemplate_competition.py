"""Wydanie D: szablon dokumentu dostaje właściciela, od razu na ``NOT NULL``.

``CertificateTemplate`` jest w tej aplikacji jedynym modelem, któremu droga przez edycję nie
wystarcza. ``Certificate`` i ``ResultsPublication`` zawsze mają swoją edycję albo etap, więc
dochodzą do konkursu bez własnej kolumny (§ 3.4). Szablon – nie: jego ``edition`` bywa **puste**
i to jest jego cecha, a nie brak („szablon dla wszystkich edycji”). Taki wiersz nie należał dotąd
do nikogo, czyli po wydaniu C był wspólną półką całej instalacji, a dopasowanie szablonu
(``apps.results.certificates.resolve_template``) kończy się właśnie na nim. Winieta organizatora A
byłaby więc tłem dyplomu organizatora B – bez żadnego kliknięcia, samym trafieniem w odwrót.

Bez etapu przejściowego z ``null=True`` i z tego samego powodu, co w ``integrations.0002``:
kolumna i kod zapisu wchodzą razem, a wiersz bez konkursu ma tu znaczenie, którego nie wolno
dopuścić nawet na jedno wydanie – byłby szablonem wszystkich konkursów naraz.

Kolejność wewnątrz migracji jest ta z § 4.4: ``ADD COLUMN ... NULL`` (nie przepisuje pliku tabeli
w PostgreSQL) → backfill → **zapytanie kontrolne** → ``SET NOT NULL``. Źródłem konkursu jest
``edition.competition``, a dla szablonów „na wszystkie edycje” – jedyny konkurs instalacji.

Zależność od ``tenancy.0003_prefixes`` nie jest potrzebna tej kolumnie, tylko **numeracji**:
od tego wydania numer dokumentu bierze prefiks z ``Competition.certificate_prefix`` (§ 3.3),
a ``apps.results.certificates`` i ta migracja mają wejść do bazy jako jedna zmiana. Bez tej
krawędzi w grafie dałoby się zatrzymać wdrożenie w stanie, w którym kod czyta pole, którego
jeszcze nie ma.
"""

import django.db.models.deletion
from django.db import migrations, models

TABLE = "results_certificatetemplate"


def sole_competition_id(apps):
    """Identyfikator jedynego konkursu instalacji albo ``None``. Więcej niż jeden przerywa wdrożenie.

    Ta sama reguła i to samo zdanie, co w ``cms.0022``, ``competitions.0020``, ``accounts.0020``
    i ``integrations.0002``: przy dwóch konkursach założenie „wszystko tu ma jednego właściciela”
    jest fałszywe, a jego cichym skutkiem byłaby winieta jednego organizatora na dyplomie drugiego.

    Sam **identyfikator**, a nie wiersz: ``values_list("pk")`` wybiera jedną kolumnę, a pobranie
    obiektu wybrałoby wszystkie – łącznie z tymi, których w bazie w danej chwili nie ma. Przy
    cofaniu migracji kolumny dokładane przez ``tenancy`` bywają już zdjęte, choć model historyczny
    wciąż je zna.
    """
    Competition = apps.get_model("tenancy", "Competition")
    rows = list(Competition.objects.order_by("pk").values_list("pk", flat=True)[:2])
    if len(rows) > 1:
        raise RuntimeError(
            "Backfill konkursu działa wyłącznie na bazie jednokonkursowej "
            "(znaleziono więcej niż jeden Competition)."
        )
    return rows[0] if rows else None


def backfill(apps, schema_editor):
    """Konkurs z edycji szablonu, a dla szablonu bez edycji – jedyny konkurs instalacji."""
    CertificateTemplate = apps.get_model("results", "CertificateTemplate")
    rows = CertificateTemplate.objects.filter(competition__isnull=True, edition__isnull=False).values_list(
        "pk", "edition__competition"
    )
    by_competition: dict[int, list[int]] = {}
    for pk, competition_id in rows:
        if competition_id is not None:
            by_competition.setdefault(competition_id, []).append(pk)
    for competition_id, ids in by_competition.items():
        CertificateTemplate.objects.filter(pk__in=ids).update(competition_id=competition_id)
    fallback_id = sole_competition_id(apps)
    if fallback_id is not None:
        CertificateTemplate.objects.filter(competition__isnull=True).update(competition_id=fallback_id)


def undo_backfill(apps, schema_editor):
    """Zeruje kolumnę przed cofnięciem jej na ``null=True`` – patrz odwrotny ``AlterField``."""
    apps.get_model("results", "CertificateTemplate").objects.update(competition=None)


def assert_no_orphans(apps, schema_editor):
    """Zapytanie kontrolne z § 4.4. Choć jeden szablon bez konkursu = przerwane wdrożenie.

    Surowy SQL, a nie ORM: kontrola ma patrzeć na **tabelę**, bo to tabelę za chwilę przestawiamy
    na ``NOT NULL``.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {TABLE} WHERE competition_id IS NULL")
        (orphans,) = cursor.fetchone()
    if orphans:
        raise RuntimeError(
            f"Szablony dokumentów bez konkursu: {orphans}. Wdrożenie przerwane przed zmianą schematu (§ 4.4)."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("results", "0004_certificate_templates_and_workshop_attendance"),
        # Prefiks numeru dokumentu przenosi się do konkursu (§ 3.3) – patrz docstring modułu.
        ("tenancy", "0003_prefixes"),
        # Backfill drogą przez edycję wymaga **wypełnionej** kolumny ``Edition.competition``,
        # a nie samego jej istnienia.
        ("competitions", "0020_backfill_edition_competition"),
    ]

    operations = [
        migrations.AddField(
            model_name="certificatetemplate",
            name="competition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="certificate_templates",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        # ``elidable=False``: jednorazowe przepisanie produkcyjnych danych plus kontrola przed
        # zmianą schematu – ``squashmigrations`` nie ma prawa zwinąć ani jednego z tych kroków.
        migrations.RunPython(backfill, undo_backfill, elidable=False),
        migrations.RunPython(assert_no_orphans, migrations.RunPython.noop, elidable=False),
        migrations.AlterField(
            model_name="certificatetemplate",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="certificate_templates",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
