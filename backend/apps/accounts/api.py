"""Widoki API kont (prefiks ``/api/auth/``).

Każdy widok deklaruje ``permission_classes`` jawnie. Rejestracja jest throttlowana scope'em ``register``.
"""

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.core.api import DomainError

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
    register_committee,
    register_participant,
    verify_committee_district,
)


class RegisterParticipantView(GenericAPIView):
    """Rejestracja otwarta uczestnika."""

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
    """Rejestracja członka komitetu na kod zaproszenia."""

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
    """Logowanie: token DRF + sesja."""

    permission_classes = [AllowAny]
    serializer_class = LoginSerializer
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
        token, _ = Token.objects.get_or_create(user=user)
        login(request._request, user)
        return Response({"token": token.key})


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
        participant = getattr(request.user, "participant", None)
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


class CommitteePendingListView(GenericAPIView):
    """Lista członków komitetu oczekujących na zatwierdzenie – tylko koordynator."""

    permission_classes = [IsCoordinator]
    serializer_class = PendingCommitteeMemberSerializer

    def get_queryset(self):
        return CommitteeMember.objects.select_related("user").filter(status=CommitteeStatus.PENDING)

    @extend_schema(responses={200: PendingCommitteeMemberSerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class CommitteeApproveView(GenericAPIView):
    """Zatwierdzenie członka komitetu (PENDING → ACTIVE) – tylko koordynator."""

    permission_classes = [IsCoordinator]
    serializer_class = PendingCommitteeMemberSerializer

    @extend_schema(request=None, responses={200: PendingCommitteeMemberSerializer})
    def post(self, request, pk: int):
        member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=pk)
        member = approve_committee_member(member, actor=request.user)
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
        member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=pk)
        member = verify_committee_district(
            member,
            district=serializer.validated_data.get("district") or None,
            actor=request.user,
            request=request,
        )
        return Response(PendingCommitteeMemberSerializer(member).data)
