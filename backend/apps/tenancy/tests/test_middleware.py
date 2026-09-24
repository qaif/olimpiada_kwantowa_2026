"""Warstwa konkursu: co ustawia, co sprząta i czego **nie** zmienia.

Najważniejszy test w tym pliku to ten ostatni: przy jednym konkursie (i przy zerowej ich liczbie)
odpowiedź serwisu ma być taka sama co do bajtu. Warstwa wprowadzona w etapie A ma być niewidoczna
dla uczestnika – widoczność zaczyna się dopiero w wydaniu C, po zakresowaniu odczytów.
"""

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import get_script_prefix, reverse

from apps.tenancy.context import current_competition
from apps.tenancy.middleware import CompetitionMiddleware
from apps.tenancy.models import Competition, RoutingMode

from .conftest import HOST_A, open_path_prefixes


def run(request, *, view=None):
    """Przepuszcza żądanie przez warstwę i oddaje to, co zobaczył widok."""
    seen = {}

    def get_response(processed):
        seen["request_competition"] = processed.competition
        seen["context_competition"] = current_competition()
        seen["path_info"] = processed.path_info
        seen["path"] = processed.path
        seen["script_prefix"] = get_script_prefix()
        seen["reversed_status"] = reverse("status")
        return HttpResponse("ok") if view is None else view(processed)

    seen["response"] = CompetitionMiddleware(get_response)(request)
    return seen


def test_competition_is_available_to_view_and_to_context(competition):
    seen = run(RequestFactory().get("/", HTTP_HOST=HOST_A))

    assert seen["request_competition"] == competition
    assert seen["context_competition"] == competition


# ``unbound_competition`` w dwóch testach niżej: sprzątanie kontekstu da się sprawdzić wyłącznie
# przy kontekście pustym na wejściu. Autouse ``_bind_competition`` (backend/conftest.py) wiąże
# Konkurs #1 dla każdego testu z bazą, więc bez tej fikstury warstwa przywracałaby wartość
# zastaną – i asercja „po odpowiedzi nie ma konkursu” mówiłaby o fiksturze, a nie o warstwie.
def test_context_is_cleared_after_the_response(competition, unbound_competition):
    run(RequestFactory().get("/", HTTP_HOST=HOST_A))

    assert current_competition() is None


def test_context_is_cleared_even_when_the_view_raises(competition, unbound_competition):
    def boom(_request):
        raise RuntimeError("widok się wywrócił")

    with pytest.raises(RuntimeError):
        run(RequestFactory().get("/", HTTP_HOST=HOST_A), view=boom)

    # Wątek obsłuży za chwilę kolejne żądanie – konkurs zostawiony po wyjątku byłby wtedy cudzy.
    assert current_competition() is None


def test_host_without_competition_sets_none(db):
    """Brak konkursu jest wartością, a nie błędem: warstwa nigdy nie kończy żądania sama."""
    Competition.objects.all().delete()

    seen = run(RequestFactory().get("/", HTTP_HOST="testserver"))

    assert seen["request_competition"] is None
    assert seen["response"].status_code == 200


def test_path_prefix_is_stripped_before_the_urlconf(competition, other_competition):
    open_path_prefixes(competition)
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "fizyczna"
    other_competition.save()

    seen = run(RequestFactory().get("/fizyczna/status/", HTTP_HOST=HOST_A))

    assert seen["request_competition"] == other_competition
    # Urlconf widzi adres bez prefiksu…
    assert seen["path_info"] == "/status/"
    # …a ``request.path`` zostaje adresem, o który poprosiła przeglądarka (``?next=``).
    assert seen["path"] == "/fizyczna/status/"
    # …i każdy ``reverse()`` w tym żądaniu produkuje adres z prefiksem, bez zmiany wzorców URL.
    assert seen["reversed_status"] == "/fizyczna/status/"


def test_prefix_without_a_trailing_slash_still_leaves_a_path(competition, other_competition):
    """``/fizyczna`` ma zostać ścieżką ``/`` – pusty napis nie pasuje do żadnego wzorca urlconfa."""
    open_path_prefixes(competition)
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "fizyczna"
    other_competition.save()

    seen = run(RequestFactory().get("/fizyczna", HTTP_HOST=HOST_A))

    assert seen["path_info"] == "/"


def test_script_prefix_does_not_leak_to_the_next_request(competition, other_competition):
    open_path_prefixes(competition)
    other_competition.routing_mode = RoutingMode.PATH
    other_competition.path_prefix = "fizyczna"
    other_competition.save()

    run(RequestFactory().get("/fizyczna/status/", HTTP_HOST=HOST_A))

    assert reverse("status") == "/status/"


def test_domain_competition_keeps_addresses_untouched(competition):
    seen = run(RequestFactory().get("/status/", HTTP_HOST=HOST_A))

    assert seen["path_info"] == "/status/"
    assert seen["reversed_status"] == "/status/"


@pytest.mark.django_db
def test_response_is_identical_with_and_without_a_competition(client):
    """Kryterium wydania A: z konkursem serwis odpowiada bajt w bajt tak, jak bez niego.

    Żądanie idzie z hosta ``testserver``, czyli trafia na witrynę domyślną – tą samą drogą, którą
    na produkcji idzie żądanie spoza listy domen konkursów.
    """
    with_competition = client.get("/healthz/")

    Competition.objects.all().delete()
    without_competition = client.get("/healthz/")

    assert with_competition.status_code == without_competition.status_code == 200
    assert with_competition.content == without_competition.content
