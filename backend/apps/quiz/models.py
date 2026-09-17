"""Model testu online sprawdzanego automatycznie: test, pytanie, wariant, podejście, odpowiedź.

Test jest **własnością etapu** (``Quiz.stage`` jest jeden-do-jednego), a nie edycji ani olimpiady.
To nie jest szczegół techniczny: planowany jest podział serwisu na wiele konkursów naraz, a jedyną
rzeczą, która przetrwa taki podział bez przenumerowania, jest etap. Wszystko, co niżej wskazuje na
zawody, wskazuje więc na ``Stage`` albo na ``StageEntry`` (udział konkretnej osoby w konkretnym
etapie) – nigdy na ``Participant`` wprost. Dzięki temu ta sama osoba może kiedyś rozwiązywać dwa
różne testy w dwóch różnych konkursach i nie będzie to ta sama encja „uczestnik testu”.

Zasady wspólne z resztą domeny (patrz ``apps.competitions.models``):

- czas zawsze przez ``django.utils.timezone.now()``,
- reguła integralności stoi jednocześnie w ``clean()`` (czytelny komunikat pod polem) i w bazie
  (constraint – ostatnia linia obrony przed zapisem z pominięciem ``full_clean()``),
- dostęp do danych wyłącznie przez ORM.

Dwie decyzje, które warto wyjaśnić od razu, bo wracają w każdej warstwie wyżej:

**Punkty są ``Decimal``, nie ``float``.** Ocena cząstkowa pytania wielokrotnego wyboru to ułamek
(„3 z 4 poprawnych wariantów”), a wynik testu bywa podstawą kwalifikacji do następnego etapu.
Suma liczb zmiennoprzecinkowych potrafi dać 5.999999999999999 tam, gdzie dwie osoby mają
ten sam zestaw odpowiedzi – a tego nie da się wytłumaczyć nikomu, kto zobaczy się na liście
o jedno miejsce niżej.

**Treść pytania jest zwykłym tekstem, nie HTML-em.** Formalnie zadanie mówiło „rich text”, ale
redaktor rich-textowy Wagtaila żyje w ``/cms/``, a nie w panelu koordynatora, i wpuszczenie tu
HTML-a znaczyłoby przechowywanie znaczników, które później trzeba renderować przez ``|safe``.
Test rozwiązuje uczestnik zalogowany, a treść pisze koordynator – ale to i tak jest jedna osoba
pisząca kod HTML, który wykona się w przeglądarce kilkuset innych. Tekst z zachowanymi złamaniami
wierszy (``|linebreaksbr`` w szablonie) zapisuje wszystko, czego potrzebuje pytanie testowe,
i nie otwiera tej furtki. Ilustracja, której tekst nie zastąpi, ma osobne pole ``image``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.competitions.models import Stage, StageEntry
from apps.competitions.storage import private_media_storage

#: Domyślny czas trwania jednego podejścia w minutach. Wartość organizatora dla testu
#: eliminacyjnego – nie jest regułą, tylko punktem wyjścia w formularzu.
DEFAULT_DURATION_MINUTES = 30

#: Ile sekund po ``QuizAttempt.deadline_at`` serwer nadal przyjmie zapis odpowiedzi. Ta tolerancja
#: nie jest przedłużeniem testu, tylko uznaniem, że między kliknięciem „Zakończ” a dotarciem
#: żądania na serwer upływa czas: wolne łącze w szkole, uśpiony telefon, ponowienie po zerwaniu
#: połączenia. Bez niej ktoś, kto kliknął o czasie, dostawał odmowę za cudzy problem z siecią.
#: Odpowiedź przyjęta w tym oknie liczy się normalnie; po nim podejście jest już przeterminowane.
SUBMIT_GRACE_SECONDS = 30

#: Maksymalna liczba punktów za jedno pytanie. Granica jest po to, żeby literówka w formularzu
#: („10” wpisane jako „100”) nie przestawiła po cichu proporcji całego testu.
MAX_QUESTION_POINTS = Decimal("100")


class ShowResultsAfter(models.TextChoices):
    """Kiedy uczestnik zobaczy swój wynik – i dlaczego to musi być decyzja koordynatora.

    Trzy tryby, bo test online bywa trzema różnymi rzeczami naraz:

    - ``IMMEDIATELY`` – test sprawdzający wiedzę „tu i teraz” (trening, quiz otwarty). Natychmiastowy
      wynik jest wtedy całą wartością ćwiczenia,
    - ``AFTER_CLOSE`` – zawody. Wynik pokazany od razu pierwszej osobie jest wynikiem, który ta
      osoba może przekazać dalej razem z treściami pytań, więc czeka do zamknięcia okna testu,
    - ``NEVER`` – etap, którego punkty wchodzą do wspólnej tabeli wyników i mają być ogłoszone
      razem z nią (``apps.results.services.publish_results``), a nie wcześniej i osobno.
    """

    NEVER = "NEVER", "nigdy (wynik tylko w tabeli wyników etapu)"
    AFTER_CLOSE = "AFTER_CLOSE", "po zamknięciu testu"
    IMMEDIATELY = "IMMEDIATELY", "od razu po zakończeniu podejścia"


class NegativeFloor(models.TextChoices):
    """Gdzie zatrzymuje się odejmowanie punktów ujemnych – na pytaniu czy na całym teście.

    Punkty ujemne za błędną odpowiedź mają zniechęcać do zgadywania, a nie karać za przystąpienie.
    Bez podłogi test z dziesięcioma pytaniami po −1 potrafi dać wynik −10, a taki wynik nie znaczy
    nic więcej niż zero: obie osoby nie umiały nic, a jedna z nich dodatkowo spróbowała.

    - ``QUESTION`` (domyślnie) – każde pytanie osobno nie schodzi poniżej zera. Błąd w jednym
      pytaniu nie zabiera punktów zdobytych w innym. To jest wariant „ujemne punkty karzą za
      zgadywanie **w tym** pytaniu”,
    - ``QUIZ`` – ujemne wolno przenosić między pytaniami, a podłogą jest dopiero suma testu.
      Tak działają arkusze, w których zgadywanie ma kosztować naprawdę; wybór zostaje przy
      koordynatorze, bo to decyzja regulaminowa, a nie techniczna.
    """

    QUESTION = "QUESTION", "zero za pytanie (ujemne nie schodzą poniżej zera w pytaniu)"
    QUIZ = "QUIZ", "zero za test (ujemne przenoszą się między pytaniami)"


class QuestionKind(models.TextChoices):
    """Rodzaj pytania – i zarazem rodzaj sprawdzania, jakiemu podlega odpowiedź.

    Rodzajów jest cztery i ani jeden z nich nie wymaga człowieka do sprawdzenia. To jest cała
    granica tej funkcji: pytanie otwarte, które ktoś musi przeczytać, jest **zadaniem** i idzie
    zwykłą ścieżką (``apps.submissions`` → recenzje), a nie testem.
    """

    SINGLE_CHOICE = "SINGLE_CHOICE", "jednokrotny wybór"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "wielokrotny wybór"
    SHORT_TEXT = "SHORT_TEXT", "krótka odpowiedź tekstowa"
    NUMERIC = "NUMERIC", "odpowiedź liczbowa"

    @classmethod
    def choice_kinds(cls) -> tuple[str, ...]:
        """Rodzaje, które mają warianty odpowiedzi (``QuizOption``).

        Pytane w kilku miejscach naraz (walidacja, edytor, losowanie kolejności wariantów),
        więc lista stoi raz tutaj, a nie jako powtórzone ``in (…, …)``.
        """
        return (cls.SINGLE_CHOICE, cls.MULTIPLE_CHOICE)


class PartialCredit(models.TextChoices):
    """Jak liczyć pytanie wielokrotnego wyboru rozwiązane częściowo."""

    ALL_OR_NOTHING = "ALL_OR_NOTHING", "wszystko albo nic"
    PROPORTIONAL = "PROPORTIONAL", "proporcjonalnie do trafionych wariantów"


class AttemptStatus(models.TextChoices):
    """Stan podejścia. Przejścia są jednokierunkowe: ``IN_PROGRESS`` → ``SUBMITTED`` albo ``EXPIRED``.

    ``EXPIRED`` **nie** znaczy „zero punktów”. Znaczy „czas minął, zanim uczestnik kliknął
    Zakończ” – i wtedy liczą się odpowiedzi, które zdążyły się zapisać. Inaczej autozapis co
    dwadzieścia sekund byłby obietnicą bez pokrycia, a zerwane łącze na ostatnim pytaniu kasowałoby
    całą pracę.
    """

    IN_PROGRESS = "IN_PROGRESS", "w trakcie"
    SUBMITTED = "SUBMITTED", "zakończone"
    EXPIRED = "EXPIRED", "czas minął"


class Quiz(models.Model):
    """Test online jednego etapu: ustawienia, okno i pula pytań.

    Relacja z etapem jest jeden-do-jednego, bo „etap w formie testu” ma dokładnie jeden arkusz –
    tak samo, jak etap pisemny ma jeden zestaw zadań. Gdyby testów na etap mogło być kilka,
    pierwszym pytaniem uczestnika byłoby „który mam rozwiązać”, a pierwszym pytaniem tabeli
    wyników – „który liczymy”. Losowanie **zestawów** robi się natomiast wewnątrz jednego testu
    (pule pytań, ``questions_per_attempt``), a nie przez kilka testów obok siebie.
    """

    stage = models.OneToOneField(Stage, on_delete=models.CASCADE, related_name="quiz", verbose_name="etap")
    title = models.CharField("tytuł", max_length=160)
    # Instrukcja czytana **przed** startem, na stronie z regulaminem podejścia – nie w trakcie.
    # Pusta jest dopuszczalna: strona startowa i tak wypisuje czas trwania, liczbę podejść
    # i zasady punktowania z ustawień, więc test bez własnej instrukcji nadal się tłumaczy.
    instructions = models.TextField("instrukcja", blank=True)
    duration_minutes = models.PositiveSmallIntegerField(
        "czas trwania (minuty)",
        default=DEFAULT_DURATION_MINUTES,
        validators=[MinValueValidator(1)],
        help_text="Licznik jednego podejścia. Koniec okna testu i tak zamyka podejście wcześniej.",
    )
    # Okno testu **domyślnie jest oknem etapu** i puste pole właśnie to znaczy (patrz ``window``).
    # Osobne pola są tu dlatego, że test bywa krótszym wycinkiem etapu: etap stacjonarny trwa cztery
    # dni, a sesja testowa dwie godziny drugiego dnia. Kopiowanie wtedy dat etapu do testu byłoby
    # zapisaniem tej samej informacji dwa razy – i rozjechałoby się przy pierwszej zmianie terminu.
    opens_at = models.DateTimeField("otwarcie testu", null=True, blank=True)
    closes_at = models.DateTimeField("zamknięcie testu", null=True, blank=True)
    attempts_allowed = models.PositiveSmallIntegerField(
        "liczba podejść", default=1, validators=[MinValueValidator(1)]
    )
    shuffle_questions = models.BooleanField("losowa kolejność pytań", default=False)
    shuffle_options = models.BooleanField("losowa kolejność wariantów", default=False)
    # Losowanie zestawu: ile pytań ciągnąć **z każdej puli**. Puste = wszystkie pytania testu.
    #
    # Dlaczego „z każdej puli”, a nie „tyle pytań w sumie”: pula jest tematem („kinematyka”,
    # „stany splątane”), a arkusz ma sprawdzić każdy z tych tematów. Losowanie z jednego worka
    # potrafiłoby wyciągnąć komuś pięć pytań z kinematyki i ani jednego ze splątania, czyli dać
    # dwóm osobom nieporównywalne testy przy tej samej liczbie pytań.
    questions_per_attempt = models.PositiveSmallIntegerField(
        "pytań z każdej puli",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Puste = wszystkie pytania. Wpisana liczba = tyle pytań losowanych z każdej puli.",
    )
    show_results_after = models.CharField(
        "pokaż wynik",
        max_length=16,
        choices=ShowResultsAfter.choices,
        default=ShowResultsAfter.AFTER_CLOSE,
    )
    negative_floor = models.CharField(
        "podłoga punktów ujemnych",
        max_length=16,
        choices=NegativeFloor.choices,
        default=NegativeFloor.QUESTION,
    )
    created_at = models.DateTimeField("utworzony", default=timezone.now)
    updated_at = models.DateTimeField("zmieniony", auto_now=True)

    class Meta:
        verbose_name = "test online"
        verbose_name_plural = "testy online"
        ordering = ("stage", "id")
        constraints = [
            # Okno o zerowej albo ujemnej długości nigdy nie jest otwarte, a interfejs pokazywałby
            # wtedy zapowiedź testu, do którego i tak nie da się przystąpić. Przy pustych datach
            # porównanie daje NULL, więc test dziedziczący okno etapu przechodzi.
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True)
                | Q(closes_at__isnull=True)
                | Q(opens_at__lt=models.F("closes_at")),
                name="quiz_quiz_window_ordered",
            ),
            models.CheckConstraint(condition=Q(duration_minutes__gte=1), name="quiz_quiz_duration_positive"),
            models.CheckConstraint(condition=Q(attempts_allowed__gte=1), name="quiz_quiz_attempts_positive"),
            models.CheckConstraint(
                condition=Q(questions_per_attempt__isnull=True) | Q(questions_per_attempt__gte=1),
                name="quiz_quiz_draw_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.stage_id})"

    def clean(self) -> None:
        # Sam ``closes_at`` bez ``opens_at`` jest poprawny (test otwarty razem z etapem, zamykany
        # wcześniej) i odwrotnie – ale odwrócona para nie jest oknem, tylko pomyłką w formularzu.
        if self.opens_at and self.closes_at and self.opens_at >= self.closes_at:
            raise ValidationError({"closes_at": "Zamknięcie testu musi być późniejsze niż otwarcie."})

    @property
    def window(self) -> tuple[datetime, datetime]:
        """Okno, w którym wolno rozpocząć podejście: własne terminy testu albo terminy etapu.

        Jedno miejsce, w którym zapisana jest reguła „puste pole znaczy: tak jak etap”. Widoki,
        serwis startu podejścia i strona startowa pytają **tutaj**, bo inaczej każde z nich
        miałoby własne ``or stage.opens_at`` i wystarczyłoby, żeby jedno z nich zostało pominięte
        przy zmianie, aby przycisk „Rozpocznij” pokazywał się poza oknem testu.
        """
        return (self.opens_at or self.stage.opens_at, self.closes_at or self.stage.deadline_at)

    def is_open(self, now=None) -> bool:
        """Czy w tej chwili wolno **rozpocząć** podejście (to nie to samo, co „trwa podejście”)."""
        now = now or timezone.now()
        opens, closes = self.window
        return opens <= now < closes

    @property
    def pools(self) -> dict[str, list[QuizQuestion]]:
        """Pytania pogrupowane w pule, w kolejności edytora. Pusta nazwa puli = pula domyślna."""
        grouped: dict[str, list[QuizQuestion]] = {}
        for question in self.questions.all():
            grouped.setdefault(question.pool, []).append(question)
        return grouped

    @property
    def draw_size(self) -> int:
        """Ile pytań dostanie jedno podejście. Bez losowania – wszystkie pytania testu."""
        pools = self.pools
        if self.questions_per_attempt is None:
            return sum(len(items) for items in pools.values())
        return sum(min(self.questions_per_attempt, len(items)) for items in pools.values())

    @property
    def max_points(self) -> Decimal:
        """Górne ograniczenie wyniku jednego podejścia.

        Bez losowania jest to po prostu suma punktów wszystkich pytań. Przy losowaniu jest to
        **ograniczenie górne**, a nie wartość dokładna: gdy pytania w jednej puli są warte różnie,
        o maksimum decyduje to, co się wylosowało. Dlatego liczymy najdroższy możliwy zestaw
        (najwyżej punktowane pytania każdej puli) – i dlatego edytor testu ostrzega przy puli
        z nierównymi punktami (``apps.quiz.services.draw_warnings``). Wartość **dokładna** dla
        konkretnej osoby stoi w ``QuizAttempt.max_points``, wyliczona z wylosowanego zestawu; to
        ona jest podstawą procentu pokazywanego uczestnikowi.
        """
        total = Decimal("0")
        for items in self.pools.values():
            points = sorted((question.points for question in items), reverse=True)
            drawn = len(points) if self.questions_per_attempt is None else self.questions_per_attempt
            total += sum(points[:drawn], Decimal("0"))
        return total


class QuizQuestion(models.Model):
    """Pojedyncze pytanie testu wraz z kluczem odpowiedzi.

    Klucz odpowiedzi mieszka w dwóch miejscach zależnie od rodzaju pytania: dla wyboru jest nim
    ``QuizOption.is_correct``, dla odpowiedzi tekstowej i liczbowej – ``settings``. Rozdział jest
    celowy: warianty wyboru są **treścią widoczną** dla uczestnika i muszą mieć własne wiersze
    (kolejność, losowanie, statystyka trafień), a klucz do pytania otwartego jest wyłącznie
    danymi sprawdzania i nigdy nie opuszcza serwera.

    Kształt ``settings`` per rodzaj opisuje ``apps.quiz.grading`` – tam też stoi walidacja, bo to
    ten sam moduł, który później te dane czyta. Model sprawdza jedynie to, czego bez bazy sprawdzić
    się nie da (np. czy pytanie wyboru ma w ogóle poprawny wariant) – patrz ``apps.quiz.services``.
    """

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions", verbose_name="test")
    # Pula tematyczna. Pusta nazwa to pula domyślna i taki jest stan wyjściowy każdego testu –
    # dopóki koordynator nie podzieli pytań na tematy, losowanie ciągnie z jednego worka.
    pool = models.CharField("pula", max_length=40, blank=True)
    order = models.PositiveSmallIntegerField("kolejność", default=0)
    kind = models.CharField(
        "rodzaj", max_length=20, choices=QuestionKind.choices, default=QuestionKind.SINGLE_CHOICE
    )
    text = models.TextField("treść")
    # Ilustracja pytania idzie na **prywatny** storage, tym samym aliasem, co treści zadań
    # (``apps.competitions.storage``). Publiczny bucket dałby adres, spod którego dałoby się
    # pobrać obrazek pytania przed otwarciem testu – a wtedy losowanie zestawów przestaje
    # cokolwiek chronić, bo pula pytań wycieka obrazek po obrazku.
    image = models.ImageField(
        "ilustracja",
        upload_to="quiz-questions/",
        storage=private_media_storage,
        null=True,
        blank=True,
    )
    points = models.DecimalField("punkty", max_digits=6, decimal_places=2, default=Decimal("1"))
    # Punkty odejmowane za odpowiedź błędną. Dodatnia liczba **odejmowana** (nie ujemna dodawana):
    # w formularzu koordynator pisze „ile karzemy”, a nie „jaki jest ujemny wynik”, i to jest
    # różnica między „1” a „-1” wpisanym przez pomyłkę bez minusa.
    negative_points = models.DecimalField(
        "punkty ujemne za błąd", max_digits=6, decimal_places=2, default=Decimal("0")
    )
    settings = models.JSONField("ustawienia sprawdzania", default=dict, blank=True)

    class Meta:
        verbose_name = "pytanie testu"
        verbose_name_plural = "pytania testu"
        ordering = ("quiz", "pool", "order", "id")
        constraints = [
            # Pytanie za zero punktów jest tekstem bez wpływu na wynik, a pytanie za 100 punktów
            # w teście z dziesięcioma pytaniami jest zwykle literówką.
            models.CheckConstraint(
                condition=Q(points__gt=0) & Q(points__lte=MAX_QUESTION_POINTS),
                name="quiz_question_points_in_range",
            ),
            # Kara większa od nagrody znaczy, że odpowiedź błędna kosztuje więcej, niż poprawna
            # daje – przy podłodze „na teście” wystarczyłoby kilka takich pytań, żeby wynik
            # przestał zależeć od wiedzy.
            models.CheckConstraint(
                condition=Q(negative_points__gte=0) & Q(negative_points__lte=models.F("points")),
                name="quiz_question_negative_within_points",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order}. {self.text[:48]}"

    @property
    def has_options(self) -> bool:
        """Czy pytanie ma warianty do wyświetlenia (a więc i do przetasowania)."""
        return self.kind in QuestionKind.choice_kinds()


class QuizOption(models.Model):
    """Wariant odpowiedzi pytania wyboru. ``is_correct`` nigdy nie trafia do HTML-a uczestnika."""

    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="options", verbose_name="pytanie"
    )
    order = models.PositiveSmallIntegerField("kolejność", default=0)
    text = models.CharField("treść", max_length=500)
    is_correct = models.BooleanField("poprawny", default=False)

    class Meta:
        verbose_name = "wariant odpowiedzi"
        verbose_name_plural = "warianty odpowiedzi"
        ordering = ("question", "order", "id")

    def __str__(self) -> str:
        return self.text[:48]


class QuizAttempt(models.Model):
    """Jedno podejście jednej osoby do testu – wraz z **wylosowanym dla niej** zestawem.

    ``question_order`` jest tym, co czyni losowanie uczciwym: zestaw i kolejność wariantów są
    losowane **raz**, przy starcie, i od tej chwili są faktem zapisanym w bazie. Gdyby losowanie
    biegło przy każdym wejściu na stronę, odświeżenie przeglądarki podmieniałoby pytania pod
    ręką – a uczestnik z gorszym łączem (czyli częściej przeładowujący) dostawałby inny test niż
    reszta. Zapisany porządek jest też jedynym powodem, dla którego odtworzenie podejścia
    z historii odpowiedzi jest w ogóle możliwe.

    Podejście wiąże się ze ``StageEntry``, a nie z ``Participant``: punkty z testu wchodzą do
    wyników **etapu**, a wpis do etapu jest tym, co te wyniki zbiera (patrz
    ``apps.quiz.services.stage_scores``).
    """

    #: Klucze w ``question_order``. Nazwy w jednym miejscu, bo czyta je serwis, szablon przez
    #: kontekst i test – literał powtórzony w trzech miejscach rozjeżdża się po pierwszej zmianie.
    ORDER_QUESTIONS = "questions"
    ORDER_OPTIONS = "options"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts", verbose_name="test")
    entry = models.ForeignKey(
        StageEntry, on_delete=models.CASCADE, related_name="quiz_attempts", verbose_name="wpis do etapu"
    )
    started_at = models.DateTimeField("rozpoczęte", default=timezone.now)
    # Termin **tego** podejścia, policzony przy starcie z czasu trwania testu i końca okna – ten
    # z nich, który wypada wcześniej. Pole jest zapisane, a nie liczone w locie, bo od chwili
    # startu jest obietnicą złożoną uczestnikowi: skrócenie czasu trwania testu w trakcie sesji
    # nie może zabrać minut komuś, kto już pisze.
    deadline_at = models.DateTimeField("koniec podejścia")
    submitted_at = models.DateTimeField("zakończone", null=True, blank=True)
    question_order = models.JSONField("wylosowany zestaw", default=dict, blank=True)
    score = models.DecimalField("wynik", max_digits=7, decimal_places=2, null=True, blank=True)
    # Maksimum **tego** zestawu. Zapisane razem z wynikiem, bo procent pokazywany uczestnikowi ma
    # zostać ten sam także wtedy, gdy koordynator później dołoży do puli kolejne pytania.
    max_points = models.DecimalField(
        "maksimum zestawu", max_digits=7, decimal_places=2, null=True, blank=True
    )
    status = models.CharField(
        "status", max_length=16, choices=AttemptStatus.choices, default=AttemptStatus.IN_PROGRESS
    )
    graded_at = models.DateTimeField("ocenione", null=True, blank=True)
    # Adres i przeglądarka podejścia. To nie jest analityka: przy sporze o przebieg testu
    # („nie mogłem się połączyć”, „to nie ja wysłałem”) są to jedyne dwa fakty techniczne, jakimi
    # dysponuje organizator. Adres jest daną osobową, więc znika razem z kontem uczestnika
    # (``on_delete=CASCADE`` przez ``StageEntry``) i nie jest nigdzie pokazywany poza panelem.
    ip = models.GenericIPAddressField("adres IP", null=True, blank=True)
    user_agent = models.CharField("przeglądarka", max_length=255, blank=True)

    class Meta:
        verbose_name = "podejście do testu"
        verbose_name_plural = "podejścia do testu"
        ordering = ("-started_at", "id")
        constraints = [
            # **Jedno aktywne podejście** na osobę i test. Bez tego dwie karty przeglądarki
            # otwierały dwa podejścia naraz, a uczestnik oddawał to, w którym poszło lepiej –
            # czyli miał dwa podejścia przy ``attempts_allowed = 1``. Częściowy indeks, bo
            # podejść zakończonych wolno mieć wiele.
            models.UniqueConstraint(
                fields=["quiz", "entry"],
                condition=Q(status=AttemptStatus.IN_PROGRESS),
                name="quiz_attempt_single_active",
            ),
            models.CheckConstraint(
                condition=Q(deadline_at__gt=models.F("started_at")),
                name="quiz_attempt_deadline_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entry_id} @ {self.quiz_id} ({self.status})"

    @property
    def is_open(self) -> bool:
        """Czy podejście wciąż przyjmuje odpowiedzi (stan, nie czas – czas sprawdza serwis)."""
        return self.status == AttemptStatus.IN_PROGRESS

    def accepts_answers_at(self, now=None) -> bool:
        """Czy o tej chwili wolno jeszcze zapisać odpowiedź – łącznie z tolerancją sieciową.

        Jedyne miejsce, w którym ``SUBMIT_GRACE_SECONDS`` są doliczane. Widok autozapisu i widok
        zakończenia pytają tutaj obydwa, bo obie ścieżki muszą mieć **tę samą** granicę: gdyby
        „Zakończ” był łagodniejszy od autozapisu, opłacałoby się nie zapisywać nic aż do końca.
        """
        now = now or timezone.now()
        return self.is_open and now <= self.deadline_at + timedelta(seconds=SUBMIT_GRACE_SECONDS)

    @property
    def drawn_question_ids(self) -> list[int]:
        """Identyfikatory pytań w kolejności wylosowanej przy starcie."""
        return [int(value) for value in self.question_order.get(self.ORDER_QUESTIONS, [])]

    def option_order_for(self, question_id: int) -> list[int]:
        """Kolejność wariantów zapisana dla tego pytania. Pusta lista = kolejność z edytora."""
        options = self.question_order.get(self.ORDER_OPTIONS, {})
        return [int(value) for value in options.get(str(question_id), [])]


class QuizAnswer(models.Model):
    """Odpowiedź na jedno pytanie w jednym podejściu – razem z tym, co dało jej sprawdzenie.

    Jeden wiersz na pytanie, nadpisywany przy każdym autozapisie (``unique_together``), a nie
    historia zmian: przedmiotem oceny jest stan z chwili zakończenia, a trzymanie wszystkich
    pośrednich kliknięć byłoby zapisem tego, jak ktoś się zastanawiał. ``payload`` ma kształt
    zależny od rodzaju pytania i opisuje go ``apps.quiz.grading``.

    ``is_correct`` i ``points_awarded`` są puste do chwili sprawdzenia i wypełnia je
    ``grade_attempt``. Puste **nie** znaczy „zero punktów”, tylko „jeszcze nie sprawdzono” –
    stąd ``null``, a nie domyślne zero.
    """

    attempt = models.ForeignKey(
        QuizAttempt, on_delete=models.CASCADE, related_name="answers", verbose_name="podejście"
    )
    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="answers", verbose_name="pytanie"
    )
    payload = models.JSONField("odpowiedź", default=dict, blank=True)
    is_correct = models.BooleanField("poprawna", null=True, blank=True)
    points_awarded = models.DecimalField(
        "przyznane punkty", max_digits=6, decimal_places=2, null=True, blank=True
    )
    updated_at = models.DateTimeField("zapisana", auto_now=True)

    class Meta:
        verbose_name = "odpowiedź w teście"
        verbose_name_plural = "odpowiedzi w teście"
        ordering = ("attempt", "question")
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="quiz_answer_unique_per_question"),
        ]

    def __str__(self) -> str:
        return f"{self.attempt_id}/{self.question_id}"
