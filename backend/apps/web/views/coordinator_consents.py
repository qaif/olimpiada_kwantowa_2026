"""Ekran „Zgody konkursu” ``/coordinator/consents/``.

Jedyne miejsce, w którym organizator zmienia **brzmienie oświadczeń** zbieranych przy rejestracji:
treść z odnośnikiem do dokumentu, podpowiedź, komunikat o braku, kolejność w formularzu, udział
w zestawie i – osobno, z potwierdzeniem – wersję dokumentu.

**Dlaczego ekran jest za flagą ``per_competition_consents``.** Dopóki flaga jest wyłączona, zestaw
zgód czyta się ze stałej (``apps.accounts.consents.consent_set``), a wiersze ``ConsentDefinition``
leżą w bazie nieużywane. Ekran, który pozwalałby je wtedy zmieniać, obiecywałby zmianę, której
uczestnik nigdy by nie zobaczył – czyli kłamałby w sprawie, w której kłamać najmniej wolno. Adres
przy wyłączonej fladze daje **404**, a nie 403 (``docs/UNIWERSALNY-ETAP-2.md`` § 2.1): adresu,
którego w tej instalacji nie ma, nie ma tak samo jak adresu cudzego konkursu. Rola sprawdza się
wcześniej i osobno (``CoordinatorRequiredMixin``), więc uczestnik dostaje 403 niezależnie od flagi
i nie dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Czego tu nie ma i dlaczego.**

- **Dodawania i kasowania definicji.** Rodzaje zgód (``ConsentKind``) są zamkniętą listą w kodzie,
  bo po rodzaju poznaje zgodę model dowodowy i reguła „opiekun dla niepełnoletniego”; wiersze
  zakłada migracja albo ``create_competition``. Wycofanie zgody z zestawu jest **odznaczeniem
  „aktywna”**, a nie skasowaniem wiersza – wpisy dowodowe z lat poprzednich mają dalej mieć w bazie
  treść, pod którą je złożono.
- **Wymagalności** (``required``, ``required_for_minor``). Zgoda na regulamin i klauzula RODO są
  podstawą przetwarzania danych, a zgoda opiekuna – warunkiem udziału osoby niepełnoletniej.
  Odznaczenie któregokolwiek z tych pól jednym kliknięciem zamieniłoby formularz rejestracji
  w zbieranie danych bez podstawy, a to jest decyzja organizatora podejmowana poza panelem
  (migracja albo ``/admin/``). Ekran je **pokazuje**, bo trzeba o nich wiedzieć, czytając treść.
- **Hurtowej zmiany wersji.** Patrz ``apps.accounts.consents.change_version``: „nowelizacja
  regulaminu” i „nowa wersja wzoru zgody opiekuna” to dwa różne zdarzenia z dwiema różnymi datami.

**Zmiana wersji ma własny, dwukrokowy adres.** Pierwszy POST wpisuje nową wersję i **nic nie
zapisuje** – oddaje stronę z jednym zdaniem: „od tej chwili nowe zgody będą zapisywane pod wersją
X”. Dopiero drugi POST, z tej strony, woła serwis. Powód jest taki sam, jak przy usuwaniu konta:
skutku nie widać na ekranie, na którym się go wywołuje. Granica przesuwa się w ``ConsentRecord``
kolejnych osób, czyli w dowodach, których nikt nie ogląda w dniu zmiany.

Audyt: wersję zapisuje serwis (``consent_definition.version_changed`` z różnicą pól), a zwykłą
edycję treści – ten widok, wpisem ``consent_definition.updated`` z **nazwami** zmienionych pól.
Nazwami, a nie parami „było → jest”, tak samo jak przy ``competition.updated``: treść oświadczenia
bywa długa na akapit i dziennik audytu nie jest jej repozytorium (od tego jest historia zmian
dokumentu w ``/cms/``).
"""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.consents import CONSENT_FEATURE_FLAG, change_version, consent_set
from apps.accounts.models import ConsentDefinition
from apps.core.models import audit
from apps.web.coordinator_forms import ConsentDefinitionForm, ConsentVersionForm
from apps.web.mixins import CoordinatorRequiredMixin

LIST_TEMPLATE = "web/coordinator/consents.html"
FORM_TEMPLATE = "web/coordinator/consent_form.html"
CONFIRM_TEMPLATE = "web/coordinator/consent_version_confirm.html"

#: Przełącznik, który włącza ten ekran – ta sama stała, którą czyta ``consent_set``. Napis
#: w dwóch miejscach dałby ekran włączony flagą, której nikt nie czyta przy renderowaniu zgód.
FEATURE = CONSENT_FEATURE_FLAG

#: Nazwa pola, po którym poznajemy drugi krok zmiany wersji. Sam przycisk, bez wartości do
#: zgadywania: potwierdzenie ma być czynnością, a nie wartością przepisaną z pierwszego kroku.
CONFIRM_FIELD = "confirm"


class ConsentScreenMixin(CoordinatorRequiredMixin):
    """Wspólna bramka trzech widoków: rola koordynatora, flaga konkursu, zakres querysetu."""

    def competition_or_404(self, request):
        """Konkurs żądania, o ile ma włączone zgody własne. Inaczej 404.

        Konkurs bierzemy z ``request.competition`` (ustawia go ``CompetitionMiddleware``), a nie
        z identyfikatora w adresie – dzięki temu nie ma tu adresu, pod którym dałoby się wskazać
        cudze zgody. Sprawdzenie stoi w metodzie widoku, a nie w ``dispatch``, bo bramkę roli
        stawia wcześniej ``RoleRequiredMixin.dispatch``: anonim i uczestnik mają dostać 302 albo
        403, **zanim** odpowiedź zdradzi stan przełącznika tego konkursu.
        """
        competition = getattr(request, "competition", None)
        if competition is None or not competition.has_feature(FEATURE):
            raise Http404("Ten konkurs czyta zgody ze wspólnego zestawu – ekran jest wyłączony.")
        return competition

    def definition_or_404(self, competition, pk: int) -> ConsentDefinition:
        """Definicja **tego** konkursu albo 404 – zakres wychodzi z querysetu, nie z widoku."""
        return get_object_or_404(ConsentDefinition.objects.for_competition(competition), pk=pk)


def _definitions(competition):
    """Komplet definicji konkursu, w kolejności z ``Meta.ordering`` (``ordering``, potem ``id``).

    Komplet, a więc razem z wyłączonymi: zgoda odznaczona jako nieaktywna znika z formularza
    rejestracji, ale ma zostać widoczna tutaj – inaczej jedyną drogą do jej przywrócenia byłby
    ``/admin/``.
    """
    return list(ConsentDefinition.objects.for_competition(competition))


def _render_form(request, definition, form, version_form, *, status: int = 200):
    """Ekran jednej definicji. Wspólny dla wejścia, nieudanego zapisu i nieudanej zmiany wersji.

    Dwa formularze na jednej stronie, bo to dwie różne czynności o dwóch różnych skutkach: zmiana
    treści działa od najbliższego otwarcia formularza rejestracji, zmiana wersji przesuwa granicę
    w dowodach. Każdy ma własny adres POST, więc nieudany zapis jednego nie czyści drugiego.
    """
    context = {
        "definition": definition,
        "form": form,
        "version_form": version_form,
        # Zestaw obowiązujący **naprawdę**, liczony tym samym wejściem, co formularz rejestracji.
        # Ekran ma pokazywać stan, a nie powtarzać założenie: gdyby konkurs został bez ani jednej
        # aktywnej definicji, ``consent_set`` schodzi do zestawu domyślnego i trzeba to widzieć.
        "live_fields": [consent.field_name for consent in consent_set(request.competition)],
    }
    return TemplateResponse(request, FORM_TEMPLATE, context, status=status)


class ConsentListView(ConsentScreenMixin, View):
    """``GET /coordinator/consents/`` – zgody konkursu: rodzaj, pole, wersja, wymagalność, stan."""

    def get(self, request):
        competition = self.competition_or_404(request)
        definitions = _definitions(competition)
        live = [consent.field_name for consent in consent_set(competition)]
        context = {
            "definitions": definitions,
            "live_fields": live,
            # „Czy to, co widać na liście, jest tym, co widzi uczestnik”. Odpowiedź bywa
            # przecząca dokładnie w jednym przypadku – zero aktywnych definicji – i wtedy trzeba
            # o tym powiedzieć wprost, a nie zostawić pustą tabelę do interpretacji.
            "falls_back_to_defaults": not any(item.is_active for item in definitions),
        }
        return TemplateResponse(request, LIST_TEMPLATE, context)


class ConsentEditView(ConsentScreenMixin, View):
    """``GET|POST /coordinator/consents/<id>/`` – treść, odnośnik, komunikaty, kolejność, stan."""

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        definition = self.definition_or_404(competition, pk)
        return _render_form(
            request, definition, ConsentDefinitionForm(instance=definition), ConsentVersionForm()
        )

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        definition = self.definition_or_404(competition, pk)
        form = ConsentDefinitionForm(request.POST, instance=definition)
        if not form.is_valid():
            return _render_form(request, definition, form, ConsentVersionForm(), status=400)
        # ``changed_data`` liczymy **przed** zapisem: potem instancja ma już nowe wartości, ale
        # ``form.initial`` nadal pamięta stan sprzed formularza.
        changed = list(form.changed_data)
        saved = form.save()
        if changed:
            audit(
                request.user,
                "consent_definition.updated",
                saved,
                {"kind": saved.kind, "field_name": saved.field_name, "fields": changed},
                request=request,
            )
            messages.success(request, f"Zgoda „{saved.get_kind_display()}” została zapisana.")
        else:
            # Zapis bez zmiany nie jest błędem i nie zostawia śladu – formularz bywa otwierany
            # po to, żeby przeczytać treść, a wpis audytowy o zmianie, której nie było, psuje
            # jedyny dziennik, który ktoś naprawdę czyta po latach.
            messages.info(request, "Nic się nie zmieniło – treść zgody została bez zmian.")
        return redirect(reverse("web:coordinator-consents"))


class ConsentVersionView(ConsentScreenMixin, View):
    """``POST /coordinator/consents/<id>/version/`` – zmiana wersji dokumentu w dwóch krokach.

    ``GET`` nie ma tu czego pokazać i odsyła na ekran zgody: wersję zmienia się **stamtąd**,
    a osobna strona z pustym polem byłaby czwartym miejscem, w którym stoi numer wersji.
    """

    def get(self, request, pk: int):
        competition = self.competition_or_404(request)
        self.definition_or_404(competition, pk)
        return redirect(reverse("web:coordinator-consent-edit", args=[pk]))

    def post(self, request, pk: int):
        competition = self.competition_or_404(request)
        definition = self.definition_or_404(competition, pk)
        form = ConsentVersionForm(request.POST)
        if not form.is_valid():
            return _render_form(
                request, definition, ConsentDefinitionForm(instance=definition), form, status=400
            )
        version = form.cleaned_data["version"]
        if version == definition.version:
            # Ta sama wersja nie jest błędem, tylko brakiem zmiany – tak samo widzi to serwis.
            # Krok potwierdzenia byłby wtedy pytaniem o zgodę na nic.
            messages.info(request, "To jest wersja, która już obowiązuje – nic nie zmieniono.")
            return redirect(reverse("web:coordinator-consent-edit", args=[pk]))
        if CONFIRM_FIELD not in request.POST:
            context = {"definition": definition, "version": version, "confirm_field": CONFIRM_FIELD}
            return TemplateResponse(request, CONFIRM_TEMPLATE, context)
        try:
            change_version(definition, version, actor=request.user, request=request)
        except ValidationError as error:
            # Jedyna droga tutaj prowadzi przez wiersz, którego ``full_clean`` nie przepuszcza
            # z innego powodu niż wersja (np. treść z literówką w nawiasach klamrowych wpisana
            # przed tym ekranem). Komunikat serwisu jest wtedy jedyną informacją, co poprawić.
            messages.error(request, "; ".join(error.messages))
            return _render_form(
                request,
                definition,
                ConsentDefinitionForm(instance=definition),
                ConsentVersionForm(initial={"version": version}),
                status=400,
            )
        messages.success(
            request,
            f"Od tej chwili nowe zgody będą zapisywane pod wersją {version}. "
            "Wcześniejsze oświadczenia zostały bez zmian.",
        )
        return redirect(reverse("web:coordinator-consent-edit", args=[pk]))
