"""Modele rozwiązań: zgłoszenie (wersjonowane) i jego plik w prywatnym storage.

Zasady:
- plik nigdy nie jest identyfikowany nazwą od użytkownika – ``object_key`` buduje ``storage.py``
  z identyfikatorów technicznych i sumy sha256 (PROJEKT.md 1.3),
- filtr per rola jest w queryseckie (``Submission.objects.for_user``), nie w widoku (PROJEKT.md 2.2),
- czas zawsze przez ``django.utils.timezone.now()``.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import GROUP_COORDINATOR, CommitteeStatus
from apps.competitions.models import Problem, Stage, StageEntry
from apps.competitions.scoping import (
    competition_scoped_manager,
    resolve_competition,
    scope_to_competition,
)
from apps.tenancy.managers import CompetitionScopedQuerySet


class SubmissionStatus(models.TextChoices):
    """Cykl życia zgłoszenia (PROJEKT.md 2.4). T-04 używa czterech pierwszych wartości."""

    SUBMITTED = "SUBMITTED", "oddane"
    SCANNING = "SCANNING", "skanowanie"
    REJECTED_INFECTED = "REJECTED_INFECTED", "odrzucone (wirus)"
    LOCKED = "LOCKED", "zablokowane"
    IN_REVIEW = "IN_REVIEW", "w ocenie"
    MODERATION = "MODERATION", "moderacja"
    GRADED_PROVISIONAL = "GRADED_PROVISIONAL", "ocena wstępna"
    APPEALED = "APPEALED", "reklamacja"
    FINAL = "FINAL", "ocena ostateczna"


class AvStatus(models.TextChoices):
    PENDING = "PENDING", "oczekuje na skan"
    CLEAN = "CLEAN", "czysty"
    INFECTED = "INFECTED", "zainfekowany"
    # Stan trwały: skan nie ma jak się udać (brak obiektu w storage, plik ponad StreamMaxLength clamd).
    # Bez niego plik zostawałby na zawsze PENDING, a zgłoszenie w SCANNING – patrz apps.submissions.tasks.
    ERROR = "ERROR", "błąd skanu"


class SubmissionQuerySet(CompetitionScopedQuerySet):
    """Prace, z **własną** kolumną konkursu – jedyną denormalizacją w całym etapie 1 (§ 3.4).

    Powód denormalizacji jest mierzalny: panel koordynatora i lista przydziałów recenzenta filtrują
    po pracach kilkanaście razy na żądanie, a złączenie ``entry → stage → edition`` przy każdym
    takim zapytaniu jest kosztem, którego nie ma czym uzasadnić. Ceną jest druga droga do tej samej
    prawdy, więc spójności pilnuje zapis (``Submission.save``) i test.
    """

    competition_path = "competition"

    def for_user(self, user, competition=None):
        """Widoczność per rola: uczestnik → własne, koordynator → wszystkie, recenzent → przydzielone.

        Członek komisji odwoławczej widzi dodatkowo rozwiązania, na które złożono reklamację –
        ale wyłącznie te, przy których nie ma konfliktu interesów, czyli nie jest autorem
        ``grading.Review`` z rundy 1 ani 2 tego rozwiązania (PROJEKT.md 2.4). Definicja rund jest
        jedna: ``apps.appeals.models.CONFLICTING_ROUNDS``, ta sama, którą sprawdza serwis decyzji.

        Reguła jest domyślnie zamknięta: kto nie ma ani profilu uczestnika, ani aktywnego profilu
        komitetu z przydziałem (``grading.Review``) lub uprawnieniem komisji odwoławczej, nie widzi
        niczego. Relacje ``reviews`` i ``appeals`` są odwrotnymi stronami FK z ``apps.grading``
        i ``apps.appeals`` – celowo przez nazwę, żeby nie robić importu w drugą stronę (te aplikacje
        zależą od submissions, nie odwrotnie).

        Definicja „aktywnego recenzenta” (status ACTIVE **i** grupa ``reviewer``) jest jedna dla
        całego systemu i mieszka w ``apps.accounts.services.active_reviewer_profile`` – widoczność
        plików nie może być luźniejsza niż uprawnienie, które wpuszcza do ``/api/grading/reviews/``.

        **Zakres konkursu idzie przed rolą** (§ 3.5) i to jest najważniejsza linia tej zmiany:
        ``for_user`` rozstrzyga *rolę*, ``for_competition`` – *własność*. Do etapu 1 koordynator
        widział tu wszystko; odtąd widzi wszystko **swojego** konkursu, bo rola jest rolą
        w konkursie, a nie w instalacji. Odwrócenie kolejności (najpierw rola, potem zakres) dałoby
        gałąź koordynatora, która zwraca ``self`` i nigdy już nie zostaje zawężona.
        """
        # Importy lokalne. ``apps.accounts.services`` ciągnie za sobą warstwę serwisową, a ten moduł
        # jest ładowany podczas rejestrowania aplikacji. ``apps.appeals`` i ``apps.grading`` zależą
        # od ``apps.submissions``, więc import na poziomie modułu byłby cyklem – stąd tutaj, gdzie
        # obie aplikacje są już załadowane.
        from apps.accounts.services import active_reviewer_profile, participant_for
        from apps.appeals.models import CONFLICTING_ROUNDS
        from apps.grading.models import Review

        competition = resolve_competition(competition)
        scoped = scope_to_competition(self, competition)
        if not user or not user.is_authenticated or not user.is_active:
            return scoped.none()
        if user.groups.filter(name=GROUP_COORDINATOR).exists():
            return scoped
        conditions = []
        participant = participant_for(user, competition)
        if participant is not None:
            conditions.append(Q(entry__participant=participant))
        reviewer = active_reviewer_profile(user, competition)
        if reviewer is not None:
            conditions.append(Q(reviews__reviewer=reviewer))
        member = getattr(user, "committee_member", None)
        appeals_member = (
            member
            if member is not None and member.status == CommitteeStatus.ACTIVE and member.is_appeals_committee
            else None
        )
        if appeals_member is not None:
            # Konflikt interesów wyklucza się podzapytaniem po kluczu głównym, a nie negacją na
            # złączeniu ``reviews``. Dwa powody:
            #
            # - ``~Q(reviews__reviewer=...)`` w tym samym ``filter()`` co gałąź recenzenta reużywa
            #   jej złączenia i Django koreluje negację z *wierszem recenzji*, a nie ze zgłoszeniem
            #   (``NOT EXISTS(... U1.id = grading_review.id)``). Wystarczała wtedy jedna cudza
            #   recenzja tej pracy, żeby warunek był spełniony mimo konfliktu. Efekt maskowała
            #   gałąź recenzenta (która i tak pokazuje własne przydziały), ale poprawność warunku
            #   nie może zależeć od tego, jakie inne gałęzie akurat są w zapytaniu,
            # - rundy konfliktowe mają jedną definicję (``apps.appeals.models.CONFLICTING_ROUNDS``).
            #   Negacja po całej relacji ukrywała reklamację przy recenzji z *dowolnej* rundy, więc
            #   „nie widzę” było szersze niż „nie mogę rozstrzygnąć” z serwisu.
            conflicted = Review.objects.filter(reviewer=appeals_member, round__in=CONFLICTING_ROUNDS).values(
                "submission_id"
            )
            conditions.append(Q(appeals__isnull=False) & ~Q(pk__in=conflicted))
        if not conditions:
            return scoped.none()
        query = conditions[0]
        for extra in conditions[1:]:
            query |= extra
        queryset = scoped.filter(query)
        # JOIN po recenzjach i reklamacjach potrafi zwielokrotnić wiersze (dwie recenzje tego samego
        # zgłoszenia w rundach 1 i 2), więc tylko ta gałąź wymaga odsiania duplikatów.
        if reviewer is not None or appeals_member is not None:
            return queryset.distinct()
        return queryset


class Submission(models.Model):
    """Jedno oddanie zadania przez uczestnika. Kolejne oddania to kolejne wersje, nie nadpisanie."""

    uuid = models.UUIDField("uuid", default=uuid.uuid4, unique=True, editable=False)
    #: **Jedyna** kolumna denormalizacyjna etapu 1 (§ 3.4). Nie zastępuje drogi przez
    #: ``entry → stage → edition`` – powtarza jej wynik, żeby zapytania panelu i kolejki recenzenta
    #: nie ciągnęły za sobą trzech złączeń. Spójności nie da się wyrazić ``CheckConstraint``-em
    #: (warunek sięga innej tabeli), więc pilnuje jej ``save()`` poniżej oraz test
    #: ``test_submission_competition_matches_entry``.
    #:
    #: ``db_index`` wprost, choć klucz obcy i tak zakłada indeks: to pole jest **filtrem**, a nie
    #: relacją do przechodzenia, i ma być widać w modelu, po co tam stoi.
    #:
    #: Nullowalne przez wydanie B (§ 4.1); ``SET_NULL``, a nie ``PROTECT`` jak przy edycji, bo to
    #: kopia, a nie źródło – skasowanie konkursu ma zatrzymać się na ``Edition``.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_index=True,
        related_name="submissions",
        verbose_name="konkurs",
    )
    entry = models.ForeignKey(StageEntry, on_delete=models.CASCADE, related_name="submissions")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="submissions")
    version = models.PositiveSmallIntegerField("wersja", default=1)
    submitted_at = models.DateTimeField("oddane", default=timezone.now)
    is_late = models.BooleanField("po deadline (w tolerancji)", default=False)
    status = models.CharField(
        "status", max_length=24, choices=SubmissionStatus.choices, default=SubmissionStatus.SUBMITTED
    )

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "rozwiązanie"
        verbose_name_plural = "rozwiązania"
        ordering = ("-submitted_at", "-version", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "problem", "version"], name="submissions_submission_unique_version"
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1), name="submissions_submission_version_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id}/zad. {self.problem_id} v{self.version} ({self.status})"

    def save(self, *args, **kwargs):
        """Nowa praca dziedziczy konkurs po swoim wpisie do etapu – zawsze, nie „zwykle”.

        Kolumna jest denormalizacją, więc jej wartość nie jest decyzją: jest funkcją
        ``entry.stage.edition.competition``. Wypełnienie stoi tutaj, a nie wyłącznie
        w ``apps.submissions.services.create_submission``, bo „jedyna droga zapisu” jest prawdą
        o produkcji, a nie o bazie: importy, fabryki testowe i przyszłe serwisy zapisują wprost,
        a praca bez właściciela jest pracą niewidoczną dla własnego koordynatora.

        Kontekstu żądania tu **nie** czytamy i to jest istotne: konkurs pracy wynika z etapu,
        w którym ją oddano, a nie z domeny, spod której ktoś ją zapisuje. Gdyby było odwrotnie,
        zapis wykonany z panelu konkursu A przepisałby pracę konkursu B na A.

        Jedno dodatkowe zapytanie, wyłącznie przy wstawianiu i wyłącznie wtedy, gdy wołający nie
        podał konkursu; ``create_submission`` podaje go z etapu, który i tak ma w ręku, więc na
        ścieżce uploadu nie ma go wcale (istotne dla testów liczby zapytań).
        """
        if self._state.adding and self.competition_id is None and self.entry_id is not None:
            self.competition_id = (
                StageEntry.objects.filter(pk=self.entry_id)
                .values_list("stage__edition__competition_id", flat=True)
                .first()
            )
        return super().save(*args, **kwargs)

    @property
    def latest_file(self) -> "SubmissionFile | None":
        """Najnowszy plik zgłoszenia. W praktyce jest dokładnie jeden, ale kolejność musi być jawna.

        Porządek malejący po ``id`` zamiast domyślnego: jeśli kiedykolwiek pojawi się drugi plik,
        pobranie ma dać ten świeższy, a nie ten, który akurat wypadł pierwszy w ``Meta.ordering``.

        Gdy wywołujący zrobił ``prefetch_related("files")``, sortujemy w Pythonie po gotowej liście.
        ``order_by(...)`` omijałby cache prefetchu i robił po jednym zapytaniu na każdy wiersz listy
        (N+1 w ``GET /api/grading/reviews/`` i ``GET /api/appeals/``).
        """
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache is not None and "files" in cache:
            files = sorted(self.files.all(), key=lambda item: item.pk, reverse=True)
            return files[0] if files else None
        return self.files.order_by("-id").first()

    @property
    def participant(self):
        return self.entry.participant


class SubmissionFile(models.Model):
    """Metadane pliku w prywatnym buckecie. Treść żyje wyłącznie w storage, nigdy w bazie."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="files")
    object_key = models.CharField("klucz obiektu", max_length=512, unique=True)
    sha256 = models.CharField("sha256", max_length=64)
    original_name = models.CharField("nazwa od użytkownika", max_length=255)
    mime = models.CharField("typ MIME", max_length=100)
    size_bytes = models.BigIntegerField("rozmiar (B)")
    av_status = models.CharField(
        "status skanu", max_length=16, choices=AvStatus.choices, default=AvStatus.PENDING
    )
    av_signature = models.CharField("sygnatura wirusa", max_length=200, blank=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    scanned_at = models.DateTimeField("zeskanowany", null=True, blank=True)
    # Liczba stron dokumentu PDF, policzona **po** czystym skanie antywirusowym
    # (``apps.submissions.preview.store_page_count`` wołane z ``apply_scan_verdict``). ``None``
    # znaczy „nie wiadomo”, a nie „zero”: tak wygląda plik innego formatu, plik sprzed
    # wprowadzenia pola i dokument, którego pypdf nie umiał otworzyć. Kolumna istnieje, bo tablica
    # stron leży na końcu pliku – policzenie jej wymaga przeczytania całego dokumentu ze storage,
    # a czyta tę liczbę panel uczestnika przy każdym wejściu.
    page_count = models.PositiveIntegerField("liczba stron", null=True, blank=True)

    #: Przez pracę, czyli przez jej kolumnę denormalizacyjną – jedno złączenie, nie cztery.
    objects = competition_scoped_manager("submission__competition")

    class Meta:
        verbose_name = "plik rozwiązania"
        verbose_name_plural = "pliki rozwiązań"
        ordering = ("id",)

    def __str__(self) -> str:
        return f"{self.object_key} ({self.av_status})"

    @property
    def is_clean(self) -> bool:
        return self.av_status == AvStatus.CLEAN


#: Próg, poniżej którego para w ogóle nie trafia do bazy. Wynik 0,6 dla dwóch niezależnych
#: rozwiązań tego samego zadania jest zwykłym zbiegiem okoliczności (ten sam import, ta sama
#: pętla, ta sama nazwa funkcji z treści zadania), a zapisanie każdej takiej pary zamieniłoby
#: tabelę w iloczyn kartezjański etapu. Próg **pokazywania** jest osobny i wyższy (parametr
#: strony), bo to decyzja czytelnika, a nie reguła przechowywania.
SIMILARITY_STORE_THRESHOLD = 0.6


class SubmissionSimilarity(models.Model):
    """Zmierzone podobieństwo dwóch rozwiązań tego samego zadania – **przesłanka, nie werdykt**.

    Po co w ogóle: przy zadaniach oddawanych jako kod (``py``, ``ipynb``) plagiat wygląda inaczej
    niż przy dowodzie w PDF-ie – wystarczy zmienić nazwy zmiennych i wciąć inaczej, żeby dwa pliki
    przestały być podobne „na oko”, a pozostały identyczne co do struktury. Porównanie maszynowe
    robi to, czego żaden recenzent nie zrobi: zestawia **każdą parę** prac w zadaniu.

    Czego ten wiersz **nie** znaczy: że ktoś ściągał. Wysoki wynik bywa skutkiem wspólnego
    szkieletu z treści zadania albo jednego oczywistego rozwiązania. Dlatego model nie ma pola
    „plagiat”, a jedynie ``reported_at`` – ślad świadomej decyzji koordynatora, że para idzie do
    komitetu. Rozstrzygnięcie zapada poza systemem i wraca do niego jako dyskwalifikacja albo
    korekta oceny, każda z własnym uzasadnieniem.

    Para jest **nieuporządkowana**, ale w bazie zapisujemy ją uporządkowaną po identyfikatorze
    (``submission_a_id < submission_b_id`` – patrz constraint). Bez tego ta sama para zapisana
    dwa razy w odwrotnej kolejności byłaby dla unikalności dwoma różnymi wierszami, a strona
    pokazywałaby ją dwukrotnie.

    ``stage`` jest zdenormalizowany (wynika z ``problem.stage_id``), bo cała strona filtruje po
    etapie, a złączenie do zadania przy każdym odczycie byłoby kosztem bez pożytku. Spójność
    pilnuje serwis, który jako jedyny te wiersze tworzy (``apps.submissions.similarity``).
    """

    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="similarities")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="similarities")
    submission_a = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="similarities_as_a", verbose_name="praca A"
    )
    submission_b = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="similarities_as_b", verbose_name="praca B"
    )
    # ``FloatField`` w zakresie 0–1, a nie procent całkowity: wynik jest wypadkową dwóch miar
    # (Jaccard na shinglach tokenów i ``difflib``), a zaokrąglanie go już przy zapisie zabrałoby
    # możliwość zmiany progu pokazywania bez przeliczania całego etapu.
    score = models.FloatField("podobieństwo")
    computed_at = models.DateTimeField("policzone", default=timezone.now)
    # Zgłoszenie do komitetu. Znacznik czasu, a nie ``BooleanField``: „kiedy” jest tu częścią
    # informacji – para zgłoszona po ogłoszeniu wyników to zupełnie inna sprawa proceduralna niż
    # zgłoszona w trakcie oceniania.
    reported_at = models.DateTimeField("zgłoszona do komitetu", null=True, blank=True)
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_similarities",
        verbose_name="zgłosił",
    )

    #: Przez etap, a nie przez którąś z dwóch prac: para jest nieuporządkowana, więc droga przez
    #: ``submission_a`` byłaby wyborem bez uzasadnienia. ``stage`` jest tu i tak zdenormalizowany,
    #: a obie prace należą do tego samego zadania, czyli do tego samego etapu.
    objects = competition_scoped_manager("stage__edition__competition")

    class Meta:
        verbose_name = "podobieństwo rozwiązań"
        verbose_name_plural = "podobieństwa rozwiązań"
        # Najbardziej podobne na górze – tabela jest listą spraw do obejrzenia, a nie archiwum.
        ordering = ("-score", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "submission_a", "submission_b"],
                name="submissions_similarity_unique_pair",
            ),
            models.CheckConstraint(
                condition=Q(submission_a__lt=F("submission_b")),
                name="submissions_similarity_ordered_pair",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0) & Q(score__lte=1),
                name="submissions_similarity_score_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.submission_a_id}↔{self.submission_b_id}: {self.score:.2f}"

    @property
    def percent(self) -> int:
        """Wynik w procentach – jedyna postać, w jakiej liczba trafia na ekran."""
        return round(self.score * 100)

    @property
    def is_reported(self) -> bool:
        return self.reported_at is not None
