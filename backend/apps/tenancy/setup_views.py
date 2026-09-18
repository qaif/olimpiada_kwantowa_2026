"""Trzy ekrany kreatora ``/setup/``: operator, konkurs, podsumowanie.

Bramka jest sprawdzana **w każdym widoku osobno i w każdej metodzie** — także w ``POST``. Nie ma
jej we wspólnym ``dispatch`` z jednego powodu: wyścig „dwie przeglądarki otwarte na kroku 1.”
rozstrzyga się między ``GET`` a ``POST``, więc sprawdzenie odziedziczone z formularza
wyrenderowanego minutę wcześniej niczego nie chroni (``dispatch`` robi tu wyłącznie jedno:
zamienia wewnętrzną odmowę na odpowiedź). Odmowa to **404**, a nie 403 — reguła
z etapu 1 § 3.6: „nie ma tego tutaj”. Na produkcji (konkurs i superużytkownik w bazie) każdy adres
pod ``/setup/`` jest więc nieodróżnialny od adresu, którego nigdy nie było.

Warunki są **iloczynem**, a odpowiedź na niespełniony jest zawsze ta sama: 404 bez treści
mówiącej, który z nich zawiódł. Kolejność sprawdzania jest więc wyłącznie kwestią kosztu i stąd
stan bazy stoi pierwszy: na produkcji żądanie z podrobionym tokenem odbija się o dwa ``EXISTS``
i nie dochodzi nawet do porównania sekretu.

**Logowanie operatora przenieśliśmy z kroku 3. do kroku 1.** — to jedyne odstępstwo od tabeli
z § 1.7.1 i ma powód bezpieczeństwa. Po kroku 1. w bazie jest superużytkownik, więc
``setup_available()`` jest już ``False`` i kroki 2.–3. nie mogą stać na tej samej bramce.
Stoją na węższej: zalogowany superużytkownik założony w **tej** sesji kreatora i baza bez
konkursu. Gdyby operator logował się dopiero na końcu, krok 2. byłby przez chwilę publicznym
adresem zakładającym konkurs każdemu, kto ma token.
"""

from __future__ import annotations

import logging

from django.contrib.auth import login
from django.core.management.base import CommandError
from django.db import transaction
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.generic import View

from apps.core.models import audit
from apps.tenancy import setup
from apps.tenancy.setup_forms import CompetitionForm, OperatorForm
from apps.tenancy.templates_catalog import TEMPLATES

logger = logging.getLogger(__name__)

OPERATOR_TEMPLATE = "tenancy/setup_operator.html"
COMPETITION_TEMPLATE = "tenancy/setup_competition.html"
DONE_TEMPLATE = "tenancy/setup_done.html"
#: Własny ekran odmowy przy przekroczeniu limitu, a nie ``web/throttled.html``: tamten dziedziczy
#: po ``base.html``, czyli po szablonie czytającym menu CMS-u i ustawienia witryny. Kreator
#: renderuje się na instalacji, w której ani jednego, ani drugiego jeszcze nie ma (§ 2.5).
THROTTLED_TEMPLATE = "tenancy/setup_throttled.html"
#: Treść odmowy (404). Własna z tego samego powodu, co wyżej, i z jednego jeszcze: standardowe
#: ``templates/404.html`` dziedziczy po ``base.html``, a ten na instalacji **bez konkursu** nie
#: renderuje się wcale (procesor kontekstu rejestracji podnosi ``CompetitionNotResolved``). Odmowa
#: kreatora musi działać dokładnie tam, gdzie tamta strona jeszcze nie działa.
ABSENT_TEMPLATE = "tenancy/setup_absent.html"

#: Backend logowania podany jawnie, mimo że skonfigurowany jest jeden. ``login()`` bez tego
#: argumentu wymaga, żeby konto przyszło z ``authenticate()`` — a to konto powstało przed chwilą
#: komendą i żadnego uwierzytelnienia nie przechodziło.
AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"

#: Adres, na który kreator oddaje operatora po zakończeniu. Stała ścieżka, a nie ``reverse``:
#: ``apps.web.urls`` należy do zadania montażowego wydania, a kreator ma od niego nie zależeć.
COORDINATOR_URL = "/coordinator/"

THROTTLED_MESSAGE = "Zbyt wiele prób z tego adresu. Odczekaj chwilę i spróbuj ponownie."


def render_setup(request, template: str, context: dict, *, status: int = 200) -> HttpResponse:
    """Renderuje ekran kreatora **poza** procesorami kontekstu żądania.

    To jest ta sama decyzja, co „własny szablon bazowy” z § 2.5, tylko dociągnięta do końca.
    Procesory kontekstu (``apps.web.context_processors.registration``, menu CMS-u, marka witryny)
    odpalają się przy renderowaniu **każdego** szablonu przez ``TemplateResponse`` — niezależnie
    od tego, czy szablon o nie prosi. Na instalacji bez konkursu jeden z nich podnosi
    ``CompetitionNotResolved``, więc kreator wywracałby się na odczycie stanu rejestracji
    konkursu, którego ma dopiero co założyć.

    ``render_to_string`` bez ``request`` procesorów nie uruchamia. Znacznik ``{% csrf_token %}``
    działa mimo to, bo czyta zmienną ``csrf_token`` z kontekstu — podajemy ją jawnie, tą samą
    funkcją, której używa procesor CSRF-owy Django.
    """
    payload = {**context, "csrf_token": get_token(request)}
    return HttpResponse(render_to_string(template, payload), status=status)


class SetupViewMixin:
    """Wspólne: limit prób, sprawdzenie tokenu i odmowa nie do odróżnienia od braku adresu."""

    def dispatch(self, request, *args, **kwargs):
        """Zamienia wewnętrzną odmowę na odpowiedź 404 renderowaną własnym szablonem.

        Zwykłe ``raise Http404`` oddałoby obsługę ``templates/404.html``, czyli szablonowi
        dziedziczącemu po ``base.html`` — a ten na instalacji bez konkursu sam podnosi wyjątek.
        Odmowa kreatora musi działać także tam, więc składamy ją sami. Kod odpowiedzi jest ten,
        którego wymaga § 1.7.1: **404**, jednakowe dla każdego powodu odmowy.
        """
        try:
            return super().dispatch(request, *args, **kwargs)
        except _Denied:
            return render_setup(request, ABSENT_TEMPLATE, {}, status=404)

    def deny(self) -> Exception:
        """Jedna odmowa dla wszystkich powodów — patrz docstring modułu."""
        return _Denied()

    def require_token(self, request) -> None:
        if not setup.token_accepted(request):
            raise self.deny()

    def enforce_throttle(self, request):
        """429 z ``Retry-After``, gdy limit prób jest wyczerpany. ``None`` = można dalej."""
        wait = setup.throttle_wait(request)
        if wait is None:
            setup.throttle_consume(request)
            return None
        response = render_setup(request, THROTTLED_TEMPLATE, {"message": THROTTLED_MESSAGE}, status=429)
        response["Retry-After"] = str(int(wait) + 1)
        return response


class SetupOperatorView(SetupViewMixin, View):
    """Krok 1: token, CAPTCHA i konto operatora. Jedyny widok kreatora zakładający konto.

    ``GET /setup/?token=…`` zapisuje token w sesji i **przekierowuje na adres bez parametru**.
    Dzięki temu sekret nie zostaje w pasku adresu, w historii przeglądarki ani w nagłówku
    ``Referer`` wysyłanym z kolejnych kroków. Adres bez tokenu i bez śladu w sesji jest 404.
    """

    def get(self, request):
        if not setup.setup_available():
            raise self.deny()
        redirected = self._consume_token_from_url(request)
        if redirected is not None:
            return redirected
        self.require_token(request)
        return self._render(request, OperatorForm())

    def post(self, request):
        self.require_token(request)
        if not setup.setup_available():
            raise self.deny()
        throttled = self.enforce_throttle(request)
        if throttled is not None:
            return throttled

        form = OperatorForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)

        try:
            operator = self._create(form)
        except _AlreadyDone as exc:
            # Druga przeglądarka, która zdążyła zacząć przed nami: konto jest, ale nie jest nasze.
            raise self.deny() from exc

        login(request, operator, backend=AUTH_BACKEND)
        request.session[setup.SESSION_OPERATOR_KEY] = operator.pk
        return redirect(reverse("setup:competition"))

    def _create(self, form: OperatorForm):
        """Konto operatora pod blokadą doradczą, z **powtórnym** sprawdzeniem bramki pod nią.

        Sprawdzenie sprzed blokady niczego nie chroni: dwa żądania przechodzą je jednocześnie
        i oba idą zakładać konto. Dopiero sprawdzenie **w środku** sekcji krytycznej sprawia, że
        drugie z nich widzi świat po pierwszym.
        """
        with transaction.atomic(), setup.setup_lock():
            if not setup.setup_available():
                raise _AlreadyDone
            return setup.create_operator(
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password1"],
                first_name=form.cleaned_data.get("first_name", ""),
                last_name=form.cleaned_data.get("last_name", ""),
            )

    def _consume_token_from_url(self, request):
        """``?token=…`` → klucz w sesji i przekierowanie na czysty adres. ``None``, gdy nie było."""
        value = request.GET.get(setup.TOKEN_QUERY_PARAM)
        if value is None:
            return None
        if not setup.token_matches(value):
            raise self.deny()
        setup.accept_token(request)
        return redirect(reverse("setup:operator"))

    def _render(self, request, form, *, status: int = 200):
        return render_setup(request, OPERATOR_TEMPLATE, {"form": form, "step": 1}, status=status)


class SetupCompetitionView(SetupViewMixin, View):
    """Krok 2: pierwszy konkurs instalacji. Wpuszcza wyłącznie operatora z kroku 1.

    Bramka jest **węższa** niż ``setup_available()`` i taka być musi — po kroku 1. superużytkownik
    już istnieje. Warunki: token w sesji, zalogowany superużytkownik, ten sam, którego założył
    krok 1. tej sesji, i baza nadal bez konkursu.
    """

    def get(self, request):
        self.require_access(request)
        return self._render(request, CompetitionForm())

    def post(self, request):
        self.require_access(request)
        throttled = self.enforce_throttle(request)
        if throttled is not None:
            return throttled

        form = CompetitionForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)

        try:
            competition, report = self._create(request, form)
        except _AlreadyDone as exc:
            raise self.deny() from exc
        except CommandError as exc:
            # Odmowy komendy („konkurs o tym identyfikatorze już istnieje”, „domena zajęta”,
            # „prefiks zarezerwowany”) są komunikatem dla człowieka, a nie błędem serwera: to on
            # wpisał dane i to on ma je poprawić.
            form.add_error(None, str(exc))
            return self._render(request, form, status=400)

        request.session[setup.SESSION_COMPETITION_KEY] = competition.pk
        request.session[setup.SESSION_TEMPLATE_KEY] = form.cleaned_data["template"]
        request.session[setup.SESSION_REPORT_KEY] = report
        return redirect(reverse("setup:done"))

    def require_access(self, request) -> None:
        self.require_token(request)
        user = request.user
        if not user.is_authenticated or not user.is_superuser:
            raise self.deny()
        if request.session.get(setup.SESSION_OPERATOR_KEY) != user.pk:
            raise self.deny()
        if setup.competition_exists():
            raise self.deny()

    def _create(self, request, form: CompetitionForm):
        with transaction.atomic(), setup.setup_lock():
            if setup.competition_exists():
                raise _AlreadyDone
            competition, report = setup.create_first_competition(
                slug=form.cleaned_data["slug"],
                name=form.cleaned_data["name"],
                domain=form.cleaned_data["domain"],
                template=form.cleaned_data["template"],
                short_name=form.cleaned_data.get("short_name", ""),
                organizer=form.cleaned_data.get("organizer", ""),
                contact_email=form.cleaned_data.get("contact_email", ""),
                coordinator_email=request.user.email,
            )
            # Audyt w tej samej transakcji, co konkurs: wpis o instalacji, która się nie udała,
            # byłby śladem zdarzenia, którego nie ma. Adres IP dokłada ``audit`` z żądania.
            audit(
                request.user,
                setup.AUDIT_ACTION_COMPLETED,
                competition,
                {"slug": competition.slug, "template": form.cleaned_data["template"]},
                request=request,
            )
        return competition, report

    def _render(self, request, form, *, status: int = 200):
        return render_setup(request, COMPETITION_TEMPLATE, {"form": form, "step": 2}, status=status)


class SetupDoneView(SetupViewMixin, View):
    """Krok 3: podsumowanie — linijki do ``.env``, lista dokumentów i wejście do panelu.

    Kreator ``.env`` **nie zmienia**: plik należy do administratora serwera, a aplikacja chodzi
    w kontenerze, który go nie widzi. Wypisuje więc dokładnie to, co wypisuje komenda z powłoki —
    i wypisuje to z jej raportu, a nie ze swojego, drugiego wyliczenia tych samych linijek.
    """

    def get(self, request):
        self.require_token(request)
        user = request.user
        if not user.is_authenticated or not user.is_superuser:
            raise self.deny()
        competition = self._competition(request)
        if competition is None:
            raise self.deny()
        template_name = request.session.get(setup.SESSION_TEMPLATE_KEY, "")
        context = {
            "step": 3,
            # Konto jawnie w kontekście: ekran renderuje się poza procesorami kontekstu żądania
            # (patrz ``render_setup``), więc zmiennej ``user`` nikt tu nie podstawi za nas.
            "operator": user,
            "competition": competition,
            "report": request.session.get(setup.SESSION_REPORT_KEY, ""),
            "documents": TEMPLATES.get(template_name, {}).get("documents", ()),
            "coordinator_url": COORDINATOR_URL,
        }
        return render_setup(request, DONE_TEMPLATE, context)

    def _competition(self, request):
        from apps.tenancy.models import Competition

        pk = request.session.get(setup.SESSION_COMPETITION_KEY)
        if not pk:
            return None
        return Competition.objects.select_related("site").filter(pk=pk).first()


class _Denied(Exception):
    """Wewnętrzna odmowa kreatora – ``SetupViewMixin.dispatch`` zamienia ją na odpowiedź 404."""


class _AlreadyDone(Exception):
    """Ktoś zdążył pierwszy — stan sprawdzony pod blokadą różni się od stanu sprzed niej."""
