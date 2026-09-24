"""Wysyłka komunikatów organizatora – ekran ``/coordinator/messages/``.

Ekran jest dwustopniowy i to jest jego jedyna nietrywialna decyzja projektowa: wypełniony
formularz najpierw pokazuje **podgląd** (liczba odbiorców i treść tak, jak pójdzie w liście),
a dopiero drugie kliknięcie wysyła. Powód jest prosty: wysyłki nie da się cofnąć. Grupa
„uczestnicy bieżącej edycji” to kilka tysięcy osób, a różnica między nią a „zapisani do etapu”
bywa w interfejsie jednym kliknięciem myszy – liczba odbiorców pokazana przed wysyłką jest
jedynym momentem, w którym pomyłka jest jeszcze odwracalna.

Podgląd i wysyłka to **to samo żądanie POST**, rozróżniane polem ``action``. Nie ma tu stanu
w sesji: formularz jedzie drugi raz w komplecie, a odświeżenie podglądu niczego nie psuje.

**Podpis podglądu** (od 24.09.2026). „Formularz jedzie drugi raz w komplecie” nie wystarczało:
przycisk „Wyślij” zostaje na stronie po podglądzie, więc koordynator mógł obejrzeć list do
dwunastu uczniów jednej szkoły, przestawić grupę na „wszyscy uczestnicy konkursu” i kliknąć
„Wyślij” – bez podglądu tego, co naprawdę wychodzi. Podgląd niesie więc ukryte pole z podpisem
(HMAC z kluczem serwisu) grupy, jej parametru, tematu i treści, a wysyłka sprawdza, czy podpis
pasuje do tego, co przyszło. Niezgodność nie wysyła niczego, tylko pokazuje podgląd na nowo.
Liczby odbiorców w podpisie nie ma celowo: grupa rośnie z każdą rejestracją, a podpis ma pilnować
tego, **co** koordynator wybrał, a nie tego, ile osób zdążyło się w międzyczasie zapisać.

Reguły domenowe – kto należy do grupy, jak dzieli się wysyłkę na porcje, co trafia do audytu –
stoją w ``apps.accounts.messaging``. Widok wyłącznie orkiestruje, tak jak reszta panelu.

**Zakres konkursu** (od 24.09.2026, razem z grupą „wszyscy uczestnicy konkursu” i grupami
z parametrem): widok podaje ``request.competition`` trzy razy i za każdym razem celowo – do list
wyboru (etapy, regiony, szkoły, klasy, warsztaty tego konkursu), do ``resolve_recipients`` (zakres
zapytania o odbiorców) i do ``send_broadcast`` (właściciel wiersza w rejestrze). Wcześniej rejestr
brał konkurs z odwrotu ``default_competition``, a grupa „członkowie komitetu” nie miała zakresu
w ogóle – w instalacji z dwiema olimpiadami list do komitetu jednej trafiłby do obu.
"""

from __future__ import annotations

import json

from django.contrib import messages as django_messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.generic import View

from apps.accounts.messaging import (
    grade_choices,
    recent_broadcasts,
    resolve_recipients,
    school_choices,
    send_broadcast,
    workshop_choices,
)
from apps.accounts.models import BroadcastGroup, Region
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.competitions.models import Stage
from apps.competitions.services import current_edition
from apps.web.coordinator_forms import BroadcastForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/messages.html"

#: Wartość pola ``action``, która znaczy „wyślij naprawdę”. Każda inna (także brak) kończy się
#: podglądem – domyślnie zamknięte, bo pomyłka w tę stronę kosztuje jedno kliknięcie więcej,
#: a w drugą kilka tysięcy listów.
ACTION_SEND = "send"

#: Sól podpisu podglądu – osobna przestrzeń HMAC, żeby podpis z tego ekranu nie pasował nigdzie indziej.
PREVIEW_SALT = "apps.web.views.coordinator_messages.preview"


def preview_signature(user, form: BroadcastForm) -> str:
    """Podpis tego, co koordynator obejrzał w podglądzie: grupa, jej parametr, temat i treść.

    Parametr bierzemy z ``recipient_kwargs`` (a nie z ``target``), bo tylko on niesie **treść**
    wklejonej listy adresów – ``target`` jej celowo nie ma. Obiekty (etap, region) wchodzą
    identyfikatorem. Konto koordynatora jest w podpisie, żeby podgląd jednej osoby nie był
    przepustką dla formularza wysłanego przez drugą.
    """
    parameters = {key: getattr(value, "pk", value) for key, value in form.recipient_kwargs().items()}
    payload = json.dumps(
        [
            form.cleaned_data["group"],
            parameters,
            form.cleaned_data["subject"],
            form.cleaned_data["body"],
            getattr(user, "pk", None),
        ],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return salted_hmac(PREVIEW_SALT, payload, algorithm="sha256").hexdigest()


class CoordinatorMessagesView(CoordinatorRequiredMixin, View):
    """``GET`` pokazuje formularz i historię, ``POST`` – podgląd albo wysyłkę."""

    def get(self, request):
        return self._render(request, self._form(request))

    def post(self, request):
        form = self._form(request, request.POST)
        if not form.is_valid():
            return self._render(request, form)
        recipients = resolve_recipients(
            form.cleaned_data["group"],
            # Konkurs żądania – jedyne źródło zakresu. ``resolve_recipients`` porównuje z nim etap
            # i region, więc parametr z cudzego konkursu kończy się pustą grupą, a nie listem.
            competition=request.competition,
            edition=current_edition(request.competition),
            **form.recipient_kwargs(),
        )
        if request.POST.get("action") != ACTION_SEND:
            return self._render(request, form, preview=recipients)
        if not constant_time_compare(
            request.POST.get("preview_signature", ""), preview_signature(request.user, form)
        ):
            django_messages.error(
                request,
                "Grupa odbiorców, temat albo treść zmieniły się od podglądu – nic nie wysłano. "
                "Sprawdź podgląd poniżej i wyślij jeszcze raz.",
            )
            return self._render(request, form, preview=recipients)
        if not recipients:
            # Wysyłka do pustej grupy nie jest błędem użytkownika, tylko informacją: grupa może
            # być pusta, bo nikt się jeszcze nie zapisał. Zapis pustego komunikatu w rejestrze
            # zaśmiecałby historię wpisem, po którym nie poszedł ani jeden list.
            django_messages.error(request, "Ta grupa nie ma ani jednego odbiorcy – nic nie wysłano.")
            return self._render(request, form, preview=recipients)
        broadcast = send_broadcast(
            group=form.cleaned_data["group"],
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            recipients=recipients,
            actor=request.user,
            request=request,
            competition=request.competition,
            target=form.target(),
        )
        django_messages.success(
            request,
            f"Komunikat przekazany do wysyłki: {broadcast.recipient_count} odbiorców.",
        )
        return redirect(reverse("web:coordinator-messages"))

    @staticmethod
    def _form(request, data=None) -> BroadcastForm:
        """Formularz z listami wyboru policzonymi **w obrębie konkursu żądania**.

        Każda lista jest zamknięta: etap spoza bieżącej edycji, region spoza podziału tego konkursu,
        szkoła bez uczniów w tym konkursie i warsztat spoza jego harmonogramu odpadają na walidacji
        pola, zanim ktokolwiek zapyta bazę o odbiorców. Koszt to kilka krótkich zapytań
        grupujących na wyświetlenie ekranu, na który wchodzi się kilka razy w miesiącu.
        """
        competition = request.competition
        return BroadcastForm(
            data,
            stages=CoordinatorMessagesView._stages(competition),
            regions=CoordinatorMessagesView._regions(competition),
            schools=school_choices(competition),
            grades=grade_choices(competition),
            workshops=workshop_choices(competition),
        )

    @staticmethod
    def _stages(competition):
        """Etapy bieżącej edycji – jedyne, do których wolno adresować komunikat.

        Bieżąca edycja, a nie wszystkie: komunikat o terminie dotyczy tegorocznych zawodów,
        a lista z etapami sprzed dwóch lat byłaby wyłącznie zaproszeniem do pomyłki.
        """
        edition = current_edition(competition)
        if edition is None:
            return Stage.objects.none()
        return Stage.objects.filter(edition=edition).select_related("edition").order_by("opens_at", "id")

    @staticmethod
    def _regions(competition):
        """Regiony konkursu z własnym podziałem – albo ``None``, gdy konkurs go nie używa.

        Przy wyłączonej fladze ``custom_regions`` grupę regionalną doprecyzowuje województwo:
        wiersze ``Region`` istnieją wtedy w bazie (migracja ``accounts.0026``), ale żaden ekran ich
        nie czyta, więc lista wyboru z nich byłaby jedynym miejscem, w którym podział konkursu
        wygląda inaczej niż wszędzie indziej. Wycofane regiony zostają na liście, bo profile
        z poprzednich edycji dalej na nie wskazują.
        """
        if competition is None or not competition.has_feature(CUSTOM_REGIONS_FLAG):
            return None
        return Region.objects.for_competition(competition).order_by("position", "name", "id")

    def _render(self, request, form, preview: list[str] | None = None):
        """Strona z formularzem, ewentualnym podglądem i historią wysyłek.

        ``preview`` niesie **listę adresów**, ale do szablonu idzie z niej wyłącznie długość:
        ekran ma powiedzieć „ile”, a nie „komu”. Wyświetlenie kilku tysięcy adresów na stronie
        byłoby wyciągiem z bazy kontaktów pokazanym bez żadnej potrzeby – koordynator i tak nie
        weryfikuje ich po jednym, tylko sprawdza rząd wielkości.

        Podgląd pokazuje też **opis grupy z parametrem** („uczestnicy z wybranej szkoły: XIV LO”)
        – dokładnie to, co trafi do historii. Liczba bez tego opisu nie odróżnia „szkoła, o którą
        chodziło” od „szkoła o podobnej nazwie z sąsiedniego miasta”.
        """
        parameter_map = form.parameter_map()
        context = {
            "form": form,
            "preview": None
            if preview is None
            else {
                "count": len(preview),
                "group": BroadcastGroup(form.cleaned_data["group"]).label,
                "target": form.target(),
                # Podpis jedzie w ukrytym polu formularza; „Wyślij” przejdzie wyłącznie z nim.
                "signature": preview_signature(request.user, form),
            },
            # Rejestr wysyłek **tego** konkursu: historia komunikatów sąsiada nie jest historią
            # tego organizatora (zakres stoi w ``recent_broadcasts``, przed limitem wierszy).
            "broadcasts": recent_broadcasts(request.competition),
            "edition": current_edition(request.competition),
            # Mapa „grupa → pole” dla skryptu, który chowa pola nienależące do wybranej grupy,
            # i zbiór tych pól dla szablonu (tylko one dostają punkt zaczepienia skryptu).
            "parameter_map": parameter_map,
            "parameter_fields": sorted(set(parameter_map.values())),
        }
        return TemplateResponse(request, TEMPLATE, context)
