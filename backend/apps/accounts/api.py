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

from .models import CommitteeMember, CommitteeStatus
from .permissions import IsCoordinator
from .serializers import (
    CommitteeRegisteredSerializer,
    CommitteeRegisterSerializer,
    LoginSerializer,
    MeSerializer,
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
        participant = register_participant(**serializer.validated_data)
        return Response(ParticipantRegisteredSerializer(participant).data, status=status.HTTP_201_CREATED)


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
        member = register_committee(**serializer.validated_data)
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
    """Profil zalogowanego użytkownika wraz z rolami."""

    permission_classes = [IsAuthenticated]
    serializer_class = MeSerializer

    @extend_schema(responses={200: MeSerializer})
    def get(self, request):
        return Response(self.get_serializer(request.user).data)


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
    """Potwierdzenie okręgu członka komitetu – tylko koordynator (dług techniczny T-02).

    Do czasu potwierdzenia okręg jest samodeklarowany, więc recenzent nie jest przydzielany
    na etapie okręgowym: reguła konfliktu interesów opiera się na tym polu.
    """

    permission_classes = [IsCoordinator]
    serializer_class = VerifyDistrictSerializer

    @extend_schema(request=VerifyDistrictSerializer, responses={200: PendingCommitteeMemberSerializer})
    def post(self, request, pk: int):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = get_object_or_404(CommitteeMember.objects.select_related("user"), pk=pk)
        member = verify_committee_district(
            member,
            district=serializer.validated_data["district"],
            actor=request.user,
            request=request,
        )
        return Response(PendingCommitteeMemberSerializer(member).data)
