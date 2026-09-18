"""Ekran „Nowy konkurs” ``/coordinator/competitions/new/`` i spis konkursów koordynatora.

Polecenie organizatora brzmi: „pozwól też na tworzenie innych konkursów z poziomu koordynatora
w subdomenach obecnej domeny”. Do tej pory konkurs zakładał wyłącznie administrator serwera —
komendą w kontenerze, po wpisaniu domeny do ``.env`` i po rekordzie w DNS-ie. Ten ekran zdejmuje
z tej czynności całą część serwerową, i tylko ją: konkurs staje pod ``<slug>.<SITE_DOMAIN>``, więc
adres mieści się w rekordzie wieloznacznym i w wildcardzie ustawień, a certyfikat pobiera Caddy
przy pierwszym wejściu. Konkurs pod **własną** domeną zakłada się nadal komendą, bo wymaga wpisu
w DNS-ie, którego panel nie zrobi (i nie ma prawa zrobić).

**Dwie bramki, nie jedna.** Ekran istnieje, gdy zgadzają się obie:

- ``PLATFORM_SUBDOMAINS`` — ustawienie **instalacji**: „na tym serwerze jest rekord wieloznaczny
  i Caddy pyta o certyfikaty”. Bez niego konkurs powstałby pod adresem, który nie odpowiada,
- flaga konkursu ``competition_creation`` — decyzja o **tym organizatorze**: „wolno mu zakładać
  kolejne konkursy”. Konkurs #1 ma ją domyślnie wyłączoną, więc jego panel wygląda jak dotąd.

Brak którejkolwiek daje **404**, a nie 403: adresu, którego w tej instalacji nie ma, nie ma tak
samo, jak adresu cudzego konkursu (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1). Rolę sprawdza wcześniej
i osobno ``CoordinatorRequiredMixin``, więc uczestnik dostaje 403 niezależnie od stanu obu bramek
i nie dowiaduje się z odpowiedzi, jak ta instalacja jest skonfigurowana.

**Dwa kroki: podgląd i potwierdzenie.** Pierwszy ``POST`` wykonuje **całą** czynność z
``dry_run=True`` i wycofuje transakcję, więc pokazuje liczby policzone przez ten sam kod, który za
chwilę je zapisze — a nie listę obietnic spisaną w szablonie. Powód jest ten sam, co przy zmianie
wersji zgody: skutku nie widać na ekranie, na którym się go wywołuje. Tu skutkiem jest witryna
z własnym drzewem stron, edycja, etapy i nowy adres publiczny — czyli dokładnie to, czego nie da
się cofnąć jednym kliknięciem (``Competition.site`` stoi na ``PROTECT``).

**Limit prób konsumuje dopiero potwierdzenie.** ``ThrottledFormMixin`` domyślnie liczy każdy
``POST``, a tutaj każde założenie konkursu to dwa POST-y — przy stawce ``5/day`` podgląd zjadałby
połowę budżetu i uczyłby klikać „Załóż” bez patrzenia. Dlatego ``throttle_on_request = False``
i jawne ``consume_throttle()`` w kroku drugim; ten sam szew, którego używa ekran logowania (tam
odwrotnie: liczy **nieudane** próby).

Ślad decyzji: ``competition.created`` zapisany **w nowym konkursie**, z identyfikatorem, adresem
i nazwą szablonu. Bez danych osobowych: kto założył, mówi kolumna ``actor``, a po co — nie należy
do dziennika audytu.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import View

from apps.core.models import audit
from apps.tenancy.provisioning import (
    WARSAW,
    ProvisioningError,
    create_competition_from_template,
)
from apps.tenancy.resolution import platform_subdomains_enabled
from apps.web.competition_create_forms import NewCompetitionForm
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin

LIST_TEMPLATE = "web/coordinator/competitions.html"
FORM_TEMPLATE = "web/coordinator/competition_new.html"

#: Flaga konkursu, która włącza oba ekrany. Stała, a nie napis w kilku miejscach: czyta ją widok,
#: menu (``apps/web/coordinator_nav.py``) i test strzegący domyślnego menu Konkursu #1.
FEATURE = "competition_creation"

#: Nazwa pola, po którym poznajemy drugi krok. Sam przycisk, bez wartości do zgadywania —
#: potwierdzenie ma być czynnością, a nie wartością przepisaną z pierwszego kroku.
CONFIRM_FIELD = "confirm"

#: Parametr adresu, którym spis konkursów poznaje, że wracamy z udanego założenia. Identyfikator,
#: a nie komunikat w sesji: strona po odświeżeniu ma nadal pokazywać adres nowego konkursu, bo to
#: jedyna rzecz, którą trzeba z tego ekranu wynieść.
CREATED_QUERY_PARAM = "utworzono"


class CompetitionCreationMixin(CoordinatorRequiredMixin):
    """Wspólna bramka obu widoków: rola koordynatora, ustawienie instalacji, flaga konkursu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile wolno z niego zakładać kolejne. Inaczej 404.

        Konkurs bierzemy z ``request.competition`` (ustawia go ``CompetitionMiddleware``), a nie
        z identyfikatora w adresie: nie ma tu adresu, pod którym dałoby się wskazać cudzy konkurs.
        Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli stawia wcześniej
        ``RoleRequiredMixin.dispatch`` — anonim i uczestnik mają dostać 302 albo 403, **zanim**
        odpowiedź zdradzi konfigurację instalacji.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not platform_subdomains_enabled():
            raise Http404("Ta instalacja nie prowadzi konkursów w subdomenach platformy.")
        if not competition.has_feature(FEATURE):
            raise Http404("Zakładanie konkursów z panelu jest w tym konkursie wyłączone.")
        return competition


def coordinated_competitions(user):
    """Konkursy, w których to konto ma rolę koordynatora — w kolejności nazw.

    Pytamy o **członkostwa**, a nie o globalną grupę Django: grupa ``coordinator`` jest jedna dla
    całej instalacji (od niej zależy dostęp do ``/cms/``), więc lista zbudowana z niej pokazałaby
    koordynatorowi jednej olimpiady adresy wszystkich pozostałych. Członkostwo jest jedyną
    odpowiedzią na pytanie „w których konkursach jestem koordynatorem”.
    """
    from apps.accounts.models import CompetitionRole
    from apps.tenancy.models import Competition

    return (
        Competition.objects.filter(
            memberships__user=user,
            memberships__role=CompetitionRole.COORDINATOR,
        )
        .select_related("site")
        .distinct()
        .order_by("name", "id")
    )


class CompetitionListView(CompetitionCreationMixin, View):
    """``GET /coordinator/competitions/`` — konkursy tego konta razem z ich adresami.

    Ekran odpowiada na dwa pytania i na nic więcej: „które konkursy prowadzę” i „pod jakim adresem
    stoi każdy z nich”. Zmiana czegokolwiek w cudzym (a nawet we własnym innym) konkursie do niego
    nie należy — konfigurację konkursu zmienia się **w tym konkursie**, czyli po przejściu pod jego
    adres, bo tam rozstrzyga się izolacja.
    """

    def get(self, request):
        competition = self.competition_or_404(request)
        created_slug = (request.GET.get(CREATED_QUERY_PARAM) or "").strip().lower()
        rows = list(coordinated_competitions(request.user))
        context = {
            "competition": competition,
            "rows": rows,
            # Konkurs właśnie założony – o ile jest wśród konkursów tego konta. Dopasowanie po
            # liście, a nie osobne zapytanie po slugu z adresu: parametr przychodzi od klienta,
            # a odpowiedź ma opisywać wyłącznie konkursy, do których to konto ma prawo.
            "created": next((row for row in rows if row.slug == created_slug), None),
            "platform_domain": settings.SITE_DOMAIN,
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


class CompetitionCreateView(CompetitionCreationMixin, ThrottledFormMixin, View):
    """``GET|POST /coordinator/competitions/new/`` — podgląd, a po potwierdzeniu nowy konkurs."""

    throttle_scope = "competition_create"
    #: Limit konsumuje dopiero potwierdzenie – patrz docstring modułu.
    throttle_on_request = False

    def get(self, request):
        self.competition_or_404(request)
        return self._render(request, NewCompetitionForm())

    def post(self, request):
        self.competition_or_404(request)
        form = NewCompetitionForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)

        confirmed = CONFIRM_FIELD in request.POST
        if confirmed:
            # Dopiero tutaj, i dopiero po przejściu formularza: limit ma trafiać w zakładanie
            # konkursów, a nie w poprawianie literówki w nazwie.
            self.consume_throttle()
        try:
            result = self._provision(request, form, dry_run=not confirmed)
        except ProvisioningError as error:
            # Jedyna droga tutaj prowadzi przez wyścig (ktoś zajął identyfikator między
            # podglądem a potwierdzeniem) albo przez regułę, której formularz nie zna, bo należy
            # do bazy. Komunikat czynności jest wtedy jedyną informacją, co poprawić.
            form.add_error(None, str(error))
            return self._render(request, form, status=400)

        if not confirmed:
            return self._render(request, form, preview=_preview(result))

        audit_payload = {
            "slug": result.competition.slug,
            "domain": result.competition.primary_domain,
            "template": result.template_name,
        }
        _audit_on_new_competition(request, result.competition, audit_payload)
        messages.success(
            request,
            f"Konkurs „{result.competition.name}” został założony pod adresem "
            f"https://{result.competition.primary_domain}/.",
        )
        return redirect(
            f"{reverse('web:coordinator-competitions')}?{CREATED_QUERY_PARAM}={result.competition.slug}"
        )

    # --- pomocnicze ---------------------------------------------------------------------------
    def _provision(self, request, form: NewCompetitionForm, *, dry_run: bool):
        """Czynność zakładania konkursu — ta sama, którą wywołuje ``manage.py create_competition``.

        ``run_safe_seeds`` zostaje przy domyślnym ``False``: seedy szablonu (dziś: ``seed_schools``)
        wpisują kilka tysięcy wierszy wykazu SIO/RSPO **poza** transakcją, czyli robotę, której nie
        wolno wykonać w środku żądania HTTP. Na działającej instalacji wykaz i tak stoi już w bazie,
        bo jest wspólny dla wszystkich konkursów — a odświeża go ``scripts/deploy.sh``.
        """
        data = form.cleaned_data
        return create_competition_from_template(
            slug=data["slug"],
            name=data["name"],
            domain=form.host,
            template=data["template"],
            organizer=data["organizer"],
            contact_email=data["contact_email"],
            short_name=data["short_name"],
            accent=data["accent_colour"],
            edition_label=data["edition_label"],
            # Zakładający zostaje koordynatorem nowego konkursu — tą samą drogą, co
            # ``--coordinator-email`` w komendzie (``grant_role``: członkostwo **i** grupa).
            # Inaczej założyłby konkurs, do którego panelu sam by nie wszedł.
            coordinator=request.user,
            dry_run=dry_run,
            run_safe_seeds=False,
        )

    def _render(self, request, form, *, preview=None, status: int = 200):
        context = {
            "competition": request.competition,
            "form": form,
            "preview": preview,
            "confirm_field": CONFIRM_FIELD,
            "platform_domain": settings.SITE_DOMAIN,
        }
        return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


def _preview(result) -> dict:
    """Podgląd jako **zwykłe wartości**, a nie obiekty z wycofanej transakcji.

    Po ``dry_run`` wiersze mają ``pk`` bez wiersza w bazie, więc każde sięgnięcie po relację,
    której nie wczytano, byłoby zapytaniem o coś, czego już nie ma. Szablon dostaje zatem napisy
    i liczby — a co ważniejsze, dostaje **to samo**, co za chwilę powstanie, bo policzył to ten
    sam kod, który za chwilę zapisze.
    """
    competition = result.competition
    seeded = result.seeded
    return {
        "address": f"https://{competition.primary_domain}/",
        "name": competition.name,
        "short_name": competition.short_name,
        "slug": competition.slug,
        "template_label": result.template["label"],
        "pages": [title for _, _, title in result.template["pages"]],
        "documents": list(result.template["documents"]),
        "edition": result.edition.year_label,
        "stages": [
            {
                "name": stage.name,
                "kind": stage.kind,
                "opens": timezone.localtime(stage.opens_at, WARSAW).date(),
                "deadline": timezone.localtime(stage.deadline_at, WARSAW).date(),
            }
            for stage in result.stages
        ],
        "consents": seeded["consents"],
        "document_templates": seeded["documents"],
        "regions": seeded["regions"],
        "pipeline": seeded["pipeline"],
        "cms_group": seeded["cms_group"],
        "public_code_prefix": competition.public_code_prefix,
        "certificate_prefix": competition.certificate_prefix,
    }


def _audit_on_new_competition(request, competition, payload: dict) -> None:
    """``competition.created`` zapisane w dzienniku **nowego** konkursu, razem z adresem IP.

    ``apps.core.models.audit`` bierze właściciela wpisu z ``request.competition`` (i słusznie:
    zdarzenie zwykle dotyczy konkursu, w którym się wydarzyło). Tutaj jest odwrotnie — zdarzeniem
    jest **powstanie** konkursu, więc jego miejscem jest dziennik tego nowego, a nie tego, z
    którego panelu je wywołano. Podmieniamy więc konkurs żądania na czas jednego wywołania,
    zamiast budować wiersz ``AuditLog`` obok serwisu: druga droga do dziennika audytu byłaby
    pierwszą, w której ktoś pominie adres IP albo aktora.
    """
    previous = getattr(request, "competition", None)
    request.competition = competition
    try:
        audit(request.user, "competition.created", competition, payload, request=request)
    finally:
        request.competition = previous
