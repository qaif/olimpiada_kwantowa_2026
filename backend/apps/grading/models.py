"""Modele oceniania: niezależna recenzja i ocena uzgodniona (PROJEKT.md 2.2).

Zasady:
- ocenianie jest ślepe: ``Review`` nie trzyma żadnych danych osobowych uczestnika, a serializery
  pokazują recenzentowi wyłącznie ``participant_public_code``,
- ``FinalGrade`` powstaje wyłącznie w serwisach (``apps.grading.services``) pod blokadą
  ``select_for_update`` na ``Submission`` – relacja jeden-do-jednego jest ostatnią linią obrony
  przed dwoma ocenami z wyścigu,
- czas zawsze przez ``django.utils.timezone.now()``.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import CommitteeMember
from apps.competitions.models import Problem
from apps.competitions.scoping import competition_scoped_manager, resolve_competition
from apps.submissions.models import Submission
from apps.tenancy.managers import CompetitionScopedQuerySet

#: Runda 1 to ocena ślepa (dwóch niezależnych recenzentów), runda 2 – rozjemcza (trzeci recenzent).
ROUND_BLIND = 1
ROUND_TIEBREAK = 2


def default_annotations() -> list:
    """``default`` JSONField musi być wywoływalny i zwracać nowy obiekt."""
    return []


def default_rubric() -> list:
    """Pusta rubryka. Osobna funkcja od ``default_annotations``: to dwa niezależne pola JSON."""
    return []


class ReviewStatus(models.TextChoices):
    ASSIGNED = "ASSIGNED", "przydzielona"
    DRAFT = "DRAFT", "szkic"
    SUBMITTED = "SUBMITTED", "wystawiona"
    # Recenzja bezprzedmiotowa: rozjazd rozstrzygnął ktoś inny (koordynator na posiedzeniu), więc
    # wiszący przydział rundy 2 nie może zostać w ASSIGNED – recenzent widziałby zadanie do zrobienia,
    # którego już nie da się wykonać. Kasowanie odpadło: ślad po przydziale ma zostać.
    CANCELLED = "CANCELLED", "anulowana"


class ReviewCancelReason(models.TextChoices):
    """Dlaczego recenzja przestała być aktualna – powód widoczny dla recenzenta, nie tylko w audycie.

    Anulowanie wygląda z panelu recenzenta tak samo w każdym przypadku: zadanie znika z listy.
    Powody są jednak zupełnie różne i recenzent ma prawo je rozróżnić – „koordynator odebrał Ci tę
    pracę” jest informacją o decyzji organizatora, a „uczestnik wysłał nową wersję” o tym, że jego
    praca nie zniknęła, tylko czeka na ocenę od nowa. Pusta wartość znaczy „recenzja anulowana przed
    wprowadzeniem tego pola” (wiersze sprzed migracji) – wtedy panel pokazuje treść ogólną.
    """

    COORDINATOR = "COORDINATOR", "koordynator odebrał pracę"
    SUPERSEDED = "SUPERSEDED", "nowa wersja rozwiązania"
    OVERRIDE = "OVERRIDE", "korekta oceny przez koordynatora"
    MODERATION_RESOLVED = "MODERATION_RESOLVED", "rozjazd rozstrzygnięty"
    REVISED = "REVISED", "recenzent poprawił ocenę"


class GradeMethod(models.TextChoices):
    CONSENSUS = "CONSENSUS", "zgodne oceny"
    THIRD_REVIEW = "THIRD_REVIEW", "trzeci recenzent"
    MODERATION = "MODERATION", "posiedzenie komisji"
    APPEAL = "APPEAL", "po reklamacji"
    # Korekta koordynatora ma **własny** tryb, a nie MODERATION: posiedzenie komisji rozstrzyga
    # rozjazd dwóch ocen, a to jest jednoosobowa decyzja organizatora – czasem dla pracy, której
    # nikt nie recenzował. Rozróżnienie jest widoczne w tabeli wyników i w aktach odwoławczych,
    # więc nie wolno go schować pod istniejącą wartością.
    COORDINATOR_OVERRIDE = "OVERRIDE", "korekta koordynatora"


class ReviewQuerySet(CompetitionScopedQuerySet):
    """Recenzje, z drogą do konkursu przez pracę – czyli przez jej kolumnę denormalizacyjną.

    Jedno złączenie zamiast czterech (``submission__entry__stage__edition__competition``) i to
    właśnie po to ta kolumna w ogóle powstała (§ 3.4): kolejka recenzenta i ekran postępu etapu
    filtrują recenzje po kilka razy na żądanie.
    """

    competition_path = "submission__competition"

    def for_reviewer(self, member: CommitteeMember | None):
        """Recenzje jednego recenzenta. ``None`` (brak profilu) nie widzi niczego."""
        if member is None:
            return self.none()
        return self.filter(reviewer=member)


class Review(models.Model):
    """Niezależna ocena jednego recenzenta dla jednego rozwiązania."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT, related_name="reviews")
    round = models.PositiveSmallIntegerField("runda", default=ROUND_BLIND)
    score = models.PositiveSmallIntegerField("punkty", null=True, blank=True)
    comment_internal = models.TextField("komentarz wewnętrzny", blank=True)
    comment_for_participant = models.TextField("komentarz dla uczestnika", blank=True)
    annotations = models.JSONField("adnotacje", default=default_annotations, blank=True)
    # Punkty cząstkowe z rubryki zadania: lista ``{criterion_id, points, comment}``. Osobne pole,
    # a nie kolejny kształt w ``annotations``: adnotacje są przypięte do miejsca w PDF-ie i mogą
    # trafić do uczestnika, a rubryka jest rozbiciem **oceny** na kryteria zadania i czyta ją
    # koordynator razem z punktami. Wspólne pole zmusiłoby każdego czytelnika do rozpoznawania,
    # z którym rodzajem wpisu ma do czynienia.
    #
    # Źródłem prawdy o ocenie zostaje ``score``: rubryka jest uzasadnieniem sumy, a nie jej
    # zamiennikiem. Dzięki temu praca oceniona przed wprowadzeniem kryteriów (albo zadanie, które
    # ich nie ma) zachowuje się dokładnie jak dotąd, a konsensus i tabela wyników liczą się z
    # jednego pola, nie z sumowania JSON-a przy każdym odczycie.
    rubric = models.JSONField("rubryka", default=default_rubric, blank=True)
    status = models.CharField(
        "status", max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.ASSIGNED
    )
    # Powód anulowania jest osobnym polem, a nie kolejną wartością ``status``: stan recenzji
    # („anulowana”) i przyczyna („bo przyszła nowa wersja pracy”) to dwie niezależne informacje,
    # a rozbicie statusu na pięć wartości zmusiłoby każdy filtr po CANCELLED do wyliczania ich listy.
    cancel_reason = models.CharField(
        "powód anulowania",
        max_length=24,
        choices=ReviewCancelReason.choices,
        blank=True,
        default="",
    )
    assigned_at = models.DateTimeField("przydzielona", default=timezone.now)
    # Termin oddania **tej** recenzji, wyliczany w chwili przydziału (``apps.grading.deadlines``).
    # Pole, a nie wartość liczona przy każdym odczycie z ``Stage.review_deadline_days``: recenzent
    # dostaje termin wraz z pracą i przedłużenie etapu zrobione tydzień później nie może po cichu
    # przesunąć terminu, który już zobaczył (ani cofnąć go, gdy koordynator skróci okno). ``None``
    # znaczy „przydział sprzed wprowadzenia terminów” – takiej recenzji nie pilnuje przypominajka.
    due_at = models.DateTimeField("termin recenzji", null=True, blank=True)
    # Kiedy poszło ostatnie przypomnienie o terminie. Znacznik jest po to, żeby beat wysyłał jeden
    # list dziennie, a nie jeden na każdy przebieg: zadanie chodzi codziennie, ale recenzent ma
    # dostać przypomnienie raz. ``None`` = jeszcze nie przypominaliśmy.
    reminded_at = models.DateTimeField("przypomnienie wysłane", null=True, blank=True)
    submitted_at = models.DateTimeField("wystawiona", null=True, blank=True)
    # Osobne pole, a nie nadpisanie ``submitted_at``: chwila pierwszego wystawienia oceny jest
    # faktem procesowym (czy recenzent zdążył przed terminem recenzji) i poprawka nie może jej
    # zacierać. ``None`` znaczy „nie poprawiano”.
    revised_at = models.DateTimeField("poprawiona", null=True, blank=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        verbose_name = "recenzja"
        verbose_name_plural = "recenzje"
        ordering = ("submission", "round", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "reviewer", "round"], name="grading_review_unique_assignment"
            ),
            models.CheckConstraint(condition=Q(round__gte=1), name="grading_review_round_positive"),
            # Wystawiona recenzja musi mieć punkty. Bez tego rozjazd/konsensus liczyłby się z None.
            models.CheckConstraint(
                condition=~Q(status=ReviewStatus.SUBMITTED) | Q(score__isnull=False),
                name="grading_review_submitted_has_score",
            ),
        ]

    def __str__(self) -> str:
        return f"recenzja {self.pk} (zgł. {self.submission_id}, runda {self.round}, {self.status})"

    @property
    def is_submitted(self) -> bool:
        return self.status == ReviewStatus.SUBMITTED

    @property
    def participant_public_code(self) -> str:
        """Jedyny identyfikator uczestnika, jaki wolno pokazać recenzentowi (ocenianie ślepe)."""
        return self.submission.entry.participant.public_code

    def public_annotations(self) -> list:
        """Adnotacje **prostokątne** oznaczone ``public`` – tylko te trafiają do uczestnika (T-07).

        Warunek ``rect`` nie jest ozdobą: od czasu uwag do linii kodu (``apps.grading.code_view``)
        w tym samym polu JSON mieszkają dwa kształty wpisu – prostokąt na stronie (``page``,
        ``rect``) i uwaga przypięta do numeru linii (``line``). Każdy czytelnik tej listy wypisuje
        adnotację jako „str. N: treść”, więc uwaga do linii wpadłaby tam jako „str. : treść”.
        Uwagi do linii mają własne wejście (``code_view.public_line_notes``) i własną prezentację.
        """
        return [
            item
            for item in (self.annotations or [])
            if isinstance(item, dict) and item.get("public") and item.get("rect") is not None
        ]


class FinalGrade(models.Model):
    """Ocena uzgodniona rozwiązania. Jedna na ``Submission`` – relacja pilnuje tego w bazie."""

    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name="final_grade")
    score = models.PositiveSmallIntegerField("punkty")
    method = models.CharField("tryb ustalenia", max_length=16, choices=GradeMethod.choices)
    # CONSENSUS nie ma człowieka podejmującego decyzję – zgodność dwóch ocen wynika z reguły,
    # a nie z czyjegoś rozstrzygnięcia. Stąd pole jest nullable.
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="final_grades",
        verbose_name="rozstrzygnął",
    )
    decided_at = models.DateTimeField("rozstrzygnięta", default=timezone.now)
    rationale = models.TextField("uzasadnienie", blank=True)

    #: Przez pracę – ocena uzgodniona jest jej własnością, nie osobnym bytem.
    objects = competition_scoped_manager("submission__competition")

    class Meta:
        verbose_name = "ocena uzgodniona"
        verbose_name_plural = "oceny uzgodnione"
        ordering = ("-decided_at", "-id")

    def __str__(self) -> str:
        return f"{self.score} pkt ({self.method}) dla zgł. {self.submission_id}"


class ProblemReviewerRule(models.Model):
    """Reguła „to zadanie recenzuje ta osoba” – przydział z góry, na poziomie całego zadania.

    Powód istnienia: automat równoważy obciążenie, ale nie zna podziału kompetencji w komitecie.
    Organizator chce móc powiedzieć „zadanie 3 sprawdza Kowalski” raz, zamiast klikać przy każdej
    pracy z osobna. Reguła jest więc **deklaracją**, a nie jednorazową akcją: obowiązuje zarówno
    prace już zablokowane (serwis dopisuje recenzje w chwili utworzenia reguły), jak i te, które
    wejdą do oceniania później (uwzględnia je najbliższy przebieg ``assign_reviewers``).

    Reguła nie unieważnia konfliktu interesów: recenzent w konflikcie z konkretnym uczestnikiem
    jest dla tej jednej pracy pomijany (``RULE_REVIEWER_CONFLICT``), a nie dopisywany „bo tak
    kazał organizator”. Kasowanie reguły nie rusza recenzji, które już powstały – przydział, który
    ktoś zaczął wykonywać, znika wyłącznie przez świadome cofnięcie (``unassign_reviewer``).

    ``on_delete=CASCADE`` po obu stronach: reguła bez zadania albo bez recenzenta nie ma sensu,
    a ślad po samej deklaracji zostaje w audycie (``review.rule_added`` / ``review.rule_removed``).
    """

    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="reviewer_rules")
    reviewer = models.ForeignKey(CommitteeMember, on_delete=models.CASCADE, related_name="problem_rules")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="problem_reviewer_rules",
        verbose_name="utworzył",
    )
    created_at = models.DateTimeField("utworzona", default=timezone.now)

    #: Przez zadanie: reguła mówi „**to** zadanie sprawdza ta osoba”, a zadanie należy do etapu.
    objects = competition_scoped_manager("problem__stage__edition__competition")

    class Meta:
        verbose_name = "reguła przydziału zadania"
        verbose_name_plural = "reguły przydziału zadań"
        # Kolejność tworzenia jest częścią semantyki: recenzenci z reguł wchodzą na miejsca
        # ``per_submission`` w tej właśnie kolejności, więc sortowanie nie może być dowolne.
        ordering = ("problem", "created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "reviewer"], name="grading_problem_rule_unique_reviewer"
            )
        ]

    def __str__(self) -> str:
        return f"zadanie {self.problem_id} → recenzent {self.reviewer_id}"


#: Górny limit długości notatki recenzenckiej. Notatka jest krótkim głosem w rozmowie o jednej
#: pracy („mam 2, bo brakuje uzasadnienia kroku 3”), a nie drugą recenzją – limit trzyma ją w tej
#: roli i zarazem chroni bazę przed wklejeniem całego rozwiązania.
MAX_NOTE_LENGTH = 1000


class RubricCriterion(models.Model):
    """Kryterium oceny jednego zadania: „za co i ile punktów”.

    Powód istnienia (prośba organizatora): dwie niezależne oceny tej samej pracy rozjeżdżają się
    najczęściej nie dlatego, że recenzenci różnie czytają rozwiązanie, tylko dlatego, że różnie
    dzielą punkty. Rubryka zapisuje ten podział **raz, przy zadaniu**, a recenzent wypełnia go
    kryterium po kryterium – suma jest wtedy skutkiem jawnych decyzji, a nie wrażenia.

    Model jest w ``grading``, a nie w ``competitions``, mimo klucza obcego do zadania: to część
    procedury oceniania (czyta ją panel recenzenta i ``Review.rubric``), a nie opisu zawodów.
    Kryteria są **opcjonalne**: zadanie bez nich ocenia się dokładnie tak, jak dotąd.

    ``max_points`` kryterium nie jest wiązane ze skalą zadania w bazie: suma maksimów bywa większa
    od maksymalnej oceny (recenzent dzieli punkty, a nie sumuje wszystko do końca), a dopuszczalność
    samej **sumy** sprawdza ``apps.grading.rubric.validate_rubric`` przy każdym zapisie oceny.
    """

    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="rubric_criteria")
    # Kolejność wpisuje koordynator, bo rubryka jest listą czytaną z góry na dół i ma odpowiadać
    # budowie rozwiązania. Bez własnego pola porządek wynikałby z identyfikatorów, więc poprawienie
    # jednego kryterium (skasuj i dodaj) przenosiłoby je na koniec listy.
    order = models.PositiveSmallIntegerField("kolejność", default=1)
    title = models.CharField("kryterium", max_length=200)
    description = models.TextField("opis", blank=True)
    max_points = models.PositiveSmallIntegerField("maksimum punktów")
    created_at = models.DateTimeField("utworzone", default=timezone.now)

    #: Przez zadanie, bo rubryka jest opisem **tego** zadania, a zadanie należy do etapu.
    objects = competition_scoped_manager("problem__stage__edition__competition")

    class Meta:
        verbose_name = "kryterium rubryki"
        verbose_name_plural = "kryteria rubryki"
        ordering = ("problem", "order", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(max_points__gte=1), name="grading_rubric_criterion_max_points_positive"
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} (maks. {self.max_points} pkt)"


class ReviewNote(models.Model):
    """Krótka notatka recenzentów przy jednej pracy – rozmowa o rozjeździe, nie druga recenzja.

    Wątek otwiera się dopiero po odsłonięciu ocen (patrz ``apps.grading.comparison``), więc nie
    psuje niezależności rundy 1: dopóki obie oceny nie są wystawione, nikt tu niczego nie napisze
    ani nie przeczyta. Potem bywa jedynym miejscem, w którym dwoje recenzentów może uzgodnić
    stanowisko bez wyciągania koordynatora na posiedzenie.

    Autorem jest ``CommitteeMember``, a nie ``User``: notatkę pisze recenzent tej pracy, a jego
    tożsamość i tak nie jest pokazywana drugiej stronie (panel podpisuje wpisy „Recenzent A/B”).
    Koordynator czyta wątek na pulpicie, ale go nie pisze – od decyzji organizatora jest
    rozstrzygnięcie moderacji z uzasadnieniem, a nie głos w dyskusji recenzentów.

    ``on_delete=CASCADE`` po stronie pracy (notatka bez pracy nie ma sensu) i ``PROTECT`` po
    stronie autora – tak samo jak w ``Review``: konta członka komitetu, który zostawił ślad
    w aktach oceniania, nie kasuje się mimochodem.
    """

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="review_notes")
    author = models.ForeignKey(CommitteeMember, on_delete=models.PROTECT, related_name="review_notes")
    text = models.CharField("treść", max_length=MAX_NOTE_LENGTH)
    created_at = models.DateTimeField("dodana", default=timezone.now)

    #: Przez pracę, tak samo jak recenzja, której notatka towarzyszy.
    objects = competition_scoped_manager("submission__competition")

    class Meta:
        verbose_name = "notatka recenzencka"
        verbose_name_plural = "notatki recenzenckie"
        # Chronologicznie: wątek czyta się od początku, a nie od końca.
        ordering = ("submission", "created_at", "id")

    def __str__(self) -> str:
        return f"notatka {self.pk} (zgł. {self.submission_id})"


#: Limit długości szablonu komentarza. Szablon jest gotowym akapitem („brakuje uzasadnienia
#: przejścia granicznego”), a nie całą recenzją – dłuższy tekst przestaje być czymś, co da się
#: wstawić i doprecyzować, a zaczyna być komentarzem napisanym za recenzenta.
MAX_SNIPPET_LENGTH = 2000


class CommentSnippet(models.Model):
    """Gotowy fragment komentarza do wstawienia w recenzji – „szablon komentarza”.

    Po co (prośba organizatora): przy trzydziestu pracach z jednego zadania te same trzy zdania
    („brakuje uzasadnienia przejścia granicznego”, „wynik poprawny, ale bez jednostek”) recenzent
    przepisuje trzydzieści razy. Szablon jest tekstem **do wstawienia i poprawienia**, a nie
    automatem: system nic nie wstawia sam, a wstawiony tekst wolno w polu komentarza edytować.

    Dwa niezależne wymiary, oba wyrażone dopuszczeniem ``NULL``:

    - ``problem`` – szablon zadania. ``None`` znaczy „nie dotyczy jednego zadania”, czyli szablon
      ogólny, dostępny przy każdej pracy. Wiązanie z etapem zamiast z zadaniem odpadło: kryteria
      różnią się między zadaniami, a nie między etapami, i to zadanie jest tym, co recenzent widzi
      w kolejce („zadanie 3 w wielu pracach”),
    - ``owner`` – właściciel. ``None`` znaczy „szablon wspólny”, przygotowany przez koordynatora
      dla całego komitetu; wartość wskazuje recenzenta, który stworzył go dla siebie. Cudzych
      prywatnych szablonów nie widzi nikt – ani inny recenzent, ani koordynator w panelu.

    ``CASCADE`` po obu stronach: szablon bez zadania albo bez autora nie ma do czego należeć,
    a wstawione już do recenzji zdania są zwykłym tekstem w ``Review.comment_for_participant``
    i skasowanie szablonu nigdy ich nie rusza.
    """

    #: Własna kolumna konkursu, choć § 3.4 prowadzi ten model do właściciela przez zadanie. Powód
    #: jest jeden i jest nim ``NULL`` w ``problem``: szablon ogólny nie ma zadania, przez które
    #: mógłby dojść do konkursu, więc do wydania D był wierszem wspólnym dla **całej instalacji** –
    #: czyli w bazie wielokonkursowej wierszem cudzym. Droga przez zadanie istnieje dalej i nadal
    #: jest prawdą; ta kolumna wyraża ją tam, gdzie tamtej drogi nie ma.
    #:
    #: ``PROTECT`` jak przy edycji: szablony są wykładnią komitetu do zadań, a nie danymi
    #: tymczasowymi, i skasowanie konkursu ma się o nie zatrzymać.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="comment_snippets",
        verbose_name="konkurs",
    )
    problem = models.ForeignKey(
        Problem,
        on_delete=models.CASCADE,
        related_name="comment_snippets",
        null=True,
        blank=True,
        verbose_name="zadanie",
    )
    owner = models.ForeignKey(
        CommitteeMember,
        on_delete=models.CASCADE,
        related_name="comment_snippets",
        null=True,
        blank=True,
        verbose_name="właściciel",
    )
    title = models.CharField("tytuł", max_length=200)
    text = models.TextField("treść", max_length=MAX_SNIPPET_LENGTH)
    # Kolejność ustala autor listy (koordynator wierszami w textarei, recenzent kolejnością
    # dopisywania). Bez własnego pola porządek wynikałby z identyfikatorów, więc poprawienie
    # jednego szablonu przenosiłoby go na koniec – a lista jest czytana z góry na dół.
    order = models.PositiveSmallIntegerField("kolejność", default=1)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    #: Własną kolumną, a nie ścieżką przez zadanie: filtr po ``problem__stage__edition__competition``
    #: gubił **szablony ogólne** (``problem IS NULL``), czyli dokładnie te wiersze, o które w tej
    #: zmianie chodzi.
    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "szablon komentarza"
        verbose_name_plural = "szablony komentarzy"
        ordering = ("order", "id")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        """Nowy szablon bez wskazanego konkursu bierze go z zadania, a bez zadania – z kontekstu.

        Wartość domyślna jest w modelu z tego samego powodu, co przy ``Edition``: szablony zakłada
        też kod spoza serwisu ``apps.grading.snippets`` – panel ``/admin/``, import i fabryki
        testowe. Szablon bez właściciela byłby po wydaniu D wierszem nie do zapisania (``NOT
        NULL``), a przed nim – wierszem widocznym u cudzego recenzenta.

        Zadanie ma pierwszeństwo przed kontekstem, bo jest **faktem o szablonie**: szablon do
        zadania konkursu B należy do konkursu B także wtedy, gdy zapisuje go ktoś zalogowany pod
        domeną konkursu A. Wyłącznie przy wstawianiu – przy zapisie istniejącego wiersza konkurs
        jest faktem, a nie wartością domyślną.
        """
        if self._state.adding and self.competition_id is None:
            if self.problem_id is not None:
                self.competition_id = (
                    Problem.objects.filter(pk=self.problem_id)
                    .values_list("stage__edition__competition_id", flat=True)
                    .first()
                )
            if self.competition_id is None:
                self.competition = resolve_competition()
        return super().save(*args, **kwargs)

    @property
    def is_shared(self) -> bool:
        """Czy szablon jest wspólny dla komitetu (koordynatora), a nie prywatny recenzenta."""
        return self.owner_id is None


class ReviewWorkLog(models.Model):
    """Zmierzony czas pracy nad jedną recenzją – suma sekund, nigdy przebieg czynności.

    Po co (prośba organizatora): planowanie obciążenia komitetu opiera się dziś na wyczuciu
    („zadanie 3 idzie wolno”). Licznik zamienia to wyczucie w liczbę, którą widać przy recenzji
    i przy recenzencie na ekranie postępu etapu.

    Czego ten model **nie** zapisuje i zapisywać nie będzie: co recenzent pisał, gdzie klikał, ani
    kiedy dokładnie przerywał. W bazie są trzy znaczniki czasu i jedna suma sekund – tyle wystarczy
    na pytanie „ile godzin zajmuje ocena zadania 3” i za mało na jakąkolwiek ocenę człowieka.

    Relacja jeden-do-jednego, bo licznik jest **narastający**: recenzent wraca do pracy wiele razy,
    a interesuje nas suma, nie lista posiedzeń. ``started_at`` to pierwsze wejście w recenzję,
    ``last_seen_at`` – ostatni odebrany sygnał życia; różnica między nimi bywa dniami i właśnie
    dlatego ``seconds`` nie da się z nich wyliczyć (patrz ``apps.grading.worklog``).
    """

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="work_log")
    started_at = models.DateTimeField("początek pracy", default=timezone.now)
    last_seen_at = models.DateTimeField("ostatni sygnał", default=timezone.now)
    seconds = models.PositiveIntegerField("zmierzony czas (s)", default=0)

    #: Przez recenzję i jej pracę. Modelu nie ma w liście § 3.5, ale jest w tabeli dróg § 3.4 –
    #: pominięcie zostawiłoby w audycie izolacji tabelę bez odpowiedzi na pytanie „czyja”.
    objects = competition_scoped_manager("review__submission__competition")

    class Meta:
        verbose_name = "czas pracy nad recenzją"
        verbose_name_plural = "czasy pracy nad recenzjami"
        ordering = ("review", "id")

    def __str__(self) -> str:
        return f"recenzja {self.review_id}: {self.seconds} s"


class WorkIssueKind(models.TextChoices):
    """Co jest nie tak z pracą. Lista zamknięta, bo od opisu słownego jest pole ``text``."""

    UNREADABLE = "UNREADABLE", "praca nieczytelna"
    WRONG_PROBLEM = "WRONG_PROBLEM", "rozwiązanie innego zadania"
    PLAGIARISM_SUSPECTED = "PLAGIARISM", "podejrzenie niesamodzielności"
    OTHER = "OTHER", "inne"


class WorkIssueStatus(models.TextChoices):
    OPEN = "OPEN", "otwarte"
    RESOLVED = "RESOLVED", "rozwiązane"


class WorkIssue(models.Model):
    """Zgłoszenie recenzenta: „z tą pracą jest coś nie tak” – sygnał do koordynatora.

    Po co (prośba organizatora): recenzent, któremu trafi się skan nie do odczytania albo
    rozwiązanie zupełnie innego zadania, nie ma dziś dokąd z tym pójść poza pocztą. Zgłoszenie jest
    drogą **wewnątrz systemu**: koordynator widzi je na własnym ekranie, z pracą i etapem w ręku,
    a rozstrzygnięcie zostaje przy recenzji, a nie w cudzej skrzynce.

    Zgłoszenie **nie blokuje oceniania** i to jest świadoma decyzja. Recenzent może uważać pracę za
    nieczytelną i mimo to wystawić ocenę, jaką da się obronić; zablokowanie formularza zamieniłoby
    sygnał w ultimatum i zmusiłoby część komitetu do omijania go pocztą – czyli dokładnie do tego,
    po co ten model powstał. Panel pokazuje więc otwarte zgłoszenie jako baner, a nie jako bramkę.

    Praca jest tu zapisana **osobno**, choć wynika z recenzji: koordynator filtruje zgłoszenia po
    etapie, a ekran zgłoszeń ma działać także dla recenzji, której dotyczą dwa różne zgłoszenia od
    dwóch recenzentów tej samej pracy. ``CASCADE`` po obu stronach – zgłoszenie bez pracy nie ma
    przedmiotu, a ślad decyzji zostaje w audycie (``issue.opened`` / ``issue.resolved``).
    """

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="issues")
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="work_issues")
    kind = models.CharField("rodzaj", max_length=24, choices=WorkIssueKind.choices)
    text = models.TextField("opis", max_length=MAX_NOTE_LENGTH)
    status = models.CharField(
        "status", max_length=16, choices=WorkIssueStatus.choices, default=WorkIssueStatus.OPEN
    )
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_work_issues",
        verbose_name="rozwiązał",
    )
    resolved_at = models.DateTimeField("rozwiązane", null=True, blank=True)
    resolution = models.TextField("rozstrzygnięcie", blank=True)

    #: Przez pracę, a nie przez recenzję: zgłoszenie dotyczy **pracy** (dwóch recenzentów potrafi
    #: zgłosić tę samą), a kolejka koordynatora filtruje właśnie po pracach.
    objects = competition_scoped_manager("submission__competition")

    class Meta:
        verbose_name = "zgłoszenie problemu z pracą"
        verbose_name_plural = "zgłoszenia problemów z pracami"
        # Najnowsze na górze: ekran koordynatora jest kolejką do obsłużenia, a nie archiwum.
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"zgłoszenie {self.pk} ({self.kind}, zgł. {self.submission_id})"

    @property
    def is_open(self) -> bool:
        return self.status == WorkIssueStatus.OPEN
