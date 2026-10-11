"""Wejście do pokoi wideo przez platformę i pokoje bez terminu (v0.39.0, „wariant A”).

Własne Jitsi wpuszcza wyłącznie z przepustką (``apps.competitions.jitsi_jwt``), a przepustka
powstaje **tutaj**, w chwili wejścia – po sprawdzeniu, kto wchodzi i czy wolno mu teraz wejść.
Każdy widok wejścia kończy się tak samo: 302 na ``https://meet…/<pokój>#jwt="…"``
z ``Cache-Control: no-store`` (``never_cache``) i ``Referrer-Policy: no-referrer``, więc ani
przeglądarka, ani pośrednik nie zachowa odpowiedzi z tokenem, a token nie ląduje w logu – dziennik
dostępu zapisuje adres **żądania** (nasz widok, bez tokenu), a nie nagłówek ``Location``.

Wejścia **zalogowanych** (uczestnik, koordynator, komisja) są GET-em: widok niczego nie zmienia
(poza wpisem audytu), tylko przekierowuje – i musi dać się otworzyć z listu („Wejście na rozmowę:
…”) jednym kliknięciem po zalogowaniu. Podrobione kliknięcie z obcej strony daje napastnikowi tyle,
co nic: przepustka trafia do przeglądarki **zalogowanego**, a nie do napastnika. Limit (scope
``video``, per konto) liczy także GET (``throttle_methods``).

Wejście **linkiem-zaproszeniem** (bramka, bez konta) jest inne: GET wyłącznie pokazuje stronę
z etykietą pokoju i polem na nazwę, a przepustkę wystawia dopiero POST „Dołącz” z tokenem CSRF.
Podgląd linku w komunikatorze, skaner poczty czy prefetch przeglądarki otwierają więc stronę, ale
nie pokój. Limit liczony po adresie IP (scope ``video_gateway``).

Widoki:

- uczestnik (``/me/…``): rozmowa i pokój „na próbę” – wyłącznie własny zapis, wyłącznie w oknie
  czasowym terminu,
- koordynator (``/coordinator/…``): wejście do pokoju terminu jako moderator; pokoje bez terminu
  (zakładanie, „Pokaż linki”, wymiana linku, zamykanie, uprawnienia komisji),
- komisja (``/review/video-rooms/…``): wejście do pokoi udostępnionych komisji i – z uprawnieniem
  od koordynatora – własne pokoje z linkami-zaproszeniami,
- bramka (``/zaproszenie/wideo/<klucz>/``): wejście z linku-zaproszenia, bez konta.

Bez skonfigurowanego sekretu przepustek wszystkie te adresy odpowiadają 404 (``VideoFeatureMixin``),
a ekrany pokazują linki tak, jak przed v0.39.0.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.generic import View

from apps.accounts.models import CommitteeMember, CommitteeStatus
from apps.competitions import room_access
from apps.competitions.jitsi_jwt import (
    issue,
    join_url,
    jwt_enabled,
    room_of,
    short_name,
)
from apps.competitions.models import InterviewSlot, Stage
from apps.competitions.video_rooms import (
    DISPLAY_NAME_MAX_LENGTH,
    VARIANTS,
    RoomCreator,
    VideoRoom,
    clean_display_name,
    close_room,
    close_rooms_of,
    committee_member,
    create_room,
    gateway_join_url,
    panel_join_url,
    panel_joinable,
    room_for_key,
    room_issuer,
    rotate_link,
    set_room_issuer,
    validity_choices,
    view_room_links,
)
from apps.core.api import DomainError
from apps.web.coordinator_forms import VideoRoomForm
from apps.web.mixins import CoordinatorRequiredMixin, ParticipantRequiredMixin, RoleRequiredMixin
from apps.web.throttle import ThrottledFormMixin

COORDINATOR_ROOMS_TEMPLATE = "web/coordinator/video_rooms.html"
COMMITTEE_ROOMS_TEMPLATE = "web/reviewer/video_rooms.html"
GATEWAY_TEMPLATE = "web/video_gateway.html"

#: Ile pokoi zamkniętych i wygasłych pokazuje historia. Lista ma przypominać, co było, a nie być
#: archiwum – pełną historię niesie audyt.
HISTORY_LIMIT = 50

#: Komunikat po zamknięciu pokoju – jeden dla koordynatora i autora.
CLOSED_MESSAGE = (
    "Pokój zamknięty: od tej chwili nikt już do niego nie wejdzie – ani z panelu, ani linkiem-"
    "zaproszeniem. Osoby, które są teraz w trwającej rozmowie, zostają w niej do wyjścia."
)


# --- wspólne -------------------------------------------------------------------------------------


class VideoFeatureMixin:
    """Bramka całej funkcji: bez sekretu przepustek adresy z tego modułu nie istnieją (404)."""

    def dispatch(self, request, *args, **kwargs):
        if not jwt_enabled():
            raise Http404("Przepustki do pokoi wideo nie są skonfigurowane.")
        return super().dispatch(request, *args, **kwargs)


class JoinViewMixin(VideoFeatureMixin, ThrottledFormMixin):
    """Wejście zalogowanego: limit per konto także na GET, odpowiedź bez pamięci podręcznej."""

    throttle_scope = "video"
    throttle_methods = ("GET",)

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


def _no_referrer(response):
    """``Referrer-Policy: no-referrer`` – z odpowiedzi związanej z przepustką nie wychodzi ``Referer``."""
    response["Referrer-Policy"] = "no-referrer"
    return response


def _form_page(response):
    """Strona **z formularzem** (ekran pokoi, bramka linku): ``Referrer-Policy: same-origin``.

    Nie ``no-referrer``: przy tej polityce przeglądarka wysyła formularz POST z nagłówkiem
    ``Origin: null``, a ``CsrfViewMiddleware`` porównuje ``Origin`` z hostem żądania – każde
    „Utwórz pokój”, „Pokaż linki” i „Dołącz” kończyło się więc stroną „Formularz wymaga
    odświeżenia” (błąd z wdrożenia v0.39.0; klient testowy Django nagłówka ``Origin`` nie wysyła,
    więc testy tego nie widziały). ``same-origin`` zostawia ``Origin`` i ``Referer`` wyłącznie dla
    naszej domeny, a na zewnątrz – także do Jitsi – nie wychodzi nic, czyli klucz z adresu bramki
    dalej nie wycieka. Ten sam powód stoi w ``apps.cms.djcms_sso`` przy przekazaniu do django CMS.
    """
    response["Referrer-Policy"] = "same-origin"
    return response


def _redirect_to_room(url: str) -> HttpResponseRedirect:
    """302 do pokoju (adres z przepustką we fragmencie)."""
    return _no_referrer(HttpResponseRedirect(url))


def _audit_join(user, obj, *, role: str, kind: str, request) -> None:
    """``interview.joined`` – kto (rola, nie osoba) i dokąd (rozmowa czy próba). Bez tokenu i danych."""
    from apps.core.models import audit

    audit(user, "interview.joined", obj, {"role": role, "kind": kind}, request=request)


def _when(value) -> str:
    return timezone.localtime(value).strftime("%d.%m.%Y, %H:%M")


# --- pokój rozmowy etapu: wspólne dla dostawców (Jitsi, LiveKit) ----------------------------------


class StageRoomFeatureMixin:
    """Bramka wejść do pokoi **rozmów etapu**: przepustki Jitsi **albo** serwer LiveKit (STAGE-LK-01).

    Bez żadnego z nich adresy nie istnieją (404) – dokładnie jak przed STAGE-LK-01 przy samym Jitsi.
    Pokój, którego dostawca nie jest skonfigurowany, i tak kończy się 404 w ``room_access``.
    """

    def dispatch(self, request, *args, **kwargs):
        from apps.webinars import livekit

        if not jwt_enabled() and not livekit.configured() and not _has_livekit_stages(request):
            raise Http404("Przepustki do pokoi wideo nie są skonfigurowane.")
        return super().dispatch(request, *args, **kwargs)


def _has_livekit_stages(request) -> bool:
    """Konkurs z etapem-rozmową w LiveKit (STAGE-LK-01, M-3): pokój ``livekit://`` zostaje pokojem
    platformy także po wyłączeniu serwera, więc jego adresy wejścia mają odpowiedzieć „serwer wideo
    nie odpowiada” (502 przy tokenie), a nie 404. Zapytanie tylko wtedy, gdy nie ma żadnego serwera."""
    from apps.competitions.models import Stage
    from apps.competitions.video import VideoProvider

    competition = getattr(request, "competition", None)
    if competition is None:
        return False
    return Stage.objects.for_competition(competition).filter(video_provider=VideoProvider.LIVEKIT).exists()


class StageJoinViewMixin(StageRoomFeatureMixin, ThrottledFormMixin):
    """Jak :class:`JoinViewMixin`, ale dla pokoi rozmów etapu (oba dostawcy)."""

    throttle_scope = "video"
    throttle_methods = ("GET",)

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


def _enter(room_pass: room_access.RoomPass, *, livekit_url: str) -> HttpResponseRedirect:
    """Bilet dostawcy dla rozstrzygnięcia ``room_access``: Jitsi – 302 z przepustką we fragmencie,
    LiveKit – 302 na stronę pokoju na platformie (token pobiera tam JS, POST-em)."""
    if room_access.provider_of(room_pass.url) == "livekit":
        return _no_referrer(HttpResponseRedirect(livekit_url))
    token = issue(
        room_of(room_pass.url),
        not_before=room_pass.not_before,
        expires_at=room_pass.expires_at,
        display_name=room_pass.display_name,
        moderator=room_pass.moderator,
    )
    return _redirect_to_room(join_url(room_pass.url, token))


# --- uczestnik -----------------------------------------------------------------------------------


class _ParticipantInterviewMixin(ParticipantRequiredMixin, StageJoinViewMixin):
    """Własny zapis uczestnika w etapie tego konkursu albo 404 – innej drogi do cudzego pokoju nie ma."""

    def booking(self, request, stage_id: int):
        stage = get_object_or_404(
            Stage.objects.for_competition(request.competition).select_related("edition"), pk=stage_id
        )
        # Zapis odwołany nie istnieje (``cancel_booking`` go kasuje), a zdyskwalifikowany uczestnik
        # nie staje przed komisją; pokój poza platformą (albo bez adresu) nie potrzebuje biletu –
        # panel pokazuje wtedy zwykły link. Reguła: ``apps.competitions.room_access``.
        found = room_access.participant_booking(stage, self.participant)
        if found is None:
            raise Http404("Nie masz zapisu na rozmowę w tym etapie.")
        booking, url = found
        return stage, booking, url

    def back(self):
        return redirect(f"{reverse('web:me')}#rozmowa")


class InterviewJoinView(_ParticipantInterviewMixin, View):
    """``GET /me/stages/<id>/interview/join/`` – wejście na własną rozmowę, w oknie terminu."""

    def get(self, request, stage_id: int):
        stage, booking, url = self.booking(request, stage_id)
        try:
            room_pass = room_access.participant_pass(booking, url, request.user, kind=room_access.INTERVIEW)
        except room_access.RoomNotYet as exc:
            messages.info(
                request,
                _("Pokój rozmowy otworzy się %(when)s (czas polski) – wróć wtedy do panelu.")
                % {"when": _when(exc.opens_at)},
            )
            return self.back()
        except room_access.RoomOver:
            messages.error(request, _("Czas tej rozmowy minął – pokój jest już zamknięty."))
            return self.back()
        if room_access.provider_of(room_pass.url) != "livekit":  # LiveKit: audyt przy tokenie
            _audit_join(request.user, booking.entry, role="participant", kind="interview", request=request)
        return _enter(room_pass, livekit_url=reverse("web:interview-room", args=[stage.pk, "interview"]))


class InterviewPrecheckView(_ParticipantInterviewMixin, View):
    """``GET /me/stages/<id>/interview/precheck/`` – pusty pokój „na próbę” (``…-test``), krótko."""

    def get(self, request, stage_id: int):
        stage, booking, url = self.booking(request, stage_id)
        try:
            room_pass = room_access.participant_pass(booking, url, request.user, kind=room_access.PRECHECK)
        except room_access.RoomOver:
            messages.error(request, _("Czas tej rozmowy minął – próba sprzętu nie jest już potrzebna."))
            return self.back()
        if room_access.provider_of(room_pass.url) != "livekit":
            _audit_join(request.user, booking.entry, role="participant", kind="precheck", request=request)
        return _enter(room_pass, livekit_url=reverse("web:interview-room", args=[stage.pk, "precheck"]))


# --- pokój terminu: koordynator i komisja ---------------------------------------------------------


class _SlotRoomMixin(StageJoinViewMixin):
    """Termin **tego** konkursu i jego pokój na platformie – inaczej 404. Wejście jako gospodarz.

    Wspólne dla koordynatora (ekran terminów) i komisji (panel recenzenta i komisji odwoławczej):
    rozmowę prowadzi komisja, a koordynator może wejść zawsze. Różni je wyłącznie bramka roli
    (mixin przed tym w MRO), rola w audycie (``join_role``), strona powrotu (:meth:`back`) i strona
    pokoju LiveKit (``room_url_name``).
    """

    join_role = ""
    room_url_name = ""

    def slot(self, request, pk: int):
        slot = get_object_or_404(
            InterviewSlot.objects.for_competition(request.competition)
            .select_related("stage")
            .prefetch_related("bookings"),
            pk=pk,
        )
        url = room_access.slot_room_url(slot)
        if not url:
            raise Http404("Ten termin nie ma pokoju na Jitsi platformy.")
        return slot, url

    def back(self, slot):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def join(self, request, pk: int):
        """Bilet moderatora w oknie terminu (to samo okno, co uczestnika)."""
        slot, url = self.slot(request, pk)
        try:
            room_pass = room_access.staff_pass(
                slot, url, request.user, role=self.join_role, kind=room_access.INTERVIEW
            )
        except room_access.RoomNotYet as exc:
            messages.info(request, f"Pokój tego terminu otworzy się {_when(exc.opens_at)} (czas polski).")
            return self.back(slot)
        except room_access.RoomOver:
            messages.error(request, "Czas tego terminu minął – pokój jest już zamknięty.")
            return self.back(slot)
        if room_access.provider_of(room_pass.url) != "livekit":  # LiveKit: audyt przy tokenie
            _audit_join(request.user, slot, role=self.join_role, kind="interview", request=request)
        return _enter(room_pass, livekit_url=reverse(self.room_url_name, args=[slot.pk, "interview"]))

    def precheck(self, request, pk: int):
        """Pokój „na próbę” terminu (``…-test``), krótki bilet bez moderatora."""
        slot, url = self.slot(request, pk)
        room_pass = room_access.staff_pass(
            slot, url, request.user, role=self.join_role, kind=room_access.PRECHECK
        )
        if room_access.provider_of(room_pass.url) != "livekit":
            _audit_join(request.user, slot, role=self.join_role, kind="precheck", request=request)
        return _enter(room_pass, livekit_url=reverse(self.room_url_name, args=[slot.pk, "precheck"]))


class _SlotJoinMixin(CoordinatorRequiredMixin, _SlotRoomMixin):
    """Koordynator: prawo z ekranu terminów (rola koordynatora konkursu + zawężony queryset)."""

    join_role = "coordinator"
    room_url_name = "web:coordinator-interview-slot-room"

    def back(self, slot):
        return redirect(reverse("web:coordinator-stage-interviews", args=[slot.stage_id]))


class SlotJoinView(_SlotJoinMixin, View):
    """``GET /coordinator/interview-slots/<id>/join/`` – koordynator jako moderator, w oknie terminu."""

    def get(self, request, pk: int):
        return self.join(request, pk)


class SlotPrecheckView(_SlotJoinMixin, View):
    """``GET /coordinator/interview-slots/<id>/precheck/`` – pokój „na próbę” terminu, bez moderatora."""

    def get(self, request, pk: int):
        return self.precheck(request, pk)


# --- pokoje bez terminu: wspólne dla koordynatora i autora ---------------------------------------


def _rooms_context(rooms, now) -> dict:
    """Podział listy pokoi na otwarte i historię – jedno przejście po liście, bez zapytań."""
    active, history = [], []
    for room in rooms:
        (active if room.is_open(now) else history).append(room)
    return {"active_rooms": active, "history_rooms": history[:HISTORY_LIMIT]}


class _RoomsScreen(VideoFeatureMixin, ThrottledFormMixin, View):
    """Ekran pokoi – lista, formularz założenia, pokazanie linków jednego pokoju.

    Podklasa mówi, **czyje** pokoje widać (:meth:`rooms`), w jakiej roli się je zakłada
    (``created_as``) i jak nazywa się adres listy. Odpowiedzi są ``no-store``: po kliknięciu
    „Pokaż linki” strona niesie poświadczenia.
    """

    throttle_scope = "video_rooms"
    template_name = ""
    created_as = RoomCreator.COORDINATOR
    #: Nazwy adresów czynności na pokoju (``links``, ``rotate``, ``close``, ``join``) – wspólny
    #: fragment szablonu linków nie wie, czy stoi na ekranie koordynatora, czy komisji.
    url_names: dict[str, str] = {}

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def rooms(self, request):  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def extra_context(self, request) -> dict:
        return {}

    def render(self, request, form=None, *, links=None, shown=None, status=200):
        now = timezone.now()
        committee = self.created_as == RoomCreator.COMMITTEE
        rooms = list(self.rooms(request).select_related("created_by").order_by("-created_at", "-id"))
        context = {
            "form": form if form is not None else VideoRoomForm(committee=committee),
            "links": links,
            "shown": shown,
            "now": now,
            "validity_choices": validity_choices(committee=committee),
            "url_names": self.url_names,
            **_rooms_context(rooms, now),
            **self.extra_context(request),
        }
        return _form_page(TemplateResponse(request, self.template_name, context, status=status))

    def get(self, request):
        return self.render(request)

    def post(self, request):
        """Założenie pokoju. Po sukcesie od razu strona z linkami nowego pokoju (to też „pokazanie”)."""
        form = VideoRoomForm(request.POST, committee=self.created_as == RoomCreator.COMMITTEE)
        if not form.is_valid():
            return self.render(request, form, status=400)
        try:
            room = create_room(
                request.competition,
                request.user,
                label=form.cleaned_data["label"],
                validity_days=form.cleaned_data["validity_days"],
                committee_access=form.cleaned_data["committee_access"],
                committee_as_moderator=form.cleaned_data["committee_as_moderator"],
                created_as=self.created_as,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.render(request, form, status=exc.status_code)
        links = view_room_links(room, request.user, request=request)
        return self.render(request, links=links, shown=room)


class _RoomActionMixin(VideoFeatureMixin, ThrottledFormMixin):
    """Czynność na jednym pokoju z listy (POST). Pokój spoza listy tej osoby – 404.

    Limit per konto (``video``) i ``no-store`` – „Pokaż linki” odpowiada stroną z poświadczeniami.
    """

    list_url_name = ""
    throttle_scope = "video"

    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def room(self, request, pk: int) -> VideoRoom:  # pragma: no cover - klasa abstrakcyjna
        raise NotImplementedError

    def back(self):
        return redirect(reverse(self.list_url_name))


class _RoomLinksMixin(_RoomActionMixin):
    """„Pokaż linki”: POST + CSRF, odpowiedź ``no-store``, wpis ``video.room_links_viewed``.

    POST, a nie GET, bo pokazanie linku jest dostępem do poświadczenia: odnośnik GET otwierałby je
    każdy prefetch przeglądarki i każdy podgląd adresu, zostawiając przy tym wpisy w audycie.
    """

    screen_class: type[_RoomsScreen]

    def post(self, request, pk: int):
        room = self.room(request, pk)
        links = view_room_links(room, request.user, request=request)
        if links is None:
            messages.error(
                request,
                "Ten pokój jest zamknięty albo wygasł – jego linki już nikogo nie wpuszczą, "
                "więc ich nie pokazujemy.",
            )
            return self.back()
        screen = self.screen_class()
        screen.setup(request)
        return screen.render(request, links=links, shown=room)


class _RoomRotateMixin(_RoomActionMixin):
    """„Wygeneruj nowy link” dla jednego wariantu (``link=host|guest``). Stary link od razu 404."""

    #: Nowy link to nowe poświadczenie – ten sam budżet, co zakładanie pokoi.
    throttle_scope = "video_rooms"

    def post(self, request, pk: int):
        room = self.room(request, pk)
        variant = request.POST.get("link", "")
        if variant not in VARIANTS:
            raise Http404("Nieznany rodzaj linku.")
        try:
            rotate_link(room, variant, request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.back()
        messages.success(
            request,
            "Nowy link gospodarza jest gotowy – poprzedni już nie działa."
            if variant == "host"
            else "Nowy link gościa jest gotowy – poprzedni już nie działa.",
        )
        return self.back()


class _RoomCloseMixin(_RoomActionMixin):
    def post(self, request, pk: int):
        close_room(self.room(request, pk), request.user, request=request)
        messages.success(request, CLOSED_MESSAGE)
        return self.back()


# --- koordynator ----------------------------------------------------------------------------------


def _issuer_rows(competition, now) -> list[dict]:
    """Aktywni członkowie komisji tego konkursu z liczbą ich otwartych pokoi – dwa zapytania."""
    members = list(
        CommitteeMember.objects.for_competition(competition)
        .filter(status=CommitteeStatus.ACTIVE)
        .select_related("user")
        .order_by("user__last_name", "user__first_name", "id")
    )
    open_counts: dict[int, int] = {}
    for user_id in (
        VideoRoom.objects.for_competition(competition)
        .filter(created_as=RoomCreator.COMMITTEE, closed_at__isnull=True, expires_at__gt=now)
        .values_list("created_by_id", flat=True)
    ):
        open_counts[user_id] = open_counts.get(user_id, 0) + 1
    return [{"member": member, "open_rooms": open_counts.get(member.user_id, 0)} for member in members]


class CoordinatorVideoRoomsView(CoordinatorRequiredMixin, _RoomsScreen):
    """``GET|POST /coordinator/video-rooms/`` – wszystkie pokoje konkursu, zakładanie, uprawnienia komisji."""

    template_name = COORDINATOR_ROOMS_TEMPLATE
    created_as = RoomCreator.COORDINATOR
    url_names = {
        "links": "web:coordinator-video-room-links",
        "rotate": "web:coordinator-video-room-rotate",
        "close": "web:coordinator-video-room-close",
        "join": "web:coordinator-video-room-join",
    }

    def rooms(self, request):
        return VideoRoom.objects.for_competition(request.competition)

    def extra_context(self, request) -> dict:
        return {
            "issuers": _issuer_rows(request.competition, timezone.now()),
            "committee_cap": max(validity_choices(committee=True)),
        }


class _CoordinatorRoomMixin(CoordinatorRequiredMixin):
    list_url_name = "web:coordinator-video-rooms"

    def room(self, request, pk: int) -> VideoRoom:
        return get_object_or_404(
            VideoRoom.objects.for_competition(request.competition).select_related("competition"), pk=pk
        )


class CoordinatorVideoRoomLinksView(_CoordinatorRoomMixin, _RoomLinksMixin, View):
    """``POST /coordinator/video-rooms/<id>/links/`` – linki **dowolnego** pokoju konkursu."""

    screen_class = CoordinatorVideoRoomsView


class CoordinatorVideoRoomRotateView(_CoordinatorRoomMixin, _RoomRotateMixin, View):
    """``POST /coordinator/video-rooms/<id>/rotate/`` – wymiana linku gospodarza albo gościa."""


class CoordinatorVideoRoomCloseView(_CoordinatorRoomMixin, _RoomCloseMixin, View):
    """``POST /coordinator/video-rooms/<id>/close/`` – koordynator zamyka **dowolny** pokój konkursu."""


class CoordinatorVideoRoomJoinView(CoordinatorRequiredMixin, JoinViewMixin, View):
    """``GET /coordinator/video-rooms/<id>/join/`` – koordynator wchodzi do pokoju jako gospodarz."""

    def get(self, request, pk: int):
        room = get_object_or_404(
            VideoRoom.objects.for_competition(request.competition).select_related(
                "competition", "created_by"
            ),
            pk=pk,
        )
        try:
            url = panel_join_url(
                room, request.user, moderator=True, role="coordinator", request=request, check_creator=False
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return redirect(reverse("web:coordinator-video-rooms"))
        return _redirect_to_room(url)


class CoordinatorVideoIssuerView(CoordinatorRequiredMixin, VideoFeatureMixin, View):
    """``POST /coordinator/video-rooms/issuers/<id>/`` – nadanie albo odebranie prawa zakładania pokoi.

    Wartość przychodzi jawnie (``granted=1/0``), a nie jako „odwróć”: podwójne wysłanie formularza
    ma dawać ten sam wynik. Członek komisji spoza tego konkursu (albo nieaktywny) – 404.
    """

    def post(self, request, pk: int):
        member = get_object_or_404(
            CommitteeMember.objects.for_competition(request.competition).filter(
                status=CommitteeStatus.ACTIVE
            ),
            pk=pk,
        )
        granted = request.POST.get("granted") == "1"
        set_room_issuer(member, granted=granted, actor=request.user, request=request)
        messages.success(
            request,
            "Uprawnienie nadane: ta osoba może zakładać pokoje i wystawiać linki-zaproszenia."
            if granted
            else "Uprawnienie odebrane: ekran pokoi znika, a panel nie wpuszcza już do pokoi tej osoby. "
            "Jej linki-zaproszenia działają, dopóki nie zamkniesz jej pokoi (przycisk w tym samym wierszu).",
        )
        return redirect(f"{reverse('web:coordinator-video-rooms')}#komisja")


class CoordinatorVideoIssuerCloseRoomsView(CoordinatorRequiredMixin, VideoFeatureMixin, View):
    """``POST /coordinator/video-rooms/issuers/<id>/close-rooms/`` – zamyka wszystkie otwarte pokoje
    członka komisji naraz. Osobna, jawna decyzja – odebranie uprawnienia jej nie podejmuje."""

    def post(self, request, pk: int):
        member = get_object_or_404(CommitteeMember.objects.for_competition(request.competition), pk=pk)
        count = close_rooms_of(member.user, request.competition, request.user, request=request)
        messages.success(request, f"Zamknięte pokoje tej osoby: {count}.")
        return redirect(f"{reverse('web:coordinator-video-rooms')}#komisja")


# --- komisja --------------------------------------------------------------------------------------


def _committee_home(request) -> str:
    """Panel, do którego wraca członek komisji: recenzenta, a bez tej roli – komisji odwoławczej."""
    from apps.accounts.services import active_reviewer_profile

    if active_reviewer_profile(request.user, request.competition) is not None:
        return reverse("web:review-list")
    return reverse("web:appeals")


class CommitteeRequiredMixin(RoleRequiredMixin):
    """Członek komisji **tego** konkursu: aktywny recenzent albo członek komisji odwoławczej."""

    role_denied_message = "Ta strona jest dostępna wyłącznie dla członków komisji."

    def has_role(self, user) -> bool:
        return committee_member(user, self.competition) is not None


class _IssuerMixin(CommitteeRequiredMixin):
    """Ekran pokoi komisji: wyłącznie z uprawnieniem od koordynatora. Bez niego – 404, nie 403.

    404, bo dla osoby bez uprawnienia ten ekran **nie istnieje** (jak każdy ekran za flagą); 403
    mówiłoby „jest tu coś, czego ci nie wolno”. Kolejność: najpierw rola (``RoleRequiredMixin`` –
    302 dla anonima, 403 dla obcej roli, w tym uczestnika), potem uprawnienie.
    """

    list_url_name = "web:committee-video-rooms"

    def dispatch(self, request, *args, **kwargs):
        if (
            request.user.is_authenticated
            and committee_member(request.user, self.competition) is not None
            and room_issuer(request.user, self.competition) is None
        ):
            raise Http404("Nie masz uprawnienia do zakładania pokoi wideo.")
        return super().dispatch(request, *args, **kwargs)

    def rooms(self, request):
        """Wyłącznie **własne** pokoje, założone w roli członka komisji, w tym konkursie."""
        return VideoRoom.objects.for_competition(request.competition).filter(
            created_by=request.user, created_as=RoomCreator.COMMITTEE
        )

    def room(self, request, pk: int) -> VideoRoom:
        return get_object_or_404(self.rooms(request).select_related("competition"), pk=pk)


class CommitteeVideoRoomsView(_IssuerMixin, _RoomsScreen):
    """``GET|POST /review/video-rooms/`` – własne pokoje członka komisji z uprawnieniem."""

    template_name = COMMITTEE_ROOMS_TEMPLATE
    created_as = RoomCreator.COMMITTEE
    url_names = {
        "links": "web:committee-video-room-links",
        "rotate": "web:committee-video-room-rotate",
        "close": "web:committee-video-room-close",
        "join": "web:committee-video-room-join",
    }


class CommitteeVideoRoomLinksView(_IssuerMixin, _RoomLinksMixin, View):
    """``POST /review/video-rooms/<id>/links/`` – linki **własnego** pokoju."""

    screen_class = CommitteeVideoRoomsView


class CommitteeVideoRoomRotateView(_IssuerMixin, _RoomRotateMixin, View):
    """``POST /review/video-rooms/<id>/rotate/`` – wymiana linku własnego pokoju."""


class CommitteeVideoRoomCloseView(_IssuerMixin, _RoomCloseMixin, View):
    """``POST /review/video-rooms/<id>/close/`` – członek komisji zamyka wyłącznie **własny** pokój."""


class CommitteeVideoRoomJoinView(CommitteeRequiredMixin, JoinViewMixin, View):
    """``GET /review/video-rooms/<id>/join/`` – wejście członka komisji z panelu (krótka przepustka).

    Wpuszcza do pokoju **udostępnionego komisji** (moderator według ustawienia pokoju) albo do
    **własnego** pokoju autora z uprawnieniem (zawsze jako gospodarz). Każdy inny pokój – także
    pokój innego konkursu – kończy się 404; pokój zamknięty, wygasły albo autora bez uprawnienia –
    komunikatem, nigdy przepustką.
    """

    def get(self, request, pk: int):
        room = get_object_or_404(
            VideoRoom.objects.for_competition(request.competition).select_related(
                "created_by", "competition"
            ),
            pk=pk,
        )
        own = room.created_as == RoomCreator.COMMITTEE and room.created_by_id == request.user.pk
        if not own and not room.committee_access:
            raise Http404("Ten pokój nie jest udostępniony komisji.")
        if not panel_joinable(room):
            messages.error(request, "Ten pokój jest zamknięty albo stracił ważność.")
            return redirect(_committee_home(request))
        url = panel_join_url(
            room,
            request.user,
            moderator=True if own else room.committee_as_moderator,
            role="creator" if own else "committee",
            request=request,
        )
        return _redirect_to_room(url)


# --- komisja: pokój terminu rozmowy --------------------------------------------------------------


class _CommitteeSlotMixin(CommitteeRequiredMixin, _SlotRoomMixin):
    """Komisja prowadzi rozmowy (decyzja właściciela z 2.10.2026): każdy aktywny członek komisji
    **tego** konkursu – recenzent albo komisja odwoławcza – wchodzi do pokoju dowolnego terminu
    jako gospodarz. Termin innego konkursu – 404 (zawężony queryset), obca rola – 403."""

    join_role = "committee"
    room_url_name = "web:committee-interview-slot-room"

    def back(self, slot):
        return redirect(_committee_home(self.request))


class CommitteeSlotJoinView(_CommitteeSlotMixin, View):
    """``GET /review/interview-slots/<id>/join/`` – członek komisji wchodzi na rozmowę jako moderator."""

    def get(self, request, pk: int):
        return self.join(request, pk)


class CommitteeSlotPrecheckView(_CommitteeSlotMixin, View):
    """``GET /review/interview-slots/<id>/precheck/`` – próba sprzętu członka komisji."""

    def get(self, request, pk: int):
        return self.precheck(request, pk)


# --- bramka linku-zaproszenia (bez konta) ---------------------------------------------------------


@method_decorator(never_cache, name="dispatch")
class VideoGatewayView(VideoFeatureMixin, ThrottledFormMixin, View):
    """``GET|POST /zaproszenie/wideo/<klucz>/`` – wejście z linku-zaproszenia, bez konta.

    - klucz spoza konkursu tego adresu, wymieniony albo zmyślony – 404 (bez śladu, że pokój był),
    - pokój zamknięty albo wygasły – 410 ze zdaniem „pokój jest zamknięty”, **bez etykiety**: link
      bywa przekazywany dalej, a etykieta (np. „konsultacja z dr X”) nie jest informacją dla kogoś,
      kto już nie ma tam wstępu,
    - otwarty – GET: strona z etykietą i polem nazwy (zalogowany może je pominąć – nazwa z konta);
      POST „Dołącz”: przepustka na ``JITSI_JWT_GATEWAY_MINUTES``, moderator wyłącznie z linku
      gospodarza, 302 do pokoju.

    Limit po adresie IP (``video_gateway``) liczy wyłącznie POST. ``never_cache`` na stronie
    i na przekierowaniu; ``Referrer-Policy``: ``same-origin`` na stronie (formularz – patrz
    ``_form_page``), ``no-referrer`` na przekierowaniu. Adres strony **jest** linkiem, a przy
    ``same-origin`` nie wychodzi w ``Referer`` poza naszą domenę.
    """

    throttle_scope = "video_gateway"

    def resolve(self, request, key: str):
        found = room_for_key(request.competition, key)
        if found is None:
            raise Http404("Nie ma takiego zaproszenia.")
        return found

    def render(self, request, room, variant, *, error: str = "", name: str = "", status: int = 200):
        context = {
            "room": room if room.is_open() else None,
            "variant": variant,
            "account_name": short_name(request.user) if request.user.is_authenticated else "",
            "name": name,
            "error": error,
            "name_max_length": DISPLAY_NAME_MAX_LENGTH,
        }
        return _form_page(TemplateResponse(request, GATEWAY_TEMPLATE, context, status=status))

    def get(self, request, key: str):
        room, variant = self.resolve(request, key)
        if not room.is_open():
            return self.render(request, room, variant, status=410)
        return self.render(request, room, variant)

    def post(self, request, key: str):
        room, variant = self.resolve(request, key)
        if not room.is_open():
            return self.render(request, room, variant, status=410)
        account_name = short_name(request.user) if request.user.is_authenticated else ""
        raw_name = request.POST.get("name", "")
        name = clean_display_name(raw_name) or account_name
        if not name:
            return self.render(
                request,
                room,
                variant,
                error=_("Podaj imię i nazwisko albo nazwę, pod którą zobaczą Cię inni."),
                status=400,
            )
        try:
            url = gateway_join_url(room, variant, display_name=name, user=request.user, request=request)
        except DomainError:
            return self.render(request, room, variant, status=410)
        return _redirect_to_room(url)
