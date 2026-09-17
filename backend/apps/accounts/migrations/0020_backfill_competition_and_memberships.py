"""Backfill wydania B: wszystko, co jest w bazie, należy do Konkursu #1; grupy → członkostwa.

Dwie rzeczy naraz, bo są jedną decyzją: „ta instalacja miała dotąd jeden konkurs, więc wszystko,
co w niej stoi, jest jego”. Rozdzielenie ich na dwie migracje dałoby stan pośredni, w którym
profile mają już właściciela, a role jeszcze nie – i odpowiedź na pytanie „czy ta osoba jest tu
koordynatorem” zależałaby od tego, która migracja zdążyła się wykonać.

**Profile.** ``UPDATE ... SET competition_id = <pk Konkursu #1>`` bez ``WHERE`` po stronie
własności – w bazie jednokonkursowej nie ma innego właściciela (``docs/UNIWERSALNY-ETAP-1.md``
§ 0.1). Warunek jest wyłącznie na ``NULL``, żeby powtórzony przebieg nie nadpisał wartości
wpisanej w międzyczasie przez kod (wydania B i C stoją obok siebie).

**Role.** ``auth_user_groups`` → ``accounts_membership``, jeden ``INSERT ... SELECT`` na rolę.
Mapowania nazw tu nie ma i nie miało być: wartości ``CompetitionRole`` są **identyczne** z nazwami
grup RBAC (``GROUP_*``), więc backfill jest przepisaniem kolumny. Gdyby kiedyś się rozeszły,
zatrzyma to asercja w ``apps/accounts/models.py``, a nie cicho pominięta rola.

Czego ta migracja **nie** robi: nie kasuje ani jednej grupy Django i nie rusza ich uprawnień.
Grupa ``coordinator`` niesie komplet uprawnień do ``/cms/`` (migracja ``cms.0003``) i pozostaje
jedynym źródłem dostępu do panelu redakcyjnego; członkostwo jest rolą w konkursie, nie
uprawnieniem Wagtaila. Nie przestawia też flagi ``memberships_enforced`` – autoryzacja po
członkostwach włącza się świadomą decyzją w wydaniu C, już po sprawdzeniu, że tabela się zgadza.

**Idempotencja i odwracalność.** Powtórzony przebieg nie dokłada wierszy (``NULL``-owy warunek przy
profilach, ``exclude`` istniejących przy członkostwach). Cofnięcie zdejmuje właścicieli i kasuje
członkostwa Konkursu #1 – czyli wraca do stanu, w którym rolę niosą wyłącznie grupy. Nie jest to
``noop``: kolumny zostają na miejscu, więc bez jawnej odwrotności cofnięta migracja zostawiłaby
dane, których nowy przebieg by nie ruszył.
"""

from django.db import migrations

#: Modele kont, które w wydaniu B dostają właściciela. Tabela zamiast pięciu wywołań, bo to jest
#: **lista**, a nie logika – i ma się dać porównać wzrokiem z tabelą w § 3.2 dokumentu.
SCOPED_MODELS = (
    "Participant",
    "CommitteeMember",
    "SchoolSupervisor",
    "InvitationCode",
    "MessageBroadcast",
)

#: Nazwy grup RBAC = wartości ``CompetitionRole``. Powtórzone tutaj jako literały, bo migracja
#: nie ma prawa importować ``apps.accounts.models``: ta stała może się jutro zmienić, a migracja
#: opisuje stan bazy z dnia, w którym powstała.
ROLES = ("participant", "reviewer", "appeals", "coordinator", "supervisor")


def sole_competition(apps):
    """Konkurs, do którego należy cała zastana baza – albo ``None``, gdy nie ma go z czego wziąć.

    ``None`` znaczy „świeża instalacja bez drzewa stron” (``tenancy.0002`` nie miała z czego
    utworzyć konkursu) i jest poprawną odpowiedzią: pusta baza nie ma czego backfillować.

    Więcej niż jeden konkurs przerywa wdrożenie. Ta migracja opiera się w całości na założeniu
    „wszystko, co tu stoi, ma jednego właściciela”; przy dwóch konkursach założenie jest fałszywe,
    a jego cichy skutek to przepisanie danych organizatora A na organizatora B.

    Zwracamy **klucz główny**, nie obiekt, i pytamy wyłącznie o kolumnę ``id``. Model historyczny
    ``Competition`` ma tyle kolumn, ile miał w swoim miejscu planu migracji, a późniejsze migracje
    ``tenancy`` (np. ``0003_prefixes``) bywają zdejmowane **przed** tą przy cofaniu bazy – kolejności
    nie da się wymusić zależnością w żadną stronę, bo ta migracja jest na produkcji już wykonana.
    ``SELECT id`` jest odpowiedzią odporną na to z definicji: kolumna klucza głównego istnieje
    w każdym stanie schematu, w którym tabela w ogóle jest.
    """
    Competition = apps.get_model("tenancy", "Competition")
    rows = list(Competition.objects.order_by("pk").values_list("pk", flat=True)[:2])
    if len(rows) > 1:
        raise RuntimeError(
            "Backfill konkursu działa wyłącznie na bazie jednokonkursowej "
            "(znaleziono więcej niż jeden Competition)."
        )
    return rows[0] if rows else None


def forwards(apps, schema_editor):
    competition_id = sole_competition(apps)
    if competition_id is None:
        return

    for name in SCOPED_MODELS:
        model = apps.get_model("accounts", name)
        # Warunek na ``NULL``, a nie brak warunku: wydanie B stoi obok kodu, który właściciela
        # już wpisuje, więc wiersz z wartością jest wierszem świeżo zapisanym, a nie zaległym.
        model.objects.filter(competition__isnull=True).update(competition_id=competition_id)

    Membership = apps.get_model("accounts", "Membership")
    User = apps.get_model("accounts", "User")
    for role in ROLES:
        # Kto ma grupę, ten dostaje członkostwo w Konkursie #1. ``granted_by`` zostaje puste:
        # ról nie nadał człowiek, tylko migracja, a wpisanie tu kogokolwiek byłoby fikcją.
        holders = User.objects.filter(groups__name=role).values_list("pk", flat=True)
        already = Membership.objects.filter(competition_id=competition_id, role=role).values_list(
            "user_id", flat=True
        )
        missing = set(holders) - set(already)
        Membership.objects.bulk_create(
            [
                Membership(user_id=user_id, competition_id=competition_id, role=role)
                for user_id in sorted(missing)
            ]
        )


def backwards(apps, schema_editor):
    competition_id = sole_competition(apps)
    if competition_id is None:
        return
    apps.get_model("accounts", "Membership").objects.filter(competition_id=competition_id).delete()
    for name in SCOPED_MODELS:
        apps.get_model("accounts", name).objects.filter(competition_id=competition_id).update(
            competition=None
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_memberships_and_competition_fks"),
        # Backfill ról czyta ``auth_user_groups``, więc historyczny model grupy musi być w stanie
        # migracji – tak samo, jak w ``0014_supervisor_rbac_group``.
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        # ``elidable=False``: tej migracji nie wolno zwinąć przy ``squashmigrations``. Jest
        # jednorazowym przepisaniem produkcyjnych danych, a nie krokiem budowy schematu.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
