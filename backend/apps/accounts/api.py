"""Widoki API kont (prefiks ``/api/auth/``).

Każdy widok deklaruje ``permission_classes`` jawnie. Rejestracja jest throttlowana scope'em ``register``.
"""

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.core.api import DomainError

from . import twofactor
from .consents import ConsentSource, descriptions
from .models import CommitteeMember, CommitteeStatus
from .permissions import IsCoordinator
from .profile import update_own_names, update_participant_profile
from .serializers import (
    CommitteeRegisteredSerializer,
    CommitteeRegisterSerializer,
    ConsentDefinitionSerializer,
    LoginSerializer,
    MeSerializer,
    MeUpdateSerializer,
    ParticipantRegisteredSerializer,
    ParticipantRegisterSerializer,
    PendingCommitteeMemberSerializer,
    TokenSerializer,
    VerifyDistrictSerializer,
)
from .services import (
    approve_committee_member,
    participant_for,
    register_committee,
    register_participant,
    verify_committee_district,
)


class RegisterParticipantView(GenericAPIView):
    """Rejestracja otwarta uczestnika. Wymaga pary CAPTCHY (``serializers.CaptchaPairMixin``)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"
    serializer_class = ParticipantRegisterSerializer

    @extend_schema(responses={201: ParticipantRegisteredSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        participant = register_participant(
            **serializer.validated_data, source=ConsentSource.API, request=request
        )
        return Response(ParticipantRegisteredSerializer(participant).data, status=status.HTTP_201_CREATED)


class ConsentSetView(GenericAPIView):
    """Treść zgód zbieranych przy rejestracji – publicznie, bez logowania.

    Endpoint istnieje po to, żeby klient zewnętrzny (aplikacja szkoły, integracja organizatora)
    mógł pokazać **to samo** oświadczenie, które pokazuje formularz na stronie, z odnośnikiem do
    tego samego dokumentu i pod tą samą wersją. Bez tego każdy klient wymyślałby własne brzmienie,
    a zgoda zebrana pod cudzą parafrazą nie broni się jako dowód.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    serializer_class = ConsentDefinitionSerializer

    @extend_schema(responses={200: ConsentDefinitionSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(descriptions(), many=True).data)


class RegisterCommitteeView(GenericAPIView):
    """Rejestracja członka komitetu na kod zaproszenia. Wymaga pary CAPTCHY, jak uczestnik."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"
    serializer_class = CommitteeRegisterSerializer

    @extend_schema(responses={201: CommitteeRegisteredSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = register_committee(**serializer.validated_data, request=request)
        return Response(CommitteeRegisteredSerializer(member).data, status=status.HTTP_201_CREATED)


class LoginView(GenericAPIView):
    """Logowanie: token DRF + sesja.

    **Wyłącznie JSON** (``parser_classes``; pakiet 5 po audycie). Widok DRF nie sprawdza CSRF dla
    niezalogowanego żądania, a kończy się ``django.contrib.auth.login()`` – czyli ustawia ciasteczko
    sesji. Z parserami domyślnymi przyjmował też ``application/x-www-form-urlencoded``
    i ``multipart/form-data``, a takie żądanie wysyła **zwykły formularz z obcej strony**, bez
    zgody przeglądarki: napastnik logował przeglądarkę ofiary na **własne** konto (login CSRF)
    i czekał, aż ofiara wpisze tam swoje dane albo wgra pracę. ``application/json`` z obcego
    pochodzenia wymaga preflightu CORS, którego serwis nie przepuszcza – formularz HTML tego typu
    treści wysłać nie umie. Inny typ treści dostaje ``415 UNSUPPORTED_MEDIA_TYPE``, zanim
    cokolwiek zostanie sprawdzone. Formularz ``/login/`` (HTML) ma własny widok z CSRF.
    """

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @extend_schema(responses={200: TokenSerializer})
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()
        user = authenticate(request._request, username=email, password=serializer.validated_data["password"])
        if user is None:
            # Jeden komunikat dla złego hasła i nieistniejącego konta (brak enumeracji użytkowników).
            raise DomainError(
                "Nieprawidłowy e-mail lub hasło.", "INVALID_CREDENTIALS", status.HTTP_400_BAD_REQUEST
            )
        second_factor = self._second_factor(request, user, serializer.validated_data.get("code", ""))
        if second_factor:
            # Nowy token, a nie ``get_or_create``: token sprzed drugiego składnika nie może po
            # tym logowaniu dalej działać, a ``TwoFactorTokenAuthentication`` przyjmuje wyłącznie
            # token młodszy od potwierdzenia urządzenia.
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
        else:
            token, _ = Token.objects.get_or_create(user=user)
        login(request._request, user)
        if second_factor:
            twofactor.mark_verified(request._request)
        return Response({"token": token.key})

    def _second_factor(self, request, user, code: str) -> bool:
        """Drugi składnik przy logowaniu przez API. Zwraca, czy konto go przeszło.

        Bez tego kroku token wydany po samym haśle omijał ``TwoFactorMiddleware``, która pilnuje
        wyłącznie sesji (``apps.accounts.authentication``). Odmowy są **po** sprawdzeniu hasła,
        więc nie mówią nic komuś, kto hasła nie zna; próby kodu liczy ten sam limit ``login``.

        - konto z potwierdzonym urządzeniem: bez kodu ``TWO_FACTOR_REQUIRED``, ze złym
          ``TWO_FACTOR_INVALID`` (kod z aplikacji albo kod zapasowy – ``twofactor.verify``),
        - konto, od którego drugi składnik jest wymagany, a go nie ma: ``TWO_FACTOR_SETUP_REQUIRED``
          – konfiguracja jest w przeglądarce, API jej nie zastępuje,
        - pozostałe konta i wyłączona funkcja: bez zmian.
        """
        if not twofactor.is_enabled():
            return False
        if twofactor.confirmed_device(user) is None:
            if twofactor.is_required_for(user):
                raise DomainError(
                    "To konto musi mieć logowanie dwuskładnikowe. Skonfiguruj je w przeglądarce.",
                    "TWO_FACTOR_SETUP_REQUIRED",
                    status.HTTP_403_FORBIDDEN,
                )
            return False
        if not code:
            raise DomainError(
                "Podaj kod z aplikacji uwierzytelniającej (pole „code”).",
                "TWO_FACTOR_REQUIRED",
                status.HTTP_400_BAD_REQUEST,
            )
        if not twofactor.verify(user, code, request=request._request):
            raise DomainError(
                "Nieprawidłowy kod logowania dwuskładnikowego.",
                "TWO_FACTOR_INVALID",
                status.HTTP_400_BAD_REQUEST,
            )
        return True


class LogoutView(GenericAPIView):
    """Wylogowanie: kasuje token i sesję."""

    permission_classes = [IsAuthenticated]
    serializer_class = TokenSerializer

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(GenericAPIView):
    """Profil zalogowanego użytkownika wraz z rolami; ``PATCH`` edytuje własne dane."""

    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    @extend_schema(responses={200: MeSerializer})
    def get(self, request):
        return Response(self.get_serializer(request.user).data)

    @extend_schema(request=MeUpdateSerializer, responses={200: MeSerializer})
    def patch(self, request):
        """Edycja własnych danych – ten sam serwis, co formularz ``/me/profile/``.

        Pola profilu uczestnika (telefon, szkoła, klasa, rocznik, województwo) przyjmujemy wyłącznie
        od konta, które ten profil ma. Konto komitetu zmienia tą drogą imię i nazwisko; województwo
        zostaje u koordynatora, bo to na nim opiera się reguła konfliktu interesów.
        """
        serializer = MeUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        # Profil **tego** konkursu: konto startujące w dwóch olimpiadach ma dwa profile, a ``PATCH``
        # ma zmienić ten, którego panel klient właśnie ogląda. Konkurs bierzemy z żądania (ustawia
        # go ``apps.tenancy.middleware``), tak samo jak klasy uprawnień w ``permissions.py``.
        participant = participant_for(request.user, getattr(request, "competition", None))
        if participant is not None:
            update_participant_profile(participant, actor=request.user, request=request, **fields)
        else:
            unsupported = set(fields) - {"first_name", "last_name"}
            if unsupported:
                raise DomainError(
                    "To konto nie ma profilu uczestnika – zmienić można wyłącznie imię i nazwisko.",
                    "NOT_A_PARTICIPANT",
                    status.HTTP_400_BAD_REQUEST,
                )
            update_own_names(
                request.user,
                first_name=fields.get("first_name", request.user.first_name),
                last_name=fields.get("last_name", request.user.last_name),
                request=request,
            )
        request.user.refresh_from_db()
        return Response(MeSerializer(request.user).data)


def committee_of(request):
    """Członkowie komitetu **konkursu żądania** – jedyne wejście trzech widoków poniżej.

    Do poprawki po audycie izolacji (01.10.2026) te trzy widoki szukały w ``CommitteeMember.objects``
    bez zawężenia: koordynator konkursu A widział oczekujących członków komitetu B i mógł ich
    zatwierdzić (czyli nadać im rolę recenzenta **w konkursie B**) albo zmienić im województwo.
    Odpowiedniki HTML (``apps.web.views.coordinator``) zawężały od zawsze – API dostaje tę samą
    regułę: członek cudzego konkursu to 404, bo jego istnienie nie jest informacją dla tego
    koordynatora. Żądanie bez konkursu nie widzi nikogo (``for_competition(None)``).
    """
    return CommitteeMember.objects.for_competition(getattr(request, "competition", None)).select_related(
        "user"
    )


class CommitteePendingListView(GenericAPIView):
    """Lista członków komitetu oczekujących na zatwierdzenie – tylko koordynator tego konkursu."""

    permission_classes = [IsCoordinator]
    serializer_class = PendingCommitteeMemberSerializer

    def get_queryset(self):
        return committee_of(self.request).filter(status=CommitteeStatus.PENDING)

    @extend_schema(responses={200: PendingCommitteeMemberSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class CommitteeApproveView(GenericAPIView):
    """Zatwierdzenie członka komitetu (PENDING → ACTIVE) – tylko koordynator."""

    permission_classes = [IsCoordinator]
    serializer_class = PendingCommitteeMemberSerializer

    @extend_schema(request=None, responses={200: PendingCommitteeMemberSerializer})
    def post(self, request, pk: int):
        member = get_object_or_404(committee_of(request), pk=pk)
        member = approve_committee_member(
            member, actor=request.user, competition=getattr(request, "competition", None)
        )
        return Response(self.get_serializer(member).data)


class CommitteeVerifyDistrictView(GenericAPIView):
    """Ustalenie województwa członka komitetu – tylko koordynator.

    Województwo jest opcjonalne i decyduje wyłącznie o konflikcie interesów na etapie
    wojewódzkim: członek bez województwa ocenia prace ze wszystkich województw.
    """

    permission_classes = [IsCoordinator]
    serializer_class = VerifyDistrictSerializer

    @extend_schema(
        request=VerifyDistrictSerializer,
        responses={200: PendingCommitteeMemberSerializer},
        description=(
            "Ustala województwo aktywnego członka komitetu. Województwo jest opcjonalne: pusta "
            'wartość (`""`, `null` lub brak pola) usuwa je i zdejmuje `district_verified`. '
            "Reguła konfliktu interesów wyklucza z oceniania pracy tylko członka, którego "
            "województwo jest **równe** województwu uczestnika, i tylko na etapie wojewódzkim; "
            "członek bez województwa ocenia prace ze wszystkich województw."
        ),
    )
    def post(self, request, pk: int):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_object_or_404(committee_of(request), pk=pk)
        member = verify_committee_district(
            member,
            district=serializer.validated_data.get("district") or None,
            actor=request.user,
            request=request,
            competition=getattr(request, "competition", None),
        )
        return Response(PendingCommitteeMemberSerializer(member).data)
