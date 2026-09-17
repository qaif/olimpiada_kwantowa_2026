"""Wydanie D: klucz API i odbiorca webhooków dostają właściciela, od razu na ``NOT NULL``.

Dlaczego bez etapu przejściowego z ``null=True``, który dostały modele w wydaniu B. Bo tam
kolumna powstawała **przed** kodem, który ją wypełnia, i przez jedno wydanie stary kod zapisu stał
obok nowego (§ 4.1). Tutaj kolumna i kod zapisu wchodzą razem, a wiersz bez konkursu ma tu
znaczenie, którego nie wolno dopuścić nawet na jedno wydanie:

- klucz API bez konkursu jest **poświadczeniem o nieokreślonym zakresie danych** – a otwiera dane
  osobowe uczestników,
- odbiorca webhooków bez konkursu dostawałby zdarzenia **wszystkich** konkursów, czyli wysyłałby
  zdarzenia organizatora A na serwer wskazany przez organizatora B.

Kolejność w jednej migracji jest więc taka, jak opisuje § 4.4: ``ADD COLUMN ... NULL`` (nie
przepisuje pliku tabeli w PostgreSQL) → backfill → **zapytanie kontrolne** → ``SET NOT NULL``.
Niezerowy wynik kontroli przerywa wdrożenie ``RuntimeError``-em **przed** zmianą schematu; kopia
z § 0.2 leży obok.

Skąd bierze się konkurs przy backfillu: z ``edition.competition``, gdy klucz albo odbiorca ma
przypisaną edycję, a w pozostałych wypadkach („wszystkie edycje”) z jedynego konkursu instalacji.
W bazie jednokonkursowej obie drogi prowadzą do tego samego wiersza – druga jest tu dlatego, że
klucz bez edycji pierwszej drogi nie ma.

``WebhookDelivery`` własnej kolumny **nie** dostaje: dochodzi do konkursu przez swojego odbiorcę
(§ 3.1). Druga droga do tej samej prawdy to druga okazja do rozjazdu, a rozjazd w tabeli izolacji
znaczy wyciek.
"""

import django.db.models.deletion
from django.db import migrations, models

#: Tabele domykane tą migracją: ``(nazwa modelu, nazwa tabeli)``. Nazwa tabeli jest potrzebna
#: zapytaniu kontrolnemu, które – zgodnie z § 4.4 – idzie surowym SQL-em.
CLOSED = (
    ("apikey", "integrations_apikey"),
    ("webhookendpoint", "integrations_webhookendpoint"),
)


def sole_competition_id(apps):
    """Identyfikator jedynego konkursu instalacji albo ``None``. Więcej niż jeden przerywa wdrożenie.

    Ta sama reguła i to samo zdanie, co w ``cms.0022``, ``competitions.0020`` i ``accounts.0020``.
    Przy dwóch konkursach założenie „wszystko, co tu stoi, ma jednego właściciela” jest fałszywe,
    a jego cichym skutkiem byłby klucz partnera przepisany cudzemu organizatorowi.

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
    """Wypełnia kolumnę: najpierw drogą przez edycję, potem jedynym konkursem instalacji."""
    fallback_id = sole_competition_id(apps)
    for model_name, _table in CLOSED:
        model = apps.get_model("integrations", model_name)
        rows = model.objects.filter(competition__isnull=True, edition__isnull=False).values_list(
            "pk", "edition__competition"
        )
        by_competition: dict[int, list[int]] = {}
        for pk, competition_id in rows:
            if competition_id is not None:
                by_competition.setdefault(competition_id, []).append(pk)
        for competition_id, ids in by_competition.items():
            model.objects.filter(pk__in=ids).update(competition_id=competition_id)
        if fallback_id is not None:
            model.objects.filter(competition__isnull=True).update(competition_id=fallback_id)


def undo_backfill(apps, schema_editor):
    """Zeruje kolumnę przed cofnięciem jej na ``null=True`` – patrz ``AlterField`` odwrotny."""
    for model_name, _table in CLOSED:
        apps.get_model("integrations", model_name).objects.update(competition=None)


def assert_no_orphans(apps, schema_editor):
    """Zapytanie kontrolne z § 4.4. Choć jeden wiersz bez konkursu = przerwane wdrożenie.

    Surowy SQL, a nie ORM, i to jest ta sama decyzja, co w dokumencie: kontrola ma patrzeć na
    **tabelę**, a nie na model historyczny, bo to tabelę za chwilę przestawiamy na ``NOT NULL``.
    Jedno zdanie ``UNION ALL`` na wszystkie tabele, żeby raport wymieniał je wszystkie naraz –
    wdrożenie przerwane dwa razy pod rząd na dwóch różnych tabelach jest wdrożeniem przerwanym
    dwa razy niepotrzebnie.
    """
    statement = "\nUNION ALL ".join(
        f"SELECT '{table}' AS t, count(*) FROM {table} WHERE competition_id IS NULL"
        for _model_name, table in CLOSED
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(statement)
        orphans = [(table, count) for table, count in cursor.fetchall() if count]
    if orphans:
        raise RuntimeError(
            "Wiersze bez konkursu przed domknięciem kolumny na NOT NULL: "
            + ", ".join(f"{table}: {count}" for table, count in orphans)
            + ". Wdrożenie przerwane przed zmianą schematu (§ 4.4)."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0001_initial"),
        ("tenancy", "0002_competition_from_site"),
        # Backfill drogą przez edycję wymaga **wypełnionej** kolumny ``Edition.competition``,
        # a nie samego jej istnienia.
        ("competitions", "0020_backfill_edition_competition"),
    ]

    operations = [
        migrations.AddField(
            model_name="apikey",
            name="competition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="api_keys",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AddField(
            model_name="webhookendpoint",
            name="competition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="webhook_endpoints",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        # ``elidable=False``: jednorazowe przepisanie produkcyjnych danych plus kontrola przed
        # zmianą schematu – ``squashmigrations`` nie ma prawa zwinąć ani jednego z tych kroków.
        migrations.RunPython(backfill, undo_backfill, elidable=False),
        migrations.RunPython(assert_no_orphans, migrations.RunPython.noop, elidable=False),
        migrations.AlterField(
            model_name="apikey",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="api_keys",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        migrations.AlterField(
            model_name="webhookendpoint",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="webhook_endpoints",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
