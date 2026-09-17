"""Backfill wydania D: zastanym wpisom audytu dopisujemy konkurs obiektu, o którym mówią.

To jest migracja **najlepszej staranności** (``docs/UNIWERSALNY-ETAP-1.md`` § 3.9) i tak ma być.
``AuditLog`` opisuje obiekt parą tekstową ``(target_type, target_id)``, a nie kluczem obcym, więc
droga do konkursu wiedzie tu przez obiekt – a obiektu czasem już nie ma. Wpis o skasowanym etapie
zostaje z pustym konkursem i jest to odpowiedź poprawna: ślad po skasowanym obiekcie jest
dokładnie tym, po co audyt istnieje, a przeglądarka koordynatora pokazuje go dalej
(``AuditLog.objects.visible_to``).

Trzy grupy wierszy i trzy różne wyniki:

1. **obiekt domeny zawodów** (uczestnik, etap, praca, recenzja, ocena, reklamacja, dyplom,
   komunikat, kod zaproszenia, wysyłka, warsztat, zadanie…) → konkurs wyliczony drogą z § 3.4,
2. **sam konkurs** (``tenancy.competition``) → ten konkurs; bez tego przypadku ekran „Ustawienia
   konkursu” zapisywałby ślad, którego autor po wdrożeniu by nie zobaczył,
3. **obiekt platformowy** (``accounts.user``, ``wagtailcore.site``, alert infrastruktury,
   zgłoszenie do supportu) **oraz obiekt nieistniejący** → ``NULL``. Konta i witryny nie należą do
   konkursu z definicji (§ 3.4), a zgłoszenie bywa skierowane do operatora platformy (§ 3.2).

Mapa dróg jest **wypisana wprost**, a nie czytana z rejestru modeli. Migracja ma opisywać świat
z chwili, w której powstała: introspekcja ``_default_manager.competition_path`` sięgałaby do
modeli **bieżących**, więc ta sama migracja dawałaby za rok inny wynik niż dziś – a to jest
dokładnie ta klasa błędu, przed którą chroni reguła „wyłącznie modele historyczne” (§ 4.2).
Ścieżka nieznana albo model nieobecny jest pomijany, nie jest błędem.

Idempotencja: przepisujemy wyłącznie wiersze z ``competition IS NULL``, więc powtórny przebieg
niczego nie nadpisuje.
"""

from django.db import migrations

#: ``target_type`` → droga od obiektu do konkursu, w zapisie ORM-owym. Kopia tabeli z § 3.2 i § 3.4
#: na stan wydania D; komentarz przy każdej grupie mówi, skąd wzięła się jej ścieżka.
TARGET_PATHS = {
    # Własna kolumna właściciela (§ 3.2).
    "accounts.participant": "competition",
    "accounts.committeemember": "competition",
    "accounts.schoolsupervisor": "competition",
    "accounts.invitationcode": "competition",
    "accounts.messagebroadcast": "competition",
    "accounts.membership": "competition",
    "cms.announcement": "competition",
    "competitions.edition": "competition",
    # Kolumna denormalizacyjna – jedyna dopuszczona w tym etapie (§ 3.4).
    "submissions.submission": "competition",
    # Droga przez edycję.
    "competitions.stage": "edition__competition",
    "competitions.editionevent": "edition__competition",
    "results.certificate": "edition__competition",
    "results.certificatetemplate": "edition__competition",
    # Droga przez etap.
    "competitions.problem": "stage__edition__competition",
    "competitions.stageentry": "stage__edition__competition",
    "competitions.scoringscale": "stage__edition__competition",
    "competitions.qualificationrule": "stage__edition__competition",
    "competitions.interviewslot": "stage__edition__competition",
    "results.resultspublication": "stage__edition__competition",
    "submissions.submissionsimilarity": "stage__edition__competition",
    "quiz.quiz": "stage__edition__competition",
    "competitions.interviewbooking": "entry__stage__edition__competition",
    "quiz.quizquestion": "quiz__stage__edition__competition",
    # Droga przez pracę.
    "grading.review": "submission__competition",
    "grading.finalgrade": "submission__competition",
    "grading.reviewnote": "submission__competition",
    "grading.workissue": "submission__competition",
    "submissions.submissionfile": "submission__competition",
    "appeals.appeal": "submission__competition",
    "grading.reviewworklog": "review__submission__competition",
    "appeals.appealdecision": "appeal__submission__competition",
    # Droga przez zadanie.
    "grading.problemreviewerrule": "problem__stage__edition__competition",
    "grading.rubriccriterion": "problem__stage__edition__competition",
    "grading.commentsnippet": "problem__stage__edition__competition",
}

#: ``target_type`` wpisów, których obiektem jest **sam konkurs**. Nie ma do czego dołączać –
#: ``target_id`` *jest* identyfikatorem konkursu.
COMPETITION_TARGET_TYPE = "tenancy.competition"

#: Ile identyfikatorów wkładamy w jedno ``IN (...)``. Tysiąc, bo produkcyjna tabela audytu ma
#: rzędu dziesiątek tysięcy wierszy, a zapytanie z listą stu tysięcy parametrów bywa odrzucane
#: przez sterownik, zanim dojdzie do bazy.
CHUNK = 1000


def _chunks(values: list):
    for start in range(0, len(values), CHUNK):
        yield values[start : start + CHUNK]


def _assign(AuditLog, target_type: str, by_competition: dict) -> None:
    """Dopisuje konkurs wpisom o wskazanych obiektach. Jedno ``UPDATE`` na konkurs i porcję."""
    for competition_id, target_ids in by_competition.items():
        for chunk in _chunks(target_ids):
            AuditLog.objects.filter(
                competition__isnull=True, target_type=target_type, target_id__in=chunk
            ).update(competition_id=competition_id)


def forwards(apps, schema_editor):
    AuditLog = apps.get_model("core", "AuditLog")
    Competition = apps.get_model("tenancy", "Competition")
    if not Competition.objects.exists():
        # Świeża instalacja: ``tenancy.0002`` nie miała z czego utworzyć konkursu, więc nie ma
        # czego dopisywać. To nie jest błąd – to jest baza bez danych.
        return

    # Rodzaje obiektów czytamy z **danych**, a nie z mapy: instalacja używa zwykle kilkunastu
    # z kilkudziesięciu możliwych, a zapytanie po indeksie ``core_audit_target_idx`` jest tańsze
    # niż trzydzieści przebiegów po tabelach, w których i tak nic nie ma.
    present = set(AuditLog.objects.values_list("target_type", flat=True).distinct())

    if COMPETITION_TARGET_TYPE in present:
        # Wpis o samym konkursie wskazuje go swoim ``target_id`` – konkursem wpisu jest więc
        # obiekt wpisu. Konkursy liczy się w dziesiątkach, więc pętla po nich jest tu tańsza
        # i czytelniejsza niż jakiekolwiek zapytanie łączone.
        _assign(
            AuditLog,
            COMPETITION_TARGET_TYPE,
            {pk: [str(pk)] for pk in Competition.objects.values_list("pk", flat=True)},
        )

    for target_type in sorted(present & set(TARGET_PATHS)):
        app_label, model_name = target_type.split(".", 1)
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:  # pragma: no cover - aplikacja zdjęta z projektu po zapisie wpisu
            continue
        path = TARGET_PATHS[target_type]
        by_competition: dict[int, list[str]] = {}
        rows = model.objects.filter(**{f"{path}__isnull": False}).values_list("pk", path)
        for pk, competition_id in rows.iterator(chunk_size=CHUNK):
            by_competition.setdefault(competition_id, []).append(str(pk))
        _assign(AuditLog, target_type, by_competition)


def backwards(apps, schema_editor):
    """Zdejmuje konkurs ze wszystkich wpisów.

    Jawna odwrotność, a nie ``noop``: kolumna po cofnięciu tej migracji zostaje na miejscu
    (zdejmuje ją dopiero odwrotność ``0002``), więc bez wyzerowania wróciłoby się do stanu, którego
    ponowny przebieg forward by nie ruszył – a ``forwards`` przepisuje wyłącznie wiersze puste.
    """
    apps.get_model("core", "AuditLog").objects.exclude(competition__isnull=True).update(competition=None)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_auditlog_competition"),
        # Każda z tych migracji **wypełnia** kolumnę konkursu w swojej aplikacji. Zależność jest
        # od backfillu, a nie od ``AddField``: gdyby kolumna źródłowa była jeszcze pusta, ten
        # przebieg policzyłby zero wierszy i zostawił audyt bez właściciela – po cichu.
        ("accounts", "0020_backfill_competition_and_memberships"),
        ("cms", "0022_backfill_announcement_competition"),
        ("competitions", "0020_backfill_edition_competition"),
        ("submissions", "0006_backfill_submission_competition"),
        # Modele, do których dochodzimy przez cudzą kolumnę – potrzebne wyłącznie w stanie
        # migracji, żeby ``apps.get_model`` miało co oddać.
        ("appeals", "0002_appeal_decision_committee_through"),
        ("grading", "0008_comment_snippets_worklog_issues"),
        ("quiz", "0001_initial"),
        ("results", "0004_certificate_templates_and_workshop_attendance"),
    ]

    operations = [
        # ``elidable=False``: jednorazowe przepisanie produkcyjnych danych, a nie krok budowy
        # schematu – ``squashmigrations`` nie ma prawa go zwinąć.
        migrations.RunPython(forwards, backwards, elidable=False)
    ]
