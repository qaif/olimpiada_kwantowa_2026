"""Backfill wydania B: każda praca dostaje konkurs **swojego etapu**, a nie „ten jedyny”.

Różnica wobec ``competitions.0020`` jest zamierzona i istotna. Tamta migracja przypisuje edycje do
jedynego konkursu w bazie, bo edycja jest korzeniem własności. Tutaj kolumna jest **denormalizacją**
(``docs/UNIWERSALNY-ETAP-1.md`` § 3.4), więc jej wartość nie jest decyzją – jest funkcją
``entry.stage.edition.competition``. Przepisanie jej z „jedynego konkursu” dałoby ten sam wynik na
dzisiejszej produkcji, ale utrwaliłoby złą regułę: dzień po dołożeniu drugiego konkursu ponowny
przebieg przypisywałby cudze prace.

Dlatego backfill idzie **z rodzica**: jeden ``UPDATE`` na konkurs, wybierający prace po ścieżce
przez wpis i etap. Przy jednym konkursie jest to dokładnie jeden ``UPDATE`` – tyle, ile w § 4.2.

Idempotencja: warunek na ``NULL`` (wydanie B stoi obok kodu, który kolumnę już wypełnia).
Odwracalność: zdjęcie właściciela z prac, których konkurs zgadza się z przypisanym – kolumna
zostaje na miejscu, więc ``noop`` zostawiłby dane, których ponowny przebieg by nie ruszył.
"""

from django.db import migrations


def forwards(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    Submission = apps.get_model("submissions", "Submission")
    for competition in Competition.objects.order_by("pk"):
        Submission.objects.filter(
            competition__isnull=True,
            entry__stage__edition__competition=competition,
        ).update(competition=competition)


def backwards(apps, schema_editor):
    Submission = apps.get_model("submissions", "Submission")
    # ``F``-porównania tu nie ma z rozmysłem: cofamy wyłącznie to, co postawiliśmy – czyli wartość
    # zgodną z drogą przez rodzica. Praca z konkursem **innym** niż jej etap jest rozjazdem, który
    # ma zostać w bazie widoczny, a nie zniknąć przy cofaniu migracji.
    Competition = apps.get_model("tenancy", "Competition")
    for competition in Competition.objects.order_by("pk"):
        Submission.objects.filter(
            competition=competition,
            entry__stage__edition__competition=competition,
        ).update(competition=None)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0005_submission_competition"),
        # Prace dochodzą do konkursu przez edycję, więc jej właściciel musi już być wpisany.
        ("competitions", "0020_backfill_edition_competition"),
    ]

    operations = [
        # ``elidable=False``: jednorazowe przepisanie danych produkcyjnych, nie krok budowy schematu.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
