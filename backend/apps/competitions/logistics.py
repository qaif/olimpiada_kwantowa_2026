"""Logistyka etapu stacjonarnego: miejsce zawodów, deklaracja przyjazdu, obecność.

Cały moduł stoi za flagą ``onsite_logistics`` (``docs/UNIWERSALNY-ETAP-2.md`` § 0.6, § 1.5.2)
i Konkurs #1 jej nie włącza: bez flagi nie powstaje ani jeden wiersz, nie pada ani jedno zapytanie
i nie ma czego pokazać na żadnym ekranie. Tabele przybywają w bazie, ale puste – to jest cała
cena, którą płaci instalacja jednokonkursowa.

**Dlaczego osobny moduł, a nie ``models.py``.** Domena zawodów opisuje dziś świat fizyczny jednym
polem (``Stage.location`` – wolny tekst) i jedną notatką (``EditionEvent.note``). Nocleg, posiłek,
sala i lista obecności to **inna dziedzina** niż punktacja i kwalifikacja: zmienia się z innego
powodu (regulamin pobytu, a nie regulamin zawodów) i czyta ją inna osoba (organizator pobytu,
a nie komitet oceniający). ``models.py`` rejestruje te modele jedną linią importu na końcu pliku –
tak samo jak ``apps.accounts.twofactor`` (``apps/accounts/models.py``), i z tego samego powodu:
bez importu ``makemigrations`` nie zobaczyłby tabel.

**Dane szczególne (decyzja organizatora D21, § 6).** „Dieta bezglutenowa” i „potrzebuję pokoju
na parterze” bywają danymi o zdrowiu (art. 9 RODO). Organizator rozstrzygnął, że **domyślnie ich
nie zbieramy**, a włączenie jest osobną, świadomą decyzją – dlatego:

- sama flaga ``onsite_logistics`` **nie wystarcza**: potrzeby szczególne (``DIET``,
  ``ACCESSIBILITY``) i pole tekstowe ``ArrivalForm.note`` pojawiają się dopiero, gdy konkurs ma
  wiersz :class:`LogisticsSettings` z ``collect_special_needs=True``. Przełącznik jest **wierszem
  konfiguracji konkursu**, a nie flagą z katalogu § 0.6, bo katalog jest zamknięty i należy do
  jednego zadania (T8) – a decyzja D21 dotyczy jednego pola, nie obszaru,
- przy wyłączonym zbieraniu ``needs`` jest **filtrowane** (dwie wartości szczególne nie przechodzą
  przez :func:`save_arrival_form`), a ``note`` jest zapisywane jako puste. Nie ma drogi, którą taka
  dana wpadłaby do bazy „bokiem”,
- ``note`` ma 500 znaków i etykietę mówiącą wprost, czego **nie** wpisywać
  (:data:`SPECIAL_NEEDS_WARNING`),
- dostęp ma wyłącznie koordynator: żadna funkcja tego modułu nie oddaje ``note`` recenzentowi ani
  opiekunowi, a ``note`` **nie wchodzi** do wpisu audytowego (audyt jest czytany szerzej niż
  formularz), do eksportu integracyjnego (``apps.integrations.exports.edition_export``) ani do
  webhooka,
- wpis w rejestrze czynności przetwarzania (``apps.accounts.processing_register``, klucz
  ``logistyka``) wchodzi tym samym commitem i jest dołączany do rejestru **wyłącznie** konkursom,
  które zbieranie włączyły.

**Ekranów tu nie ma.** Widoki koordynatora (``/coordinator/venues/``,
``/coordinator/stages/<id>/logistics/``, ``/coordinator/stages/<id>/attendance/``) są własnością
zadania montażowego wydania K (T42, § 2.2) i wołają wyłącznie funkcje z dolnej części tego pliku.
Listy do druku składa ``apps.integrations.exports`` tą samą ścieżką ReportLab, co protokół etapu –
nowy generator PDF nie powstaje.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from apps.core.api import DomainError

from .scoping import competition_scoped_manager

#: Flaga konkursu z katalogu ``apps.tenancy.models.FEATURE_DEFAULTS`` (§ 0.6). Czytana **wyłącznie**
#: przez :func:`onsite_logistics_enabled` – jedno miejsce odczytu, zgodnie z § 1.0 (c).
FEATURE = "onsite_logistics"

#: Maksymalna długość uwagi w formularzu przyjazdu. Limit jest **częścią ochrony danych**, a nie
#: wygody bazy: pole na pół strony zaprasza do wpisania historii choroby, a pole na dwa zdania
#: wystarcza do „dieta bezglutenowa” i „pokój na parterze”.
NOTE_MAX_LENGTH = 500

#: Etykieta pola ``ArrivalForm.note`` pokazywana uczestnikowi. Mówi wprost, czego **nie** wpisywać –
#: to jest warunek z decyzji D21 i dlatego stoi w kodzie, a nie w szablonie: szablon należy do
#: zadania montażowego, a treść ostrzeżenia ma się zmieniać razem z regułą, którą opisuje.
SPECIAL_NEEDS_WARNING = (
    "Napisz wyłącznie to, co jest potrzebne do przygotowania pobytu (np. „dieta bezmięsna”, "
    "„pokój na parterze”). Nie podawaj diagnoz, nazw chorób, leków ani orzeczeń – tych informacji "
    "nie potrzebujemy i prosimy ich nie przesyłać."
)


class LogisticsNeed(models.TextChoices):
    """Rodzaje potrzeb zgłaszanych przed etapem stacjonarnym.

    Lista zamknięta, bo po niej się **liczy**: organizator zamawia noclegi i posiłki w liczbach,
    a wolny tekst w tej rubryce znaczyłby liczenie ręczne. Szczegół, którego lista nie opisuje,
    idzie do ``ArrivalForm.note`` – i tylko wtedy, gdy konkurs zbiera potrzeby szczególne.
    """

    ACCOMMODATION = "ACCOMMODATION", "nocleg"
    MEAL = "MEAL", "wyżywienie"
    DIET = "DIET", "dieta szczególna"
    ACCESSIBILITY = "ACCESSIBILITY", "dostępność"
    TRANSPORT = "TRANSPORT", "dojazd"


#: Potrzeby, które **bywają** danymi o zdrowiu (art. 9 RODO). Wymienione raz, żeby reguła D21 miała
#: jedno miejsce – filtr formularza, widok koordynatora i listy do druku czytają tę samą krotkę.
SPECIAL_NEEDS: tuple[str, ...] = (LogisticsNeed.DIET, LogisticsNeed.ACCESSIBILITY)

#: Potrzeby zwykłe: liczba łóżek, liczba obiadów, informacja o dojeździe. Żadna z nich nie mówi
#: niczego o zdrowiu ani o niepełnosprawności, więc zbiera je każdy konkurs z włączoną logistyką.
ORDINARY_NEEDS: tuple[str, ...] = tuple(value for value in LogisticsNeed.values if value not in SPECIAL_NEEDS)


# --- modele --------------------------------------------------------------------------------------


class Venue(models.Model):
    """Miejsce zawodów: adres, miasto, pojemność.

    Per konkurs, a nie per etap: finał i warsztaty bywają w tym samym budynku, a organizator
    wpisuje adres raz i wskazuje go w kolejnych latach. Etap odwołuje się do miejsca przez
    deklarację przyjazdu (:class:`ArrivalForm`), bo to przy przyjeździe pytanie „dokąd” ma
    odpowiedź: ten sam etap potrafi odbywać się w kilku salach naraz.

    ``Stage.location`` **zostaje bez zmian** i nadal jest tym, co czyta publiczność na stronie
    i w harmonogramie. Ten model jest danymi organizacyjnymi, a nie zapowiedzią – podmiana
    ``Stage.location`` na klucz obcy byłaby zmianą widoczną dla Konkursu #1 (§ 0.1).
    """

    competition = models.ForeignKey(
        "tenancy.Competition",
        on_delete=models.PROTECT,
        related_name="venues",
        verbose_name="konkurs",
    )
    name = models.CharField("nazwa", max_length=160)
    address = models.CharField("adres", max_length=255, blank=True)
    city = models.CharField("miejscowość", max_length=120, blank=True)
    #: Ile osób miejsce pomieści. Puste znaczy „nie wiadomo”, a nie „zero”: organizator bywa
    #: pewien sali, zanim pozna jej limit, a system nie ma prawa wtedy odmówić zapisania adresu.
    capacity = models.PositiveSmallIntegerField("pojemność", null=True, blank=True)
    note = models.TextField("uwagi", blank=True)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "miejsce zawodów"
        verbose_name_plural = "miejsca zawodów"
        ordering = ("competition", "name", "id")
        constraints = [
            # Nazwa jest tym, po czym koordynator rozpoznaje miejsce na liście wyboru przy
            # formularzu przyjazdu. Dwa „Wydział Fizyki UJ” w jednym konkursie znaczyłyby wybór
            # między dwiema identycznymi pozycjami – a unikalność globalna zabierałaby tę nazwę
            # wszystkim pozostałym organizatorom.
            models.UniqueConstraint(fields=["competition", "name"], name="competitions_venue_unique_name"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city})" if self.city else self.name


class LogisticsSettings(models.Model):
    """Ustawienia logistyki jednego konkursu – dziś jedno rozstrzygnięcie: dane szczególne.

    Wiersz istnieje wyłącznie dla konkursów, które logistykę w ogóle mają; jego brak znaczy
    „ustawienia domyślne”, czyli **nie zbieramy** danych szczególnych (decyzja D21). Dlatego
    :func:`collects_special_needs` nie zakłada wiersza i nie wymaga go – brak wiersza jest
    poprawną odpowiedzią „nie”.

    Dlaczego osobna tabela, a nie kolumna w ``tenancy.Competition``: katalog flag i pola konkursu
    należą do zadań platformowych (§ 4.1), a to jest ustawienie **obszaru** – rośnie razem z nim
    (termin zamknięcia deklaracji, domyślne miejsce, wzór listy) i nie ma powodu, żeby rosło
    w tabeli czytanej przy każdym żądaniu.
    """

    competition = models.OneToOneField(
        "tenancy.Competition",
        on_delete=models.CASCADE,
        related_name="logistics_settings",
        verbose_name="konkurs",
    )
    #: Zgoda organizatora na zbieranie potrzeb szczególnych (dieta, dostępność) i uwagi tekstowej.
    #: Domyślnie **wyłączone**: art. 9 RODO nie jest ustawieniem domyślnym żadnego konkursu.
    #: Włączenie ma sens dopiero wtedy, gdy organizator wie, po co pyta i kto tę odpowiedź czyta.
    collect_special_needs = models.BooleanField("zbieraj potrzeby szczególne", default=False)
    updated_at = models.DateTimeField("zmienione", default=timezone.now)

    objects = competition_scoped_manager()

    class Meta:
        verbose_name = "ustawienia logistyki"
        verbose_name_plural = "ustawienia logistyki"

    def __str__(self) -> str:
        return f"logistyka: {self.competition_id}"


class ArrivalForm(models.Model):
    """Deklaracja uczestnika przed etapem stacjonarnym: kiedy przyjeżdża i czego potrzebuje.

    Potrzeby są **listą wybranych rodzajów** plus jednym polem tekstowym, a nie piętnastoma
    kolumnami: organizatorzy pytają o różne rzeczy, a lista pytań jest konfiguracją, nie schematem.
    Kolumna JSON kosztuje tu dokładnie tyle, ile daje – liczenie noclegów jest jednym przebiegiem
    po wierszach etapu (:func:`needs_summary`), a nie zapytaniem, które ktoś musiałby indeksować.

    ``note`` jest polem o danych szczególnych i podlega regule D21 opisanej w docstringu modułu:
    zapisuje je wyłącznie :func:`save_arrival_form` i wyłącznie przy włączonym zbieraniu.
    """

    entry = models.OneToOneField(
        "competitions.StageEntry",
        on_delete=models.CASCADE,
        related_name="arrival_form",
        verbose_name="wpis do etapu",
    )
    #: ``PROTECT``: skasowanie miejsca razem z deklaracjami zabrałoby informację, kto gdzie nocuje,
    #: w tygodniu, w którym jest ona najbardziej potrzebna. Miejsce wycofuje się przez podmianę
    #: w deklaracjach, a nie przez usunięcie wiersza.
    venue = models.ForeignKey(
        Venue,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="arrivals",
        verbose_name="miejsce",
    )
    arrives_on = models.DateField("przyjazd", null=True, blank=True)
    departs_on = models.DateField("wyjazd", null=True, blank=True)
    #: Lista wartości :class:`LogisticsNeed`. Pusta lista znaczy „nic nie potrzebuję” i jest
    #: odpowiedzią, a nie brakiem odpowiedzi – od braku odróżnia ją istnienie samego wiersza.
    needs = models.JSONField("potrzeby", default=list, blank=True)
    note = models.CharField("uwagi", max_length=NOTE_MAX_LENGTH, blank=True)
    submitted_at = models.DateTimeField("złożony", default=timezone.now)

    objects = competition_scoped_manager("entry__stage__edition__competition")

    class Meta:
        verbose_name = "formularz przyjazdu"
        verbose_name_plural = "formularze przyjazdu"
        ordering = ("entry", "id")
        constraints = [
            # Wyjazd przed przyjazdem jest błędem, którego nie widać na liście noclegowej – widać
            # go dopiero jako brakujące łóżko. Ta sama reguła stoi w ``clean()`` z komunikatem,
            # który da się pokazać pod polem.
            models.CheckConstraint(
                condition=Q(arrives_on__isnull=True)
                | Q(departs_on__isnull=True)
                | Q(departs_on__gte=models.F("arrives_on")),
                name="competitions_arrivalform_departure_after_arrival",
            ),
        ]

    def __str__(self) -> str:
        return f"przyjazd: wpis {self.entry_id}"

    def clean(self) -> None:
        """Reguły integralności z komunikatem dla człowieka; więz w bazie powtarza pierwszą z nich."""
        super().clean()
        if self.arrives_on is not None and self.departs_on is not None and self.departs_on < self.arrives_on:
            raise ValidationError({"departs_on": "Wyjazd nie może być przed przyjazdem."})
        self.needs = validated_needs(self.needs)
        if self.venue_id is not None and self.entry_id is not None:
            from .models import StageEntry

            # Miejsce cudzego konkursu w deklaracji byłoby wyciekiem w obie strony: uczestnik
            # zobaczyłby adres cudzego organizatora, a lista noclegowa tamtego konkursu policzyłaby
            # obcego uczestnika. Więzu bazodanowego tu nie ma, bo droga wpisu do konkursu wiedzie
            # przez trzy klucze obce – reguła stoi więc w walidacji, tak jak przy ``StageEntry``.
            owner = (
                StageEntry.objects.filter(pk=self.entry_id)
                .values_list("stage__edition__competition_id", flat=True)
                .first()
            )
            venue_owner = (
                Venue.objects.filter(pk=self.venue_id).values_list("competition_id", flat=True).first()
            )
            if owner is not None and venue_owner is not None and venue_owner != owner:
                raise ValidationError({"venue": "Miejsce należy do innego konkursu."})


class AttendanceRecord(models.Model):
    """Obecność na etapie stacjonarnym – wpis komisji, jeden na wpis do etapu.

    Osobny model od ``cms.WorkshopAttendance``: tamten wisi na **tekstowym** kluczu wiersza
    harmonogramu w CMS-ie, bo warsztat nie jest obiektem w bazie. Etap jest, więc tutaj jest klucz
    obcy – i nie ma powodu dziedziczyć ograniczenia, które tam wynikało z braku modelu.

    Wiersz powstaje dopiero przy odnotowaniu obecności, a nie przy zapisie do etapu: lista
    obecności drukuje się z wpisów do etapu (:func:`attendance_rows`), więc pusta tabela znaczy
    „jeszcze nikogo nie odhaczono”, a nie „nikogo nie ma”.
    """

    entry = models.OneToOneField(
        "competitions.StageEntry",
        on_delete=models.CASCADE,
        related_name="attendance",
        verbose_name="wpis do etapu",
    )
    present = models.BooleanField("obecny", default=False)
    checked_in_at = models.DateTimeField("odnotowano", null=True, blank=True)
    #: ``SET_NULL``: skasowanie konta członka komisji nie może wymazać tego, że obecność w ogóle
    #: odnotowano – bez autora zostaje data, czyli nadal dokument.
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_recorded",
        verbose_name="odnotował",
    )

    objects = competition_scoped_manager("entry__stage__edition__competition")

    class Meta:
        verbose_name = "obecność"
        verbose_name_plural = "obecności"
        ordering = ("entry", "id")
        constraints = [
            # „Obecny” bez godziny odpowiada na pytanie „czy był” i nie odpowiada na „kiedy”,
            # a to jest pytanie, które pada – przy sporze o dopuszczenie do zawodów jako pierwsze.
            models.CheckConstraint(
                condition=Q(present=False) | Q(checked_in_at__isnull=False),
                name="competitions_attendancerecord_present_has_time",
            ),
        ]

    def __str__(self) -> str:
        return f"obecność: wpis {self.entry_id} ({'tak' if self.present else 'nie'})"


# --- flagi i ustawienia ---------------------------------------------------------------------------


def onsite_logistics_enabled(competition=None) -> bool:
    """Czy ten konkurs prowadzi logistykę etapu stacjonarnego. **Jedyny** odczyt flagi w module.

    ``None`` (nie wiadomo, o który konkurs chodzi) znaczy „nie”: logistyka jest zdolnością
    dołożoną, a nie zachowaniem zastanym, więc brak rozstrzygnięcia ma dać stan sprzed etapu 2.
    """
    return competition is not None and competition.has_feature(FEATURE)


def collects_special_needs(competition=None) -> bool:
    """Czy konkurs zbiera potrzeby szczególne (dieta, dostępność) i uwagę tekstową – decyzja D21.

    Dwa warunki naraz i oba muszą być spełnione: flaga obszaru (logistyka w ogóle działa) oraz
    świadome ustawienie konkursu (:class:`LogisticsSettings`). Brak wiersza ustawień znaczy „nie” –
    domyślnie **nie zbieramy** danych, które bywają danymi o zdrowiu.
    """
    if not onsite_logistics_enabled(competition):
        return False
    return LogisticsSettings.objects.filter(competition=competition, collect_special_needs=True).exists()


def available_needs(competition=None) -> tuple[tuple[str, str], ...]:
    """Rodzaje potrzeb, o które wolno zapytać w tym konkursie – pary ``(wartość, etykieta)``.

    Formularz przyjazdu (T42) buduje z tego swoje pole wyboru i nie pyta o nic więcej: przy
    wyłączonym zbieraniu danych szczególnych „dieta” i „dostępność” **nie istnieją** na ekranie,
    a nie są tylko ignorowane przy zapisie.
    """
    allowed = LogisticsNeed.choices
    if not collects_special_needs(competition):
        allowed = [choice for choice in allowed if choice[0] not in SPECIAL_NEEDS]
    return tuple(allowed)


def validated_needs(values) -> list[str]:
    """Lista potrzeb sprowadzona do postaci kanonicznej: bez duplikatów, w kolejności słownika.

    Kolejność ze słownika, a nie z formularza: lista jest drukowana na liście obecności i na liście
    żywieniowej, a dwa wiersze z tymi samymi potrzebami w innej kolejności czyta się jak dwie różne
    odpowiedzi. Wartość spoza :class:`LogisticsNeed` jest błędem wołającego, nie danymi do
    zapisania – stąd ``ValidationError`` zamiast cichego pominięcia.
    """
    if values in (None, ""):
        return []
    if isinstance(values, str) or not isinstance(values, (list, tuple, set, frozenset)):
        raise ValidationError({"needs": "Potrzeby zapisujemy jako listę wartości."})
    unknown = sorted({str(value) for value in values} - set(LogisticsNeed.values))
    if unknown:
        raise ValidationError({"needs": f"Nieznany rodzaj potrzeby: {', '.join(unknown)}."})
    chosen = {str(value) for value in values}
    return [value for value in LogisticsNeed.values if value in chosen]


def need_labels(values) -> list[str]:
    """Etykiety potrzeb do pokazania na ekranie i na wydruku („nocleg”, „wyżywienie”)."""
    labels = dict(LogisticsNeed.choices)
    return [labels[value] for value in validated_needs(values)]


def set_special_needs_collection(competition, *, enabled: bool, actor=None, request=None):
    """Włącza albo wyłącza zbieranie potrzeb szczególnych – z wpisem audytowym.

    Decyzja D21 jest decyzją organizatora o **danych o zdrowiu**, więc musi zostawiać ślad: kto
    i kiedy ją podjął. Wyłączenie nie kasuje zebranych już uwag – to jest osobna czynność
    (retencja albo ręczne czyszczenie), a ciche usuwanie danych przy przestawieniu przełącznika
    byłoby zmianą, której nikt się nie spodziewa.
    """
    from apps.core.models import audit

    require_onsite_logistics(competition)
    with transaction.atomic():
        row, _ = LogisticsSettings.objects.get_or_create(competition=competition)
        before = row.collect_special_needs
        row.collect_special_needs = enabled
        row.updated_at = timezone.now()
        row.save(update_fields=["collect_special_needs", "updated_at"])
    audit(
        actor,
        "logistics.special_needs_collection_changed",
        row,
        {"from": before, "to": enabled},
        request=request,
    )
    return row


def require_onsite_logistics(competition=None) -> None:
    """Przerywa, gdy konkurs nie prowadzi logistyki. Odwrót twardy – to odczyt **konfiguracji**.

    Rozróżnienie z § 1.0 (b): listy i podglądy schodzą do pustki (miękko), ale **zapis** deklaracji
    albo obecności w konkursie bez logistyki jest błędem wołającego i ma być słyszalny od razu,
    a nie zamienić się w wiersz, którego nikt nigdy nie zobaczy.
    """
    if not onsite_logistics_enabled(competition):
        raise DomainError(
            "Ten konkurs nie prowadzi logistyki etapu stacjonarnego.",
            "ONSITE_LOGISTICS_DISABLED",
            status.HTTP_403_FORBIDDEN,
        )


def competition_of_stage(stage):
    """Konkurs, do którego należy etap – jedną drogą, tą samą, którą filtruje zakresowanie."""
    return stage.edition.competition


# --- miejsca zawodów -------------------------------------------------------------------------------


def venues_for(competition=None):
    """Miejsca zawodów konkursu; przy wyłączonej fladze **pustka bez zapytania do bazy**.

    Pustka, a nie wyjątek: ekran, który wyliczyłby się dla konkursu bez logistyki, i tak nie ma
    czego pokazać, a lista wyboru miejsca w cudzym formularzu ma być pusta, a nie wywalać widok.
    """
    if not onsite_logistics_enabled(competition):
        return Venue.objects.none()
    return Venue.objects.for_competition(competition)


def create_venue(competition, *, name: str, actor=None, request=None, **fields) -> Venue:
    """Zakłada miejsce zawodów. Wołane z ekranu „Miejsca zawodów” (T42)."""
    from apps.core.models import audit

    require_onsite_logistics(competition)
    venue = Venue(competition=competition, name=name, **fields)
    venue.full_clean()
    venue.save()
    audit(actor, "venue.created", venue, {"name": venue.name, "city": venue.city}, request=request)
    return venue


def update_venue(venue: Venue, *, actor=None, request=None, **fields) -> Venue:
    """Zmienia pola miejsca zawodów; audyt notuje wyłącznie to, co naprawdę się zmieniło."""
    from apps.core.models import audit

    before = {field: getattr(venue, field) for field in fields}
    for field, value in fields.items():
        setattr(venue, field, value)
    venue.full_clean()
    venue.save(update_fields=list(fields) or None)
    changed = {field: {"from": before[field], "to": value} for field, value in fields.items()}
    if changed:
        audit(actor, "venue.updated", venue, changed, request=request)
    return venue


# --- deklaracje przyjazdu ---------------------------------------------------------------------------


def arrival_form_for(entry):
    """Deklaracja przyjazdu tego wpisu albo ``None``. Jedno wejście dla panelu uczestnika."""
    if not onsite_logistics_enabled(competition_of_stage(entry.stage)):
        return None
    return ArrivalForm.objects.filter(entry=entry).first()


def save_arrival_form(
    entry,
    *,
    venue=None,
    arrives_on=None,
    departs_on=None,
    needs=(),
    note: str = "",
    actor=None,
    request=None,
) -> ArrivalForm:
    """Zapisuje (albo aktualizuje) deklarację przyjazdu jednego wpisu do etapu.

    Tu jest **cała** brama danych szczególnych (D21): przy wyłączonym zbieraniu potrzeby ``DIET``
    i ``ACCESSIBILITY`` są odfiltrowane, a ``note`` zapisywane jako puste – niezależnie od tego, co
    przyszło z formularza. Dzięki temu włączenie zbierania jest jedną decyzją w jednym miejscu,
    a nie zgodnością dziesięciu ekranów.

    Wpis audytowy notuje **rodzaje** potrzeb i daty, nigdy treść uwagi: audyt czyta szersze grono
    niż formularz, a dana o zdrowiu przepisana do dziennika zdarzeń zostaje tam bezterminowo.
    """
    from apps.core.models import audit

    competition = competition_of_stage(entry.stage)
    require_onsite_logistics(competition)
    special = collects_special_needs(competition)
    chosen = validated_needs(needs)
    if not special:
        chosen = [value for value in chosen if value not in SPECIAL_NEEDS]
        note = ""
    form = ArrivalForm.objects.filter(entry=entry).first() or ArrivalForm(entry=entry)
    before = {"needs": list(form.needs or []), "venue": form.venue_id}
    form.venue = venue
    form.arrives_on = arrives_on
    form.departs_on = departs_on
    form.needs = chosen
    form.note = (note or "").strip()
    form.submitted_at = timezone.now()
    form.full_clean()
    form.save()
    audit(
        actor,
        "arrival_form.saved",
        form,
        {
            "from": before,
            "to": {"needs": form.needs, "venue": form.venue_id},
            "arrives_on": form.arrives_on.isoformat() if form.arrives_on else None,
            "departs_on": form.departs_on.isoformat() if form.departs_on else None,
            # Sama informacja „uwaga jest / uwagi nie ma” nie jest daną o zdrowiu, a odpowiada na
            # pytanie, które pada przy sporze: czy uczestnik cokolwiek zgłosił.
            "has_note": bool(form.note),
        },
        request=request,
    )
    return form


def arrivals_for_stage(stage):
    """Deklaracje przyjazdu jednego etapu – queryset gotowy do wyświetlenia, bez N+1.

    Zawężenie idzie przez etap, a nie przez konkurs: ekran dotyczy jednego etapu, a zakresowanie
    konkursem pilnuje, żeby cudzy etap nie dał się w ogóle wskazać (``StageEntry.objects``).
    """
    if not onsite_logistics_enabled(competition_of_stage(stage)):
        return ArrivalForm.objects.none()
    return (
        ArrivalForm.objects.filter(entry__stage=stage)
        .select_related("entry__participant__user", "venue")
        .order_by("entry__participant__public_code")
    )


def arrival_rows(stage) -> list[dict]:
    """Wiersze ekranu „Przyjazdy i potrzeby” – dane gotowe do pokazania koordynatorowi.

    ``note`` jest w wierszu **wyłącznie** wtedy, gdy konkurs zbiera potrzeby szczególne; przy
    wyłączonym zbieraniu klucza nie ma w ogóle, więc szablon nie ma czego pokazać nawet przez
    pomyłkę. Ekran jest koordynatorski i tylko koordynatorski (§ 1.5.2) – recenzent i opiekun nie
    mają do tych funkcji drogi.
    """
    special = collects_special_needs(competition_of_stage(stage))
    rows: list[dict] = []
    for form in arrivals_for_stage(stage):
        participant = form.entry.participant
        row = {
            "entry_id": form.entry_id,
            "public_code": participant.public_code,
            "first_name": participant.user.first_name,
            "last_name": participant.user.last_name,
            "school": participant.school,
            "venue": form.venue.name if form.venue_id else "",
            "arrives_on": form.arrives_on,
            "departs_on": form.departs_on,
            "needs": list(form.needs or []),
            "need_labels": need_labels(form.needs),
        }
        if special:
            row["note"] = form.note
        rows.append(row)
    return rows


def needs_summary(stage) -> dict[str, int]:
    """Ile osób zgłosiło którą potrzebę – liczby, które organizator przepisuje do zamówienia.

    Zliczanie w Pythonie, a nie w bazie: potrzeby są listą w kolumnie JSON, a etap stacjonarny ma
    setki, nie miliony wierszy. Zapytanie rozwijające tablicę byłoby szybsze o ułamek milisekundy
    i nieczytelne dla każdego, kto je kiedyś otworzy.
    """
    counts = dict.fromkeys(LogisticsNeed.values, 0)
    for needs in arrivals_for_stage(stage).values_list("needs", flat=True):
        for value in validated_needs(needs):
            counts[value] += 1
    return counts


# --- obecność --------------------------------------------------------------------------------------


def attendance_for_stage(stage):
    """Odnotowane obecności jednego etapu – queryset dla ekranu i dla listy do druku."""
    if not onsite_logistics_enabled(competition_of_stage(stage)):
        return AttendanceRecord.objects.none()
    return AttendanceRecord.objects.filter(entry__stage=stage).select_related("entry__participant__user")


def record_attendance(entry, *, present: bool, actor=None, request=None, now=None):
    """Odnotowuje obecność (albo jej brak) na etapie stacjonarnym – jeden wiersz na wpis.

    Godzina powstaje **tutaj**, a nie w formularzu: „kiedy się stawił” jest faktem z systemu, a nie
    deklaracją komisji, i przy sporze o dopuszczenie do zawodów to ona jest odpowiedzią. Odznaczenie
    obecności zeruje godzinę, bo godzina przy nieobecnym opisywałaby zdarzenie, którego nie było.
    """
    from apps.core.models import audit

    competition = competition_of_stage(entry.stage)
    require_onsite_logistics(competition)
    record = AttendanceRecord.objects.filter(entry=entry).first() or AttendanceRecord(entry=entry)
    before = record.present
    record.present = present
    record.checked_in_at = (now or timezone.now()) if present else None
    record.recorded_by = actor if getattr(actor, "is_authenticated", False) else None
    record.full_clean()
    record.save()
    audit(actor, "attendance.recorded", record, {"from": before, "to": present}, request=request)
    return record


def attendance_rows(stage) -> list[dict]:
    """Wiersze listy obecności: **wszyscy** zapisani do etapu, a nie tylko odhaczeni.

    Lista obecności jest dokumentem, który komisja niesie na salę – ma na niej być każdy, kto ma
    prawo wejść, z pustą rubryką do podpisu. Dlatego chodzimy po wpisach do etapu i dokładamy
    obecność, jeżeli już jest, a nie odwrotnie.
    """
    from .models import StageEntry

    if not onsite_logistics_enabled(competition_of_stage(stage)):
        return []
    recorded = {row.entry_id: row for row in attendance_for_stage(stage)}
    arrivals = {form.entry_id: form for form in arrivals_for_stage(stage)}
    entries = (
        StageEntry.objects.filter(stage=stage)
        .select_related("participant__user")
        .order_by("participant__public_code")
    )
    rows: list[dict] = []
    for entry in entries:
        record = recorded.get(entry.pk)
        form = arrivals.get(entry.pk)
        rows.append(
            {
                "entry_id": entry.pk,
                "public_code": entry.participant.public_code,
                "first_name": entry.participant.user.first_name,
                "last_name": entry.participant.user.last_name,
                "school": entry.participant.school,
                "status": entry.status,
                "present": bool(record and record.present),
                "checked_in_at": record.checked_in_at if record else None,
                "venue": form.venue.name if form and form.venue_id else "",
                "needs": list(form.needs or []) if form else [],
            }
        )
    return rows
