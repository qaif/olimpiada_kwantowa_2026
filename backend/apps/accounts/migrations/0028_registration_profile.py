"""Profil rejestracji konkursu i dwie kolumny uczestnika spoza Polski (§ 1.3.2, § 1.3.4).

**Migracja nie zmienia ani jednego istniejącego wiersza.** Powstaje pusta tabela
``accounts_registrationprofile`` oraz dwie puste kolumny w ``accounts_participant``: ``country``
(ISO 3166-1 alpha-2, pusty znaczy Polska) i ``institution_name`` (nazwa placówki wpisana wolnym
tekstem). Obie są ``blank`` i obie zostają puste – wypełnia je wyłącznie rejestracja konkursu,
który dopuszcza placówki inne niż szkoła ponadpodstawowa.

**Dlaczego nie powstaje wiersz dla Konkursu #1.** Bo brak wiersza i wiersz z samymi wartościami
domyślnymi znaczą dokładnie to samo, a brak jest tańszy i jawniejszy – tak stanowi § 1.3.4 planu
etapu 2 („migracja **nie tworzy** wiersza dla Konkursu #1”). Jedyne wejście do tej konfiguracji,
``apps.accounts.services.registration_profile``, oddaje przy braku wiersza **niezapisany** profil
z domyślnymi, czyli dzisiejsze reguły: szkoła ponadpodstawowa z wykazu SIO albo wpisana ręcznie,
klasa 1–5 wymagana, telefon wymagany, województwo wymagane, rocznik wymagany. Wpisanie wierszy
„na wszelki wypadek” dołożyłoby ``/register/`` zapytanie, którego dziś nie ma (budżet § 5.6),
i zostawiło w bazie konfigurację, której nikt nie ustawiał – a której ręczna zmiana zmieniałaby
formularz Konkursu #1 bez śladu w audycie. Równość „brak wiersza = dzisiejszy formularz”
sprawdza ``apps/accounts/tests/test_registration_profile.py``.

``CreateModel`` i ``AddField`` kolumn z wartością domyślną ``""`` są odwracalne z definicji, więc
``reverse_code`` nie ma czego opisywać.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0027_consent_definition_cascade"),
        # Profil ma własną kolumnę konkursu, więc tabela konkursów musi już stać. Wskazujemy
        # najstarszą migrację, która ją ma – tak samo jak ``0025_region``.
        ("tenancy", "0004_competition_site_alias"),
    ]

    operations = [
        migrations.CreateModel(
            name="RegistrationProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "allowed_institution_types",
                    models.JSONField(blank=True, default=list, verbose_name="dozwolone placówki"),
                ),
                (
                    "allow_custom_directory",
                    models.BooleanField(default=False, verbose_name="słownik organizatora"),
                ),
                (
                    "allow_free_text_school",
                    models.BooleanField(default=True, verbose_name="szkoła spoza wykazu"),
                ),
                (
                    "allow_foreign",
                    models.BooleanField(default=False, verbose_name="uczestnicy spoza Polski"),
                ),
                ("require_grade", models.BooleanField(default=True, verbose_name="klasa wymagana")),
                ("require_phone", models.BooleanField(default=True, verbose_name="telefon wymagany")),
                ("require_region", models.BooleanField(default=True, verbose_name="region wymagany")),
                (
                    "require_birth_year",
                    models.BooleanField(default=True, verbose_name="rocznik wymagany"),
                ),
                (
                    "grade_min",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="klasa od"),
                ),
                (
                    "grade_max",
                    models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="klasa do"),
                ),
                (
                    "participant_picks_category",
                    models.BooleanField(default=False, verbose_name="kategoria z wyboru"),
                ),
                (
                    "competition",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="registration_profile",
                        to="tenancy.competition",
                        verbose_name="konkurs",
                    ),
                ),
            ],
            options={
                "verbose_name": "profil rejestracji",
                "verbose_name_plural": "profile rejestracji",
                "ordering": ("competition",),
            },
        ),
        migrations.AddField(
            model_name="participant",
            name="country",
            field=models.CharField(blank=True, max_length=2, verbose_name="kraj"),
        ),
        migrations.AddField(
            model_name="participant",
            name="institution_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="nazwa placówki"),
        ),
    ]
