"""Panel koordynatora: klucze API, odbiorcy webhooków i dziennik doręczeń.

Jeden ekran, bo to jedna sprawa: „kto z zewnątrz ma dostęp do tej olimpiady i co od nas dostaje”.
Rozbicie na trzy podstrony kazałoby koordynatorowi skakać między nimi przy każdej integracji,
a odpowiedź na pytanie „czy partner X działa” wymagałaby złożenia jej z dwóch miejsc.

Formularze mieszkają **tutaj**, a nie w ``apps.web.forms``: opisują wyłącznie ten ekran i wprost
listę zakresów i zdarzeń z ``apps.integrations.models``. Trzymanie ich we wspólnym module
formularzy związałoby warstwę WWW z listą uprawnień integracji, którą zmienia się razem z API.

Widoki wyłącznie orkiestrują – każda reguła (co wolno wpisać, czy klucz da się unieważnić dwa
razy, co się dzieje przy ponownym włączeniu wygaszonego odbiorcy) siedzi w
``apps.integrations.services`` i jest ta sama dla panelu i dla każdej innej drogi.

Klucz w postaci jawnej pokazujemy **raz**, zaraz po wystawieniu, i przenosimy go przez
przekierowanie w sesji – nie w komunikacie ``messages``. Powód jest konkretny: komunikaty bywają
renderowane ponownie na kolejnym ekranie i z założenia są tekstem do pokazania, a to jest hasło.
Sesja koordynatora jest po stronie serwera, więc klucz nie ląduje w ciasteczku ani w historii
przeglądarki, a po pokazaniu znika z niej bezpowrotnie.
"""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.competitions.models import Edition
from apps.competitions.services import current_edition
from apps.core.api import DomainError
from apps.integrations.models import (
    DEFAULT_RATE_LIMIT,
    MAX_RATE_LIMIT,
    SCOPES,
    WEBHOOK_EVENTS,
    ApiKey,
    WebhookDelivery,
    WebhookEndpoint,
)
from apps.integrations.services import (
    api_keys_for_panel,
    create_api_key,
    create_endpoint,
    delete_endpoint,
    deliveries_for_panel,
    resend_delivery,
    revoke_api_key,
    send_test_delivery,
    update_endpoint,
)
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/integrations.html"

#: Ile doręczeń pokazuje dziennik. Ta sama liczba, co domyślna w
#: ``apps.integrations.services.deliveries_for_panel`` – dziennik rośnie z każdym zdarzeniem
#: razy liczba odbiorców, a ekran odpowiada na pytanie „czy ostatnie rzeczy doszły”.
DELIVERY_LIMIT = 50

#: Klucz w sesji, pod którym czeka świeżo wystawiony klucz API – do jednorazowego pokazania.
PLAIN_KEY_SESSION = "integrations_plain_key"


def _edition_field(label: str) -> forms.ModelChoiceField:
    """Pole wyboru edycji wspólne dla obu formularzy. Puste = „wszystkie edycje”.

    Queryset jest tu pusty z rozmysłu i wypełnia go dopiero widok (``_bind_editions``):
    pole powstaje przy imporcie modułu, czyli zanim istnieje jakiekolwiek żądanie, a lista
    edycji jest **zakresowana konkursem**. Domyślne „wszystkie edycje instalacji” byłoby
    listą, z której da się wybrać cudzą – a ``ModelChoiceField`` jest tu realną bramką
    zapisu, nie ozdobą ekranu.
    """
    return forms.ModelChoiceField(
        queryset=Edition.objects.none(),
        required=False,
        label=label,
        empty_label="wszystkie edycje konkursu",
    )


def _bind_editions(form, competition):
    """Wpisuje do formularza edycje **tego** konkursu i oddaje ten sam formularz."""
    form.fields["edition"].queryset = Edition.objects.for_competition(competition).order_by(
        "-created_at", "-id"
    )
    return form


class ApiKeyForm(forms.Form):
    """Formularz wystawienia klucza. Sekret nie jest polem – losuje go serwis."""

    name = forms.CharField(label="Nazwa", max_length=120)
    edition = _edition_field("Edycja")
    scopes = forms.MultipleChoiceField(
        label="Zakresy",
        choices=[(scope, f"{scope} – {description}") for scope, description in SCOPES.items()],
        widget=forms.CheckboxSelectMultiple,
    )
    pii_allowed = forms.BooleanField(label="Dane osobowe dozwolone", required=False)
    rate_limit_per_minute = forms.IntegerField(
        label="Limit żądań na minutę", min_value=1, max_value=MAX_RATE_LIMIT, initial=DEFAULT_RATE_LIMIT
    )


class WebhookForm(forms.Form):
    """Formularz odbiorcy webhooków. Sekret podpisu losuje serwis przy dodaniu."""

    url = forms.CharField(label="Adres (https)", max_length=500)
    edition = _edition_field("Edycja")
    events = forms.MultipleChoiceField(
        label="Zdarzenia",
        choices=[(event, f"{event} – {description}") for event, description in WEBHOOK_EVENTS.items()],
        widget=forms.CheckboxSelectMultiple,
    )


def _redirect():
    return redirect(reverse("web:coordinator-integrations"))


def _endpoint(competition, pk: int) -> WebhookEndpoint:
    """Odbiorca webhooków **tego konkursu** albo 404.

    Zawężenie idzie z managera (``WebhookEndpoint.competition``, § 3.2), więc żaden z czterech
    adresów działających na odbiorcy (zapis, usunięcie, doręczenie próbne, ponowienie) nie może
    go pominąć. 404, a nie 403: istnienie cudzego odbiorcy nie jest informacją tego koordynatora.
    """
    return get_object_or_404(
        WebhookEndpoint.objects.for_competition(competition).select_related("edition"), pk=pk
    )


def _page_context(competition, *, key_form=None, webhook_form=None, plain_key=None) -> dict:
    """Komplet danych ekranu. Jedno miejsce, bo ten sam ekran renderuje wejście i nieudany formularz.

    Wiersz „wszystkie edycje” (pusta ``edition``) jest od wydania D wierszem **tego konkursu**,
    a nie wspólną półką instalacji: klucz i odbiorca mają własną kolumnę ``competition``, bo
    edycja bywa pusta i wtedy nie prowadziła do żadnego właściciela.
    """
    return {
        "edition": current_edition(competition),
        # Listę kluczy składa serwis integracji (jedna definicja „co widać w panelu”) i on sam
        # ją zawęża – zakres jest argumentem, a nie sitem nałożonym na gotowy wynik.
        "keys": api_keys_for_panel(competition=competition),
        "endpoints": list(
            WebhookEndpoint.objects.for_competition(competition).select_related("edition", "created_by")
        ),
        "deliveries": deliveries_for_panel(competition=competition, limit=DELIVERY_LIMIT),
        "key_form": _bind_editions(key_form or ApiKeyForm(), competition),
        "webhook_form": _bind_editions(webhook_form or WebhookForm(), competition),
        "plain_key": plain_key,
        "scopes": SCOPES,
        "events": WEBHOOK_EVENTS,
    }


class IntegrationsView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/integrations/`` – klucze, odbiorcy i ostatnie doręczenia na jednym ekranie."""

    def get(self, request):
        # ``pop`` zamiast ``get``: klucz w postaci jawnej pokazuje się dokładnie raz, a odświeżenie
        # strony ma go już nie pokazać. Inaczej zostawałby w sesji do wylogowania.
        plain_key = request.session.pop(PLAIN_KEY_SESSION, None)
        return TemplateResponse(request, TEMPLATE, _page_context(request.competition, plain_key=plain_key))


class ApiKeyCreateView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/keys/`` – wystawienie klucza.

    Odpowiedź na nieudany formularz jest pełnym ekranem z błędami i kodem 400, a nie
    przekierowaniem z komunikatem: koordynator ma zobaczyć, które pole odrzucono, nie stracić
    wpisanych zakresów.
    """

    def post(self, request):
        form = _bind_editions(ApiKeyForm(request.POST), request.competition)
        if not form.is_valid():
            return self._render(request, form, status=400)
        data = form.cleaned_data
        try:
            key, token = create_api_key(
                name=data["name"],
                scopes=data["scopes"],
                edition=data["edition"],
                # Właściciela podajemy wprost, bo edycja bywa pusta („klucz na wszystkie
                # roczniki”) i wtedy sama nie wskazuje żadnego konkursu.
                competition=request.competition,
                pii_allowed=data["pii_allowed"],
                rate_limit_per_minute=data["rate_limit_per_minute"],
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        request.session[PLAIN_KEY_SESSION] = token
        messages.success(
            request,
            f"Klucz „{key.name}” został wystawiony. Skopiuj go teraz – nie da się go odczytać ponownie.",
        )
        return _redirect()

    def _render(self, request, form: ApiKeyForm, *, status: int):
        return TemplateResponse(
            request, TEMPLATE, _page_context(request.competition, key_form=form), status=status
        )


class ApiKeyRevokeView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/keys/<id>/revoke/`` – unieważnienie klucza."""

    def post(self, request, pk: int):
        key = get_object_or_404(ApiKey.objects.for_competition(request.competition), pk=pk)
        try:
            revoke_api_key(key, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Klucz „{key.name}” został unieważniony.")
        return _redirect()


class WebhookCreateView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/webhooks/`` – dodanie odbiorcy."""

    def post(self, request):
        form = _bind_editions(WebhookForm(request.POST), request.competition)
        if not form.is_valid():
            for error in form.errors.values():
                messages.error(request, " ".join(error))
            return _redirect()
        data = form.cleaned_data
        try:
            endpoint = create_endpoint(
                url=data["url"],
                events=data["events"],
                edition=data["edition"],
                # Jak przy kluczu: odbiorca „na wszystkie edycje” też musi mieć właściciela,
                # bo zdarzenia konkursu A nie mają prawa wyjść na serwer wskazany przez B.
                competition=request.competition,
                actor=request.user,
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Odbiorca {endpoint.url} został dodany.")
        return _redirect()


class WebhookUpdateView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/webhooks/<id>/update/`` – zmiana adresu, zdarzeń i stanu.

    Ten sam adres obsługuje włączenie i wyłączenie odbiorcy, bo to jest zmiana tego samego pola.
    Osobny przycisk „wyłącz” byłby drugą drogą do jednej reguły, czyli drugim miejscem, w którym
    trzeba pamiętać o zerowaniu licznika porażek.
    """

    def post(self, request, pk: int):
        endpoint = _endpoint(request.competition, pk)
        fields: dict = {}
        if "url" in request.POST:
            fields["url"] = request.POST.get("url", "")
        if "events" in request.POST:
            fields["events"] = request.POST.getlist("events")
        if "is_active" in request.POST:
            fields["is_active"] = request.POST.get("is_active") == "1"
        try:
            update_endpoint(endpoint, actor=request.user, request=request, **fields)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, f"Odbiorca {endpoint.url} został zapisany.")
        return _redirect()


class WebhookDeleteView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/webhooks/<id>/delete/`` – usunięcie odbiorcy."""

    def post(self, request, pk: int):
        endpoint = _endpoint(request.competition, pk)
        url = endpoint.url
        delete_endpoint(endpoint, actor=request.user, request=request)
        messages.success(request, f"Odbiorca {url} został usunięty.")
        return _redirect()


class WebhookTestView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/webhooks/<id>/test/`` – doręczenie próbne (``ping``)."""

    def post(self, request, pk: int):
        endpoint = _endpoint(request.competition, pk)
        send_test_delivery(endpoint, actor=request.user, request=request)
        messages.success(
            request,
            "Doręczenie próbne zostało zakolejkowane – jego wynik pojawi się w dzienniku poniżej.",
        )
        return _redirect()


class DeliveryResendView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/integrations/deliveries/<id>/resend/`` – ponowienie z dziennika."""

    def post(self, request, pk: int):
        # Doręczenie dochodzi do konkursu przez swojego odbiorcę (``endpoint__competition``) –
        # tę ścieżkę zna jego manager, więc zawężenie jest tym samym wywołaniem, co wszędzie.
        delivery = get_object_or_404(
            WebhookDelivery.objects.for_competition(request.competition).select_related("endpoint"),
            pk=pk,
        )
        try:
            resend_delivery(delivery, actor=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Doręczenie zostało ponowione.")
        return _redirect()


# --- eksporty do systemów zewnętrznych ----------------------------------------------------------
# Adresy stoją pod ``/coordinator/export/…``, czyli tam, gdzie koordynator szuka plików, ale widoki
# są tutaj: to eksporty **na zewnątrz** (kuratorium, teczka zawodów, migracja), a nie arkusze
# robocze panelu. Wpis audytowy powstaje **przed** oddaniem pliku i niesie wyłącznie rodzaj
# eksportu i liczbę wierszy – tak samo jak w ``coordinator_reports``, bo to ta sama obietnica:
# komplet danych nie wychodzi z systemu bez śladu.


def _stage_from_query(request):
    """Etap z ``?stage=<id>``. Bez parametru 404 – domyślanie się etapu przy danych osobowych
    byłoby najgorszym możliwym rozwiązaniem."""
    from django.http import Http404

    from apps.competitions.models import Stage

    raw = (request.GET.get("stage") or "").strip()
    if not raw.isdigit():
        raise Http404("Eksport etapu wymaga parametru ?stage=<id>.")
    return get_object_or_404(
        Stage.objects.for_competition(request.competition).select_related("edition", "qualification_rule"),
        pk=int(raw),
    )


class KuratoriumExportView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/export/kuratorium/<fmt>/?stage=<id>&voivodeship=<slug>``.

    Województwo jest **obowiązkowe**: kuratorium ma prawo do danych uczniów ze swojego terenu
    i tylko z niego, więc wariant „wszystkie naraz” dla tego odbiorcy nie istnieje.
    """

    def get(self, request, fmt: str):
        from django.http import Http404

        from apps.accounts.models import Voivodeship
        from apps.core import exports as core_exports
        from apps.core.models import audit
        from apps.integrations.exports import kuratorium_dataset

        if fmt not in core_exports.FORMATS:
            raise Http404("Nieznany format eksportu.")
        voivodeship = (request.GET.get("voivodeship") or "").strip()
        if voivodeship not in Voivodeship.values:
            raise Http404("Eksport dla kuratorium wymaga parametru ?voivodeship=<województwo>.")
        stage = _stage_from_query(request)
        dataset = kuratorium_dataset(stage, voivodeship)
        audit(
            request.user,
            "export.generated",
            stage,
            {"kind": "kuratorium", "format": fmt, "voivodeship": voivodeship, "rows": dataset.count},
            request=request,
        )
        return core_exports.build_response(dataset, fmt)


class StageProtocolView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/export/protocol/?stage=<id>`` – protokół etapu w PDF do podpisu."""

    def get(self, request):
        from django.http import HttpResponse

        from apps.core.models import audit
        from apps.integrations.exports import protocol_filename, render_stage_protocol

        stage = _stage_from_query(request)
        pdf = render_stage_protocol(stage)
        audit(request.user, "export.generated", stage, {"kind": "protocol", "format": "pdf"}, request=request)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{protocol_filename(stage)}"'
        return response


class EditionJsonExportView(CoordinatorRequiredMixin, View):
    """``GET /coordinator/export/edition/?edition=<id>`` – zrzut struktury edycji w JSON.

    Bez parametru bierze edycję bieżącą: to jedyny sensowny domyślny wybór, a zrzut nie niesie
    danych osobowych, więc pomyłka co do rocznika nie jest tu wyciekiem, tylko niepotrzebnym
    plikiem.
    """

    def get(self, request):
        import json

        from django.http import Http404, HttpResponse

        from apps.core.models import audit
        from apps.integrations.exports import edition_export

        raw = (request.GET.get("edition") or "").strip()
        if raw.isdigit():
            edition = get_object_or_404(Edition.objects.for_competition(request.competition), pk=int(raw))
        else:
            edition = current_edition(request.competition)
            if edition is None:
                raise Http404("Brak bieżącej edycji.")
        payload = edition_export(edition)
        audit(
            request.user,
            "export.generated",
            edition,
            {"kind": "edition_json", "format": "json", "stages": len(payload["stages"])},
            request=request,
        )
        response = HttpResponse(
            json.dumps(payload, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8"
        )
        label = edition.year_label.replace("/", "-").replace(" ", "_")
        response["Content-Disposition"] = f'attachment; filename="edycja-{label}.json"'
        return response
