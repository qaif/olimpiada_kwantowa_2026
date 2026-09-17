"""Widoki API zawodów (prefiks ``/api/competitions/``).

Każdy widok deklaruje ``permission_classes`` jawnie. Widoki tylko orkiestrują – reguły domenowe
są w ``services.py``, kształt odpowiedzi w ``serializers.py``.
"""

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.accounts.permissions import IsCoordinator, IsParticipant
from apps.core.api import DomainError

from .models import Problem, Stage
from .scoping import competition_of, scope_to_competition
from .serializers import CurrentEditionSerializer, StageEntrySerializer
from .services import current_edition, current_stage, entries_for_user, register_for_stage


class CurrentEditionView(GenericAPIView):
    """Publiczny odczyt bieżącej edycji: etapy i zadania etapu bieżącego. Bez danych osobowych."""

    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]
    serializer_class = CurrentEditionSerializer

    @extend_schema(responses={200: CurrentEditionSerializer})
    def get(self, request):
        # Bieżąca edycja **konkursu z żądania**: ten sam adres pod dwiema domenami ma oddać dwie
        # różne edycje, a pod domeną bez konkursu – 404, a nie cudzy harmonogram.
        edition = current_edition(competition_of(request))
        if edition is None:
            raise DomainError(
                "Nie ustawiono bieżącej edycji.", "NO_CURRENT_EDITION", status.HTTP_404_NOT_FOUND
            )
        now = timezone.now()
        stage = current_stage(edition, now)
        context = {**self.get_serializer_context(), "stage": stage, "now": now}
        return Response(CurrentEditionSerializer(edition, context=context).data)


class StageRegisterView(GenericAPIView):
    """Rejestracja zalogowanego uczestnika do etapu eliminacyjnego."""

    permission_classes = [IsParticipant]
    serializer_class = StageEntrySerializer

    @extend_schema(request=None, responses={201: StageEntrySerializer})
    def post(self, request, pk: int):
        from apps.accounts.services import participant_for

        competition = competition_of(request)
        # Etap cudzego konkursu to 404, a nie 403: jego istnienie nie jest informacją dla tego,
        # kto pyta spod innej domeny. 404 wychodzi samo z zawężonego querysetu.
        stage = get_object_or_404(
            scope_to_competition(Stage.objects.select_related("edition"), competition), pk=pk
        )
        # Uczestnik bierze się z request.user, nigdy z body – brak IDOR. Profil jest profilem
        # **w tym konkursie**: konto z profilem w konkursie B nie zapisze się do etapu konkursu A.
        entry = register_for_stage(participant_for(request.user, competition), stage)
        return Response(self.get_serializer(entry).data, status=status.HTTP_201_CREATED)


class MyEntriesView(GenericAPIView):
    """Wpisy zalogowanego uczestnika do etapów."""

    permission_classes = [IsParticipant]
    serializer_class = StageEntrySerializer

    def get_queryset(self):
        # Widok "me" zawsze zawęża do własnego profilu – konto łączące role nie dostanie cudzych wpisów.
        # Zakres konkursu idzie przed rolą (§ 3.5) i robi to ``for_user`` wewnątrz serwisu.
        return entries_for_user(self.request.user, competition_of(self.request)).filter(
            participant__user=self.request.user
        )

    @extend_schema(responses={200: StageEntrySerializer(many=True)})
    def get(self, request):
        return Response(self.get_serializer(self.get_queryset(), many=True).data)


class ProblemStatementView(GenericAPIView):
    """Treść zadania (PDF) – serwowana przez aplikację, nie przez publiczny URL storage.

    Plik jest osiągalny wyłącznie po ``opens_at`` etapu; wcześniej odpowiedź to 404 (nie 403),
    żeby nie ujawniać, czy treść już istnieje.

    Jedyny wyjątek to **koordynator**: to on wgrywa treść z panelu i musi ją obejrzeć, zanim etap
    się otworzy – bez tego jedyną drogą sprawdzenia, czy wgrał właściwy plik, byłoby czekanie do
    otwarcia zawodów. Wyjątek jest wąski (sama grupa ``coordinator``, bez eskalacji superusera)
    i nie zmienia reguły dla nikogo innego: uczestnik, recenzent i anonim dostają przed otwarciem
    to samo 404, co dotąd.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    @extend_schema(responses={(200, "application/pdf"): bytes})
    def get(self, request, pk: int):
        problem = get_object_or_404(
            scope_to_competition(Problem.objects.select_related("stage"), competition_of(request)),
            pk=pk,
        )
        visible = problem.stage.has_opened() or IsCoordinator().has_permission(request, self)
        if not visible or not problem.statement_pdf:
            raise Http404
        # Wersja językowa wybiera się sama: ``statement_file`` oddaje plik angielski, gdy jest
        # i gdy język interfejsu jest angielski, a w każdym innym przypadku polski. Bramka
        # widoczności zostaje przy wersji polskiej – bez niej etap nie ma treści w żadnym języku,
        # więc to ona rozstrzyga, czy zadanie w ogóle istnieje dla świata.
        statement = problem.statement_file
        return FileResponse(
            statement.open("rb"),
            content_type="application/pdf",
            as_attachment=False,
            filename=f"zadanie-{problem.number}.pdf",
        )
