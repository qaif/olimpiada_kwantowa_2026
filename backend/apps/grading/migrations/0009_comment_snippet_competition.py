"""Szablony komentarzy dostają konkurs – bo „szablon ogólny” był szablonem całej instalacji.

Model dochodzi do właściciela przez zadanie (``docs/UNIWERSALNY-ETAP-1.md`` § 3.4) i tak zostaje –
dla szablonów **zadania**. Problem dotyczy drugiej postaci tego samego modelu: szablonu ogólnego
(``problem IS NULL``), przygotowanego raz i podpowiadanego przy każdej pracy. Taki wiersz nie ma
zadania, przez które mógłby dojść do konkursu, więc do tej migracji leżał na półce wspólnej dla
całej instalacji – czyli w bazie wielokonkursowej na półce cudzej. Recenzent konkursu B widziałby
w podpowiedziach zdania napisane przez komitet konkursu A.

Kolumna jest więc **dopisaniem drogi tam, gdzie jej nie ma**, a nie drugą drogą do tej samej
prawdy: dla szablonów zadania jej wartość jest równa tej z drogi przez zadanie i pilnuje tego
``CommentSnippet.save()`` (zadanie ma pierwszeństwo przed kontekstem żądania).

Cała zmiana mieści się w jednym pliku i to jest świadome. Wydania A–D rozdzielały schemat od
backfillu, bo dotyczyły kolumn na tabelach produkcyjnych z dziesiątkami tysięcy wierszy, w których
``NOT NULL`` postawione razem z dodaniem kolumny blokowałoby tabelę. Tutaj kolumna **powstaje
w wydaniu D**, tabela jest mała (limit to 50 szablonów na zadanie i 50 na recenzenta), a rozbicie
na cztery migracje w czterech wydaniach oznaczałoby trzy wydania z cudzymi podpowiedziami
w panelu recenzenta.

Kolejność operacji jest kolejnością wydań A–D w miniaturze: kolumna nullowalna, backfill z rodzica,
zapytanie kontrolne (§ 4.4), dopiero potem ``NOT NULL``.
"""

import django.db.models.deletion
from django.db import migrations, models

TABLE = "grading_commentsnippet"


def forwards(apps, schema_editor):
    """Właściciel z zadania, a bez zadania – z komitetu autora albo z jedynego konkursu w bazie.

    Trzy przejścia, w kolejności od najpewniejszego źródła. Zadanie jest faktem o szablonie
    (należy do jednego etapu, a ten do jednej edycji i jednego konkursu). Komitet autora jest
    drugi, bo prywatny szablon ogólny recenzenta nie ma innego wskazania niż ten, kto go napisał.
    Jedyny konkurs w bazie jest ostatni i dotyczy wyłącznie bazy jednokonkursowej – czyli tej,
    która stoi na produkcji w chwili tego wdrożenia (§ 0.1).

    Idempotencja: każdy filtr ma warunek na ``NULL``, więc powtórny przebieg nie nadpisze wartości
    wpisanej w międzyczasie przez kod.
    """
    CommentSnippet = apps.get_model("grading", "CommentSnippet")
    Competition = apps.get_model("tenancy", "Competition")
    # Same klucze, a nie całe wiersze: migracja danych bywa odgrywana na stanie historycznym,
    # w którym tabela konkursów ma mniej kolumn niż dzisiejszy model (``tenancy.0003_prefixes``
    # dokłada prefiksy, a ``apps/tenancy/tests/test_migration_0002.py`` przewija przed nią).
    competition_ids = list(Competition.objects.order_by("pk").values_list("pk", flat=True))
    for competition_id in competition_ids:
        CommentSnippet.objects.filter(
            competition__isnull=True, problem__stage__edition__competition_id=competition_id
        ).update(competition_id=competition_id)
        CommentSnippet.objects.filter(
            competition__isnull=True, owner__competition_id=competition_id
        ).update(competition_id=competition_id)
    if len(competition_ids) == 1:
        CommentSnippet.objects.filter(competition__isnull=True).update(
            competition_id=competition_ids[0]
        )


def backwards(apps, schema_editor):
    """Zdjęcie właścicieli. Kolumnę kasuje dopiero odwrotność ``AddField``, więc ``noop`` nie starczy."""
    CommentSnippet = apps.get_model("grading", "CommentSnippet")
    CommentSnippet.objects.update(competition=None)


def check_no_snippet_without_competition(apps, schema_editor):
    """Zapytanie kontrolne przed ``NOT NULL`` (§ 4.4).

    Niezerowy wynik znaczy tu jedno: w bazie stoi szablon ogólny, którego nie da się przypisać –
    bo konkursów jest kilka, a ten wiersz nie ma ani zadania, ani autora z komitetem. Zgadywanie
    właściciela podłożyłoby komuś cudze zdania pod przyciskiem „wstaw”, więc migracja przerywa
    wdrożenie i zostawia decyzję człowiekowi.
    """
    CommentSnippet = apps.get_model("grading", "CommentSnippet")
    orphans = CommentSnippet.objects.filter(competition__isnull=True).count()
    if orphans:
        raise RuntimeError(
            f"{TABLE}: {orphans} szablonów komentarzy bez konkursu. Wskaż właściciela ręcznie "
            "(szablon ogólny bez zadania i bez autora nie ma go z czego wziąć), zanim powtórzysz "
            "migrację."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("grading", "0008_comment_snippets_worklog_issues"),
        # Backfill idzie przez ``problem → stage → edition → competition``…
        ("competitions", "0021_edition_competition_not_null"),
        # …a dla szablonów bez zadania przez ``owner.competition`` (kolumna z wydania B).
        ("accounts", "0019_memberships_and_competition_fks"),
    ]

    operations = [
        migrations.AddField(
            model_name="commentsnippet",
            name="competition",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="comment_snippets",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
        # ``elidable=False``: jednorazowe przepisanie danych, a nie krok budowy schematu.
        migrations.RunPython(forwards, backwards, elidable=False),
        migrations.RunPython(
            check_no_snippet_without_competition, migrations.RunPython.noop, elidable=False
        ),
        migrations.AlterField(
            model_name="commentsnippet",
            name="competition",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="comment_snippets",
                to="tenancy.competition",
                verbose_name="konkurs",
            ),
        ),
    ]
