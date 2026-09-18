"""Ekran „Regiony” ``/coordinator/regions/`` – podział terytorialny konkursu (etap 2, § 1.4).

Podział jest **danymi konkursu**, a nie instalacji: olimpiada ogólnopolska dzieli się na 16
województw, olimpiada uczelniana na 5 okręgów akademickich, a konkurs międzynarodowy na kraje.
Ten ekran jest jedynym miejscem, w którym organizator ten podział widzi w całości i zmienia.

**Dlaczego ekran jest za flagą ``custom_regions``.** Dopóki flaga jest wyłączona, wszystko czyta
``district`` – zamkniętą listę szesnastu województw ze stałej ``Voivodeship`` (§ 1.4.2). Ekran
pozwalający wtedy dopisać region obiecywałby podział, którego nie zobaczy ani formularz
rejestracji, ani reguła konfliktu interesów, ani tabela wyników. Adres przy wyłączonej fladze daje
**404**, a nie 403 (§ 2.1); rolę sprawdza wcześniej i osobno ``CoordinatorRequiredMixin``, więc
uczestnik dostaje 403 niezależnie od stanu flagi.

**Region w użyciu się nie kasuje.** ``Participant.region`` i ``CommitteeMember.region`` są
``PROTECT``, więc baza zatrzymuje usunięcie wiersza, do którego ktoś jest przypisany. Ekran tego
nie obchodzi ani nie kończy pięćsetką: łapie ``ProtectedError``, liczy, kogo to dotyczy, i mówi
o drodze wycofania – odznaczeniu „aktywny”, po którym region znika z list wyboru, a profile
z poprzednich lat dalej wskazują ten, w którym wtedy startowały.

**Trzy panele czytelne i żaden nieedytowalny w locie.** Drzewo jest konfiguracją, „kto jest gdzie”
liczbami, a podgląd konfliktu interesów odpowiedzią na pytanie „czy po tym podziale zostanie komu
oceniać”. Każdy z nich kosztuje stałą liczbę zapytań, niezależną od liczby regionów i profili –
liczniki idą zapytaniami grupującymi, a nie pętlą po regionach.

Audyt pisze ten widok, bo regiony nie mają pod sobą serwisu domenowego (są słownikiem konkursu,
a nie czynnością na danych uczestników). Do wpisu idą **kody, poziomy i liczby** – ani jednego
nazwiska, ani jednego kodu publicznego, także przy odtworzeniu zestawu startowego.
"""

from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, ProtectedError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import CommitteeMember, Region
from apps.accounts.regions import default_regions_for
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.competitions.models import StageEntry
from apps.competitions.services import current_edition
from apps.core.models import audit
from apps.grading.services import conflict_by_region
from apps.web.coordinator_forms import RegionForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/regions.html"
FORM_TEMPLATE = "web/coordinator/regions_form.html"

#: Przełącznik, który włącza ten ekran. Ta sama nazwa, którą czyta rejestracja
#: (``apps.accounts.services``) i reguła konfliktu interesów (``apps.grading.services``) – napis
#: powtórzony tutaj dałby ekran włączany flagą, której nikt poza nim nie czyta.
FEATURE = CUSTOM_REGIONS_FLAG

#: Wcięcie jednego poziomu drzewa w kolumnie „nazwa”. Znak, a nie styl: strony panelu chodzą na
#: ścisłym CSP bez ``'unsafe-inline'``, więc ``style="padding-left: …"`` w szablonie nie ma prawa
#: wstępu, a nowa klasa na poziom znaczyłaby arkusz zależny od głębokości drzewa.
INDENT = "· "


class RegionScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka ekranów regionów: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma własny podział terytorialny. Inaczej 404.

        Konkurs bierzemy z ``request.competition``, a nie z identyfikatora w adresie – dzięki temu
        nie ma tu adresu, pod którym dałoby się obejrzeć cudzy podział. Sprawdzenie stoi w metodzie
        widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej ``RoleRequiredMixin.dispatch``:
        anonim i uczestnik mają dostać 302 albo 403, **zanim** odpowiedź zdradzi stan przełącznika
        tego konkursu.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs nie ma własnego podziału terytorialnego – ekran jest wyłączony.")
        return competition

    def region_or_404(self, competition, pk: int) -> Region:
        """Region **tego konkursu** albo 404 – zakres wychodzi z querysetu, nie z widoku."""
        return get_object_or_404(Region.objects.for_competition(competition), pk=pk)


def _region_diff(region: Region) -> dict:
    """Komplet pól regionu do audytu – kody i liczby, ani jednego wiersza o człowieku.

    Nadrzędny idzie **kodem**, a nie identyfikatorem: wpis audytowy ma być czytelny za dwa lata,
    gdy wiersza o tym numerze może już nie być, a kod zostaje w eksportach i w protokołach.
    """
    return {
        "code": region.code,
        "name": region.name,
        "level": region.level,
        "parent": region.parent.code if region.parent_id else None,
        "position": region.position,
        "is_active": region.is_active,
        "counts_for_conflict": region.counts_for_conflict,
    }


def _tree_rows(regions: list[Region]) -> list[dict]:
    """Drzewo regionów jako lista z głębokością – **bez ani jednego dodatkowego zapytania**.

    Wiersze przychodzą jednym zapytaniem i są układane w pamięci: drzewo ma trzy poziomy i
    kilkanaście liści, a zapytanie na gałąź znaczyłoby koszt rosnący z liczbą regionów na ekranie,
    który istnieje właśnie po to, żeby regionów przybywało.

    Sierota (nadrzędny spoza tej listy – możliwy wyłącznie przy ręcznej zmianie w bazie) trafia na
    koniec jako korzeń, a nie znika: ekran diagnostyczny ma pokazać stan faktyczny.
    """
    children: dict[int | None, list[Region]] = {}
    known = {region.pk for region in regions}
    for region in regions:
        parent = region.parent_id if region.parent_id in known else None
        children.setdefault(parent, []).append(region)
    rows: list[dict] = []

    def walk(parent_id: int | None, depth: int) -> None:
        group = children.get(parent_id, [])
        for index, region in enumerate(group):
            rows.append(
                {
                    "region": region,
                    "depth": depth,
                    "indent": INDENT * depth,
                    "can_move_up": index > 0,
                    "can_move_down": index < len(group) - 1,
                }
            )
            walk(region.pk, depth + 1)

    walk(None, 0)
    return rows


def _participants_by_region(competition, edition) -> dict[int | None, int]:
    """Ilu uczestników bieżącej edycji jest w każdym regionie – **jedno** zapytanie grupujące.

    Liczymy przez wpisy do etapów, bo „uczestnik tej edycji” znaczy w tym systemie dokładnie tyle:
    profil należy do konkursu i przeżywa rocznik, a start w rocznika dowodzi wpis do etapu.
    ``distinct`` jest konieczne, bo jeden uczestnik ma w edycji tyle wpisów, ile etapów przeszedł.

    Klucz ``None`` to profile bez regionu – w konkursie, który dopiero włączył flagę, jest ich
    tylu, ilu zarejestrowało się przed włączeniem. Ta liczba jest odpowiedzią, a nie brakiem
    odpowiedzi, więc panel ją pokazuje osobnym wierszem.
    """
    if edition is None:
        return {}
    rows = (
        StageEntry.objects.for_competition(competition)
        .filter(stage__edition=edition, participant__isnull=False)
        .values("participant__region_id")
        .annotate(total=Count("participant_id", distinct=True))
    )
    return {row["participant__region_id"]: row["total"] for row in rows}


def _members_by_region(competition) -> dict[int | None, int]:
    """Ilu członków komitetu jest w każdym regionie – **jedno** zapytanie grupujące.

    Bez zawężenia do edycji i to jest różnica wobec uczestników, a nie niekonsekwencja: komitet
    jest powoływany **w konkursie** (``CommitteeMember.competition``), nie w roczniku, i ta sama
    osoba ocenia prace kolejnych edycji bez zakładania nowego profilu.
    """
    rows = (
        CommitteeMember.objects.for_competition(competition).values("region_id").annotate(total=Count("id"))
    )
    return {row["region_id"]: row["total"] for row in rows}


def _stage_or_none(stages: list, raw):
    """Etap wskazany w adresie albo ``None``. Śmieci w parametrze nie są błędem użytkownika.

    Szukamy na **wczytanej już liście** etapów edycji, a nie zapytaniem po identyfikatorze: lista
    i tak stoi w formularzu wyboru, a drugie zapytanie o ten sam wiersz byłoby kosztem za nic.
    Przy okazji wychodzi z tego zawężenie – etap spoza bieżącej edycji tego konkursu nie ma jak
    trafić do podglądu, choćby ktoś wpisał jego numer w adres.
    """
    if not raw:
        return None
    try:
        stage_id = int(raw)
    except (TypeError, ValueError):
        return None
    return next((stage for stage in stages if stage.pk == stage_id), None)


def _conflict_preview(competition, stage, regions: list[Region]) -> dict | None:
    """Podgląd reguły konfliktu interesów dla wskazanego etapu (§ 1.4.4).

    Odpowiada na jedno pytanie, którego po samym drzewie nie widać: **czy po tym podziale zostanie
    komu oceniać**. Region, w którym mieszka połowa komitetu, odbiera tę połowę pracom swoich
    uczestników – i lepiej, żeby organizator zobaczył to przed przydziałem, a nie po nim.

    O tym, czy reguła w ogóle czyta regiony, rozstrzyga :func:`apps.grading.services.conflict_by_region`
    – ta sama funkcja, którą woła przydział recenzentów. Drugiej kopii warunku („etap wojewódzki
    i włączona flaga”) tu nie ma, bo kopia rozjechałaby się przy pierwszej zmianie reguły i podgląd
    pokazywałby coś innego niż automat.

    Grupowanie idzie po ``participant__region_id``, czyli po dokładnie tej wartości, którą
    przydziałowi podaje ``participant_conflict_region`` – pilnuje tego
    ``test_the_preview_groups_by_the_same_region_the_rule_reads``.
    """
    if stage is None:
        return None
    if not conflict_by_region(stage):
        return {"stage": stage, "applies": False, "rows": [], "reviewers": 0, "unassigned": 0}
    entries = (
        StageEntry.objects.for_competition(competition)
        .filter(stage=stage, participant__isnull=False)
        .values("participant__region_id")
        .annotate(total=Count("id"))
    )
    by_region = {row["participant__region_id"]: row["total"] for row in entries}
    members = _members_by_region(competition)
    reviewers = sum(members.values())
    rows = [
        {
            "region": region,
            "participants": by_region.get(region.pk, 0),
            "members": members.get(region.pk, 0),
            # Ilu członków komitetu zostaje pracom z tego regionu. Region, który do konfliktu się
            # nie liczy („poza Polską”), nie zabiera nikogo – i to jest cała jego rola.
            "available": reviewers - (members.get(region.pk, 0) if region.counts_for_conflict else 0),
        }
        for region in regions
        if by_region.get(region.pk, 0) or members.get(region.pk, 0)
    ]
    return {
        "stage": stage,
        "applies": True,
        "rows": rows,
        "reviewers": reviewers,
        # Wpisy uczestników, którzy regionu nie mają. Reguła odtwarza im go wtedy z województwa
        # (``region_for_district``), więc nie są „poza konfliktem” – po prostu nie widać ich w
        # podziale i tabela mówi to wprost.
        "unassigned": by_region.get(None, 0),
    }


def _render_list(request, competition, form: RegionForm, *, status: int = 200):
    """Ekran regionów. Wspólny dla wejścia i dla każdego nieudanego dopisania."""
    edition = current_edition(competition)
    regions = list(Region.objects.for_competition(competition))
    participants = _participants_by_region(competition, edition)
    members = _members_by_region(competition)
    stages = list(edition.stages.order_by("opens_at", "id")) if edition is not None else []
    stage = _stage_or_none(stages, request.GET.get("stage"))
    context = {
        "edition": edition,
        "rows": _tree_rows(regions),
        "counts": [
            {
                "region": region,
                "participants": participants.get(region.pk, 0),
                "members": members.get(region.pk, 0),
            }
            for region in regions
        ],
        "counts_unassigned": {
            "participants": participants.get(None, 0),
            "members": members.get(None, 0),
        },
        "stages": stages,
        "preview": _conflict_preview(competition, stage, regions),
        "selected_stage_id": stage.pk if stage is not None else None,
        "form": form,
    }
    return TemplateResponse(request, LIST_TEMPLATE, context, status=status)


@transaction.atomic
def _move_region(region: Region, delta: int) -> bool:
    """Przesuwa region o jedno miejsce **wśród rodzeństwa**. ``False`` = jest już na skraju.

    Kolejność zmienia się w obrębie jednego nadrzędnego i to jest cała reguła: „mazowieckie” nie
    ma jak stanąć przed „Polską”, bo pod nią wisi. Po zamianie przenumerowujemy całe rodzeństwo od
    jedynki, zamiast zamieniać dwie wartości miejscami – wiersze dopisane ręcznie mają domyślne
    ``position = 0``, więc zamiana dwóch zer nie przestawiłaby niczego, a kolejność rozstrzygałaby
    dalej nazwa.
    """
    siblings = list(
        Region.objects.filter(competition_id=region.competition_id, parent_id=region.parent_id).order_by(
            "position", "name", "id"
        )
    )
    ids = [row.pk for row in siblings]
    index = ids.index(region.pk)
    target = index + delta
    if not 0 <= target < len(siblings):
        return False
    siblings[index], siblings[target] = siblings[target], siblings[index]
    for position, row in enumerate(siblings, start=1):
        if row.position != position:
            Region.objects.filter(pk=row.pk).update(position=position)
    region.refresh_from_db(fields=["position"])
    return True


def _usage(region: Region) -> dict[str, int]:
    """Ilu ludzi wskazuje ten region – liczba do komunikatu odmowy, nie do ekranu.

    Liczymy dopiero **po** odmowie bazy, a nie przed każdą próbą: ścieżka udana (region bez ani
    jednego profilu) nie ma powodu płacić trzech zapytań za sprawdzenie warunku, który baza i tak
    sprawdza sama.
    """
    return {
        "participants": region.participants.count(),
        "members": region.committee_members.count(),
        "codes": region.invitation_codes.count(),
    }


class RegionListView(RegionScreenMixin, View):
    """``GET /coordinator/regions/`` – drzewo regionów, „kto jest gdzie” i podgląd konfliktu."""

    def get(self, request):
        competition = self.competition_or_404(request)
        return _render_list(request, competition, RegionForm(competition=competition))


class RegionCreateView(RegionScreenMixin, View):
    """``POST /coordinator/regions/new/`` – nowy region konkursu."""

    def post(self, request):
        competition = self.competition_or_404(request)
        form = RegionForm(request.POST, competition=competition)
        if not form.is_valid():
            return _render_list(request, competition, form, status=400)
        region = form.save(commit=False)
        region.competition = competition
        region.save()
        audit(request.user, "region.created", region, _region_diff(region), request=request)
        messages.success(request, f"Region „{region.name}” został dodany.")
        return redirect(reverse("web:coordinator-regions"))


class RegionEditView(RegionScreenMixin, View):
    """``GET|POST /coordinator/regions/<id>/`` – zmiana jednego regionu.

    Kod wolno zmienić, choć jest tożsamością: profile wskazują region kluczem obcym, a nie napisem.
    Zmiana kodu jest więc poprawką w regulaminie, a nie podmianą obiektu – z jednym skutkiem, który
    trzeba znać i który mówi wprost szablon: ``Participant.district`` jest **denormalizowaną kopią**
    kodu (§ 1.4.2) i odświeży się dopiero przy najbliższym zapisie profilu.
    """

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        return self._render(request, region, RegionForm(instance=region, competition=competition))

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        before = _region_diff(region)
        form = RegionForm(request.POST, instance=region, competition=competition)
        if not form.is_valid():
            return self._render(request, region, form, status=400)
        saved = form.save()
        audit(
            request.user,
            "region.updated",
            saved,
            {"before": before, "after": _region_diff(saved)},
            request=request,
        )
        messages.success(request, f"Region „{saved.name}” został zapisany.")
        return redirect(reverse("web:coordinator-regions"))

    def _render(self, request, region: Region, form: RegionForm, *, status: int = 200):
        context = {"region": region, "form": form, "usage": _usage(region)}
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class RegionMoveView(RegionScreenMixin, View):
    """``POST /coordinator/regions/<id>/move/<kierunek>/`` – przesunięcie o jedno miejsce.

    Osobny adres na kierunek, a nie jeden z polem „kierunek”: rozróżnianie po nazwie wciśniętego
    przycisku zależy od tego, czy przeglądarka ją przyśle, a przy wysyłce klawiaturą nie zawsze
    przysyła (ta sama reguła, co na ekranie integracji i w edytorze przebiegu).
    """

    #: Kierunki i ich przesunięcie na liście. Napis w adresie, a nie ``-1``/``1``, bo adres
    #: z minusem bywa łamany przez filtry proxy i jest nieczytelny w logu.
    DIRECTIONS = {"up": -1, "down": 1}

    def post(self, request, pk: int, direction: str):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        delta = self.DIRECTIONS.get(direction)
        if delta is None:
            raise Http404("Nieznany kierunek przesunięcia regionu.")
        before = region.position
        if not _move_region(region, delta):
            messages.info(request, "Ten region jest już na skraju swojego poziomu – nic się nie zmieniło.")
            return redirect(reverse("web:coordinator-regions"))
        audit(
            request.user,
            "region.updated",
            region,
            {"code": region.code, "before": {"position": before}, "after": {"position": region.position}},
            request=request,
        )
        messages.success(request, f"Region „{region.name}” zmienił miejsce w kolejności.")
        return redirect(reverse("web:coordinator-regions"))


class RegionDeactivateView(RegionScreenMixin, View):
    """``POST /coordinator/regions/<id>/deactivate/`` – region znika z list wyboru.

    Droga wycofania regionu, którym ktoś już startował. Wiersz **zostaje**: profile z lat
    poprzednich mają dalej wskazywać region, w którym wtedy startowały, a tabela wyników sprzed
    dwóch sezonów ma dalej mieć czym podpisać kolumnę.
    """

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        if region.is_active:
            region.is_active = False
            region.save(update_fields=["is_active"])
            audit(request.user, "region.deactivated", region, {"code": region.code}, request=request)
        messages.success(
            request,
            f"Region „{region.name}” nie pojawi się już na listach wyboru. Profile, które go mają, "
            "zostają bez zmian.",
        )
        return redirect(reverse("web:coordinator-regions"))


class RegionActivateView(RegionScreenMixin, View):
    """``POST /coordinator/regions/<id>/activate/`` – powrót regionu na listy wyboru."""

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        if not region.is_active:
            region.is_active = True
            region.save(update_fields=["is_active"])
            audit(
                request.user,
                "region.updated",
                region,
                {"code": region.code, "before": {"is_active": False}, "after": {"is_active": True}},
                request=request,
            )
        messages.success(request, f"Region „{region.name}” wrócił na listy wyboru.")
        return redirect(reverse("web:coordinator-regions"))


class RegionDeleteView(RegionScreenMixin, View):
    """``POST /coordinator/regions/<id>/delete/`` – usunięcie regionu, którego nikt nie używa.

    Region z profilami jest chroniony przez bazę (``PROTECT`` przy ``Participant.region``,
    ``CommitteeMember.region`` i ``InvitationCode.region``) i tak ma zostać: skasowanie go zabrałoby
    dokumentację odbytych zawodów. Odmowa mówi, **ilu** ludzi to dotyczy i wskazuje drogę wycofania,
    zamiast kończyć się pięćsetką.

    Region z dziećmi znika razem z poddrzewem (``parent`` jest ``CASCADE``), więc dzieci w użyciu
    zatrzymają kasowanie tak samo – i o tym też mówi komunikat.
    """

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        region = self.region_or_404(competition, pk)
        diff = _region_diff(region)
        try:
            with transaction.atomic():
                region.delete()
        except ProtectedError:
            usage = _usage(region)
            messages.error(
                request,
                f"Region „{region.name}” jest w użyciu: uczestników {usage['participants']}, "
                f"członków komitetu {usage['members']}, kodów zaproszeń {usage['codes']}. "
                "Nie da się go usunąć – wyłącz go, żeby zniknął z list wyboru.",
            )
            return redirect(reverse("web:coordinator-regions"))
        audit(request.user, "region.deleted", competition, diff, request=request)
        messages.success(request, f"Region „{diff['name']}” został usunięty.")
        return redirect(reverse("web:coordinator-regions"))


class RegionDefaultsView(RegionScreenMixin, View):
    """``POST /coordinator/regions/defaults/`` – zestaw startowy: kraj, 16 województw, „poza Polską”.

    Wołamy ``apps.accounts.regions.default_regions_for`` – tę samą funkcję, którą wołają komenda
    ``create_competition`` i kreator ``/setup/``, żeby konkurs założony trzema różnymi drogami miał
    dokładnie ten sam zestaw startowy.

    Czynność jest **idempotentna i nie nadpisuje**: region o danym kodzie jest w konkursie jeden
    (więz ``accounts_region_unique_code``), a nazwa i kolejność poprawione tu przez organizatora
    przeżywają ponowne kliknięcie. Dlatego przycisk nie nazywa się „przywróć”, tylko uzupełnia
    braki – i dlatego komunikat mówi, ile wierszy naprawdę przybyło.
    """

    def post(self, request):
        competition = self.competition_or_404(request)
        before = set(Region.objects.for_competition(competition).values_list("code", flat=True))
        with transaction.atomic():
            default_regions_for(competition)
        after = set(Region.objects.for_competition(competition).values_list("code", flat=True))
        created = sorted(after - before)
        if created:
            audit(
                request.user,
                "region.created",
                competition,
                {"codes": created, "source": "defaults"},
                request=request,
            )
            messages.success(request, f"Zestaw startowy uzupełniony: przybyło regionów {len(created)}.")
        else:
            messages.info(
                request,
                "Zestaw startowy już jest w całości – ani jeden region nie został zmieniony.",
            )
        return redirect(reverse("web:coordinator-regions"))
