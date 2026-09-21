"""Forum uczestników: tablica ogłoszeń, na której moderacja jest stanem wiersza, a nie obyczajem.

Prośba organizatora z 21.09.2026 brzmi „proste forum dla uczestników, moderowane przez
koordynatora”. Słowo „proste” jest tu ważniejsze niż „forum”: pod adresem siedzą osoby
**niepełnoletnie**, oceny w olimpiadzie są **anonimowe**, a etapy to zawody. Każda z tych trzech
rzeczy zabiera z klasycznego forum jakąś funkcję i to one, a nie oszczędność pracy, wyznaczyły
zakres tego modułu.

Cztery decyzje, na których stoją te modele:

- **moderacja jest kolumną, a nie osobną tabelą zgłoszeń.** ``status`` na wątku i na wpisie
  odpowiada na jedyne pytanie, które zadaje każdy odczyt: „czy to wolno pokazać”. Tabela
  „wpisy oczekujące” obok tabeli wpisów znaczyłaby dwa miejsca, w których żyje ta sama prawda,
  i pierwszy zapomniany ``JOIN`` byłby pokazaniem czegoś, czego nikt nie przeczytał,
- **treść jest tekstem, nie HTML-em, i nie ma załączników.** ``body`` przechodzi przez
  autoescapowanie szablonu i wychodzi przez ``linebreaksbr`` + ``urlize``
  (``apps.forum.templatetags.forum_extras``). Nigdzie nie ma ``|safe`` i nie ma po co go dodawać:
  formatowanie tekstu nie jest warte jednej luki XSS na koncie niepełnoletniego uczestnika,
  a plik wgrany na forum byłby drugą, nieskanowaną drogą wnoszenia plików do serwisu,
- **każdy wiersz niesie konkurs.** Także wpis i zgłoszenie, do których droga wiedzie przez wątek
  i kategorię (``docs/UNIWERSALNY-ETAP-1.md`` § 3.5). Łańcuch ``post__thread__category__competition``
  byłby czterema złączeniami przy każdym odczycie strony wątku, a pierwsze przeoczone zawężenie –
  cudzą rozmową pod naszą domeną,
- **autor zostaje, tożsamość nie.** ``author`` jest ``SET_NULL``: skasowane albo zanonimizowane
  konto ma zostawić rozmowę czytelną, a nie wyciąć z niej co drugi akapit. Kto stoi pod wpisem,
  rozstrzyga **jedna** funkcja (:func:`display_author`) i nie wolno jej obejść w szablonie –
  patrz jej docstring.

Czego tu **nie ma i w wersji pierwszej nie będzie**: prywatnych wiadomości (uczestnicy są
niepełnoletni, a rozmowa bez świadków jest dokładnie tym, czego moderacja nie widzi), polubień
i rankingów (zawody mają już jeden ranking i jest anonimowy), podpisów, awatarów i cytowania
z formatowaniem, a także powiadomień e-mail – decyzja i jej uzasadnienie stoją w
``docs/PODRECZNIK-ORGANIZATORA.md``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.competitions.scoping import competition_scoped_manager

#: Przełącznik konkursu, za którym stoi całe forum (``apps.tenancy.models.FEATURE_DEFAULTS``).
#: Stała, a nie napis powtórzony w sześciu widokach: wyłączone forum ma znaczyć 404 pod **każdym**
#: adresem i brak pozycji w **każdym** menu, a literówka w jednym z tych miejsc byłaby dziurą,
#: która wygląda jak działająca funkcja.
FORUM_FLAG = "participant_forum"

#: Twardy limit długości wpisu. Odrzuca formularz, a nie obcina po cichu – ta sama reguła, co przy
#: zgłoszeniach do organizatora (``apps.support.models.MAX_BODY_LENGTH``). Pięć tysięcy znaków to
#: około dwóch stron maszynopisu: dłuższy tekst na forum przestaje być pytaniem, a zaczyna być
#: rozwiązaniem zadania – czyli dokładnie tym, czego regulamin zabrania (§ 10 ust. 2).
MAX_POST_LENGTH = 5000

#: Limit tematu wątku. Tyle, ile temat zgłoszenia – nagłówek ma się zmieścić w jednym wierszu listy.
MAX_TITLE_LENGTH = 200

#: Limit uzasadnienia zgłoszenia wpisu i notatki moderatora. Krótko, bo oba są zdaniem dla
#: człowieka po drugiej stronie, a nie opisem sprawy.
MAX_REASON_LENGTH = 500

#: Ile minut autor może poprawić swój wpis. Kwadrans wystarcza na literówkę i na dopisanie zdania,
#: którego zabrakło, a jest za krótki, żeby podmienić treść już przeczytaną przez innych – wpis
#: sprzed godziny, który brzmi inaczej niż odpowiedź pod nim, jest gorszy niż wpis z literówką.
EDIT_WINDOW_MINUTES = 15

#: Ile wpisów mieści się na stronie wątku. Dwadzieścia, bo tyle mieści się na ekranie telefonu
#: bez wrażenia, że strona nigdy się nie kończy.
POSTS_PER_PAGE = 20


class ModerationStatus(models.TextChoices):
    """Cztery stany wątku i wpisu – po jednym na każdą odpowiedź, jaką może dać moderacja.

    ``REJECTED`` jest osobno od ``HIDDEN`` i to nie jest podwójna nazwa tego samego: odrzucenie
    jest decyzją **przed** publikacją i autor dostaje razem z nim notatkę, a ukrycie zdejmuje coś,
    co już wisiało, i bywa czynnością autora (usunięcie własnego wpisu), nie moderatora. Zwinięcie
    obu do jednego stanu odbierałoby ekranowi „Twoje wpisy” możliwość powiedzenia, co się stało.
    """

    PENDING = "PENDING", "czeka na moderację"
    PUBLISHED = "PUBLISHED", "opublikowane"
    REJECTED = "REJECTED", "odrzucone"
    HIDDEN = "HIDDEN", "ukryte"


#: Stany, które czekają na koordynatora – to one wyznaczają kolejkę moderacji i odznakę w menu.
#: Jedna definicja, bo kolejka i licznik muszą liczyć to samo (ta sama reguła, co
#: ``apps.support.models.PENDING_STATUSES``).
PENDING_STATUSES = (ModerationStatus.PENDING,)


class ModerationMode(models.TextChoices):
    """Kiedy wpis staje się widoczny dla innych.

    ``PRE`` jest domyślny i to jest decyzja o bezpieczeństwie, a nie o wygodzie: forum bez
    moderatora przy klawiaturze ma być ciche, a nie otwarte. ``POST`` zostaje dla konkursu, który
    ma dyżur moderacyjny i woli rozmowę na żywo – wtedy moderator **ukrywa**, zamiast wpuszczać.
    """

    PRE = "PRE", "przed publikacją"
    POST = "POST", "po publikacji"


#: Podpis pod wpisem osoby, której konta już nie ma (skasowane) albo które przeszło anonimizację
#: (``apps.accounts.profile.anonymise_account`` wyciera imię i nazwisko, a wiersz konta zostawia).
#: Napis, a nie puste miejsce: wpis bez podpisu wyglądałby jak wpis organizatora.
ANONYMISED_AUTHOR_LABEL = "Użytkownik usunięty"


def display_author(user) -> str:
    """Podpis pod wpisem: imię i inicjał nazwiska. **Jedyne** miejsce, w którym powstaje ten napis.

    Reguła jest krótka, a powód długi. Kod publiczny uczestnika (``OLM-…``) jest kluczem
    anonimizacji w ocenianiu: recenzent widzi pracę podpisaną kodem i nie ma prawa dowiedzieć się,
    czyja ona jest. Gdyby forum podpisywało wpisy kodem – albo pokazywało obok imienia szkołę,
    z której da się autora rozpoznać – wystarczyłby jeden wątek „cześć, jestem Ania OLM-XXXXXX”,
    żeby powiązanie kod → osoba stało się publiczne, i anonimowość oceniania przestałaby istnieć
    dla wszystkich naraz. Dlatego na forum nie ma i nie będzie: adresu e-mail, kodu publicznego,
    szkoły ani rocznika – a ``Model.author`` w szablonie nie jest renderowany wprost.

    Konto skasowane (``None``) i konto po anonimizacji (imię i nazwisko wytarte przez
    ``apps.accounts.profile.anonymise_account``) dostają ten sam napis: wpis zostaje, bo jest
    częścią rozmowy, a tożsamości pod nim już nie ma i nie ma jej udawać.

    Odznaka „Komitet”/„Organizator” **nie** wchodzi w skład tego napisu – powstaje osobno
    (``apps.forum.services.author_badge``), bo zależy od roli w konkursie, a nie od danych konta.
    """
    if user is None:
        return ANONYMISED_AUTHOR_LABEL
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if not first:
        return ANONYMISED_AUTHOR_LABEL
    return f"{first} {last[0]}." if last else first


class ForumSettings(models.Model):
    """Ustawienia forum **jednego konkursu**: tryb moderacji i przełącznik „tylko do odczytu”.

    Osobny model, a nie dwa pola na ``tenancy.Competition``, i to jest decyzja świadoma. Tabela
    konkursów jest tabelą, przez którą przechodzi każde żądanie i którą przepisuje migracja
    ``tenancy.0002`` razem ze swoimi testami (``apps/tenancy/tests/test_migration_0002.py``,
    ``apps/core/tests/migration_helpers.py``): dołożenie tam kolumny dla funkcji domyślnie
    wyłączonej znaczyłoby ruszenie wiersza Konkursu #1 i jego złotych testów po to, żeby zapisać
    ustawienie, którego ten konkurs dziś nie używa. Wiersz w osobnej tabeli powstaje dopiero wtedy,
    gdy koordynator pierwszy raz otworzy ekran ustawień – a jego brak jest odpowiedzią pełną
    (patrz ``apps.forum.services.settings_for``), nie brakiem danych.

    ``OneToOne``: „jaki tryb ma forum tego konkursu” jest pytaniem o jedną odpowiedź, a dwa
    wiersze dla jednego konkursu znaczyłyby dwie – i losową między nimi.
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="forum_settings",
        verbose_name="konkurs",
    )
    mode = models.CharField(
        "tryb moderacji",
        max_length=8,
        choices=ModerationMode.choices,
        default=ModerationMode.PRE,
    )
    #: Forum zamknięte na zapis, ale nadal czytelne. Jedno pole zamiast kasowania kategorii: po
    #: zakończeniu edycji rozmowy mają zostać do przeczytania, a nie zniknąć razem z prawem do
    #: pisania.
    is_read_only = models.BooleanField("tylko do odczytu", default=False)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "ustawienia forum"
        verbose_name_plural = "ustawienia forum"

    def __str__(self) -> str:
        return f"Forum: {self.get_mode_display()}"


class ForumCategory(models.Model):
    """Dział forum („Zadania i teoria”, „Organizacja zawodów”). Zakłada je koordynator.

    Kategoria jest **jedynym** miejscem, w którym powstaje wątek, i dlatego ma ``is_open``:
    zamknięcie działu po etapie zostawia go do czytania, a nie do pisania – i robi to jednym
    kliknięciem, bez chodzenia po wątkach.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="forum_categories",
        verbose_name="konkurs",
    )
    name = models.CharField("nazwa", max_length=120)
    #: Slug jest w adresie (``/forum/<slug>/``), bo adres działu bywa przesyłany między
    #: uczestnikami, a numer porządkowy nie mówi, dokąd prowadzi.
    slug = models.SlugField("identyfikator", max_length=60)
    description = models.CharField("opis", max_length=300, blank=True)
    ordering = models.PositiveSmallIntegerField("kolejność", default=100)
    is_open = models.BooleanField("otwarty na nowe wątki", default=True)
    created_at = models.DateTimeField("utworzony", default=timezone.now)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "kategoria forum"
        verbose_name_plural = "kategorie forum"
        ordering = ("ordering", "name", "id")
        constraints = [
            # Slug jest unikalny **w konkursie**, a nie w instalacji: dwie olimpiady mają prawo
            # mieć dział „zadania” i adres rozstrzyga się już po domenie.
            models.UniqueConstraint(
                fields=["competition", "slug"], name="forum_category_slug_per_competition"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ForumThread(models.Model):
    """Wątek: temat rozmowy razem z jej stanem moderacyjnym.

    Wątek ma własny ``status``, a nie dziedziczy go po pierwszym wpisie, bo to dwie różne decyzje:
    „temat nadaje się na forum” i „ten akapit nadaje się do publikacji”. Odrzucony wątek znika
    z listy razem z całą rozmową; odrzucony wpis zostawia wątek w spokoju.

    ``is_pinned`` i ``is_locked`` są **oddzielne od statusu** z tego samego powodu: przypięcie jest
    decyzją o kolejności, a zamknięcie – o prawie do pisania, i żadna z nich nie jest decyzją
    o widoczności.
    """

    #: Własna kolumna mimo drogi przez kategorię (§ 3.5) – uzasadnienie w docstringu modułu.
    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="forum_threads",
        verbose_name="konkurs",
    )
    category = models.ForeignKey(
        ForumCategory, on_delete=models.CASCADE, related_name="threads", verbose_name="kategoria"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_threads",
        verbose_name="autor",
    )
    title = models.CharField("temat", max_length=MAX_TITLE_LENGTH)
    created_at = models.DateTimeField("założony", default=timezone.now)
    #: Znacznik ostatniej **opublikowanej** wypowiedzi – po nim sortuje się lista wątków. Kolumna,
    #: a nie ``MAX(post.created_at)`` w podzapytaniu: lista działu pokazuje kilkadziesiąt wątków,
    #: a agregat po wpisach byłby złączeniem po najgrubszej tabeli forum przy każdym wejściu.
    last_activity_at = models.DateTimeField("ostatnia aktywność", default=timezone.now)
    is_pinned = models.BooleanField("przypięty", default=False)
    is_locked = models.BooleanField("zamknięty", default=False)
    status = models.CharField(
        "stan", max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.PENDING
    )
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_moderated_threads",
        verbose_name="moderował",
    )
    moderated_at = models.DateTimeField("moderowany", null=True, blank=True)
    moderation_note = models.CharField("uzasadnienie moderatora", max_length=MAX_REASON_LENGTH, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "wątek forum"
        verbose_name_plural = "wątki forum"
        # Przypięte na górze, dalej od ostatniej wypowiedzi – to jest kolejność listy działu
        # i jest regułą, a nie ustawieniem sortowania.
        ordering = ("-is_pinned", "-last_activity_at", "-id")
        indexes = [
            models.Index(fields=["category", "status", "-last_activity_at"], name="forum_thread_list_idx"),
            models.Index(fields=["competition", "status"], name="forum_thread_queue_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.status == ModerationStatus.PUBLISHED

    @property
    def is_pending(self) -> bool:
        return self.status == ModerationStatus.PENDING


class ForumPost(models.Model):
    """Pojedyncza wypowiedź w wątku. Tekst i nic poza tekstem.

    ``edited_at`` jest pustą datą do pierwszej poprawki i to jest cała jego treść: wpis poprawiony
    ma być **widocznie** poprawiony. Historia wersji tu nie stoi – forum uczestników nie jest
    dokumentem zawodów, a pierwsze kilkanaście minut po napisaniu i tak zamyka okno zmian
    (:data:`EDIT_WINDOW_MINUTES`).
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="forum_posts",
        verbose_name="konkurs",
    )
    thread = models.ForeignKey(
        ForumThread, on_delete=models.CASCADE, related_name="posts", verbose_name="wątek"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_posts",
        verbose_name="autor",
    )
    body = models.TextField("treść", max_length=MAX_POST_LENGTH)
    created_at = models.DateTimeField("dodany", default=timezone.now)
    edited_at = models.DateTimeField("poprawiony", null=True, blank=True)
    status = models.CharField(
        "stan", max_length=16, choices=ModerationStatus.choices, default=ModerationStatus.PENDING
    )
    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_moderated_posts",
        verbose_name="moderował",
    )
    moderated_at = models.DateTimeField("moderowany", null=True, blank=True)
    moderation_note = models.CharField("uzasadnienie moderatora", max_length=MAX_REASON_LENGTH, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "wpis forum"
        verbose_name_plural = "wpisy forum"
        ordering = ("created_at", "id")
        indexes = [
            models.Index(fields=["thread", "status", "created_at"], name="forum_post_thread_idx"),
            models.Index(fields=["author", "-created_at"], name="forum_post_author_idx"),
            models.Index(fields=["competition", "status"], name="forum_post_queue_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.thread_id}: {self.body[:40]}"

    @property
    def is_published(self) -> bool:
        return self.status == ModerationStatus.PUBLISHED

    @property
    def is_pending(self) -> bool:
        return self.status == ModerationStatus.PENDING

    @property
    def is_rejected(self) -> bool:
        return self.status == ModerationStatus.REJECTED

    def editable_until(self):
        """Do kiedy autor może poprawić ten wpis. Jedno miejsce reguły – czyta je widok i szablon."""
        from datetime import timedelta

        return self.created_at + timedelta(minutes=EDIT_WINDOW_MINUTES)


class ForumReport(models.Model):
    """„Zgłoś wpis”: ktoś uważa, że ten akapit nie powinien tu stać.

    Zgłoszenie jest osobnym wierszem, a nie polem na wpisie, bo dwie osoby potrafią zgłosić ten sam
    wpis z dwóch różnych powodów, a moderator ma zobaczyć oba. ``resolved_at`` puste znaczy
    „czeka”; rozpatrzenie zgłoszenia **nie** jest tym samym, co ukrycie wpisu – moderator bywa
    innego zdania niż zgłaszający i to też jest rozpatrzeniem.
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="forum_reports",
        verbose_name="konkurs",
    )
    post = models.ForeignKey(ForumPost, on_delete=models.CASCADE, related_name="reports", verbose_name="wpis")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_reports",
        verbose_name="zgłaszający",
    )
    reason = models.CharField("powód", max_length=MAX_REASON_LENGTH)
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="forum_resolved_reports",
        verbose_name="rozpatrzył",
    )
    resolved_at = models.DateTimeField("rozpatrzone", null=True, blank=True)

    objects = competition_scoped_manager("competition")

    class Meta:
        verbose_name = "zgłoszenie wpisu"
        verbose_name_plural = "zgłoszenia wpisów"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["competition", "resolved_at"], name="forum_report_queue_idx"),
        ]

    def __str__(self) -> str:
        return f"zgłoszenie wpisu #{self.post_id}"

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None
