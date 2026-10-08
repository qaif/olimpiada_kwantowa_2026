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

**Eksport odbiorców** (MSG-EXPORT-01, prośba organizatora z 8.10.2026): trzeci przycisk tego samego
formularza, „Eksportuj do Excela”, oddaje plik .xlsx z imieniem, nazwiskiem i adresem odbiorców
wybranej grupy – np. do zaproszenia wysyłanego spoza platformy. Parametry grupy waliduje ta sama
forma, co przy podglądzie (bez tematu i treści), a odbiorców liczy ``recipient_rows`` – ta sama
droga przez ``recipient_users``, co wysyłka, więc plik nie może pokazać kogoś, do kogo list by nie
poszedł. Wpis audytu (``export.generated``) powstaje przed oddaniem pliku i niesie grupę, jej
parametr i liczbę wierszy – nigdy dane.

**Komunikaty z datą przyszłą** (MSG-SCHED-01, prośba organizatora z 8.10.2026): pole „Wyślij później”
zamienia „Wyślij” w „Zaplanuj”. Ten sam podgląd i ten sam podpis (termin wchodzi do podpisu), ale
zamiast ``send_broadcast`` widok woła ``schedule_broadcast`` – wiersz w stanie „zaplanowana” bez
listy adresów, bo odbiorców liczy zadanie beat w chwili wysyłki. Podgląd pokazuje więc liczbę
**dzisiejszą** z dopiskiem, że to nie jest liczba ostateczna. Zaplanowany komunikat da się anulować
(``CoordinatorBroadcastCancelView``) do chwili, w której beat go wyśle.
"""

from __future__ import annotations

import json

from django.contrib import messages as django_messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.formats import date_format
from django.utils.text import slugify
from django.utils.timezone import localtime
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.accounts.messaging import (
    cancel_scheduled_broadcast,
    grade_choices,
    recent_broadcasts,
    recipient_rows,
    resolve_recipients,
    schedule_broadcast,
    scheduled_broadcasts,
    school_choices,
    send_broadcast,
    workshop_choices,
)
from apps.accounts.models import BroadcastGroup, MessageBroadcast, Region
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.competitions.models import Stage
from apps.competitions.services import current_edition
from apps.core.exports import Dataset, xlsx_response
from apps.core.models import audit
from apps.web.coordinator_forms import BroadcastForm
from apps.web.mixins import CoordinatorRequiredMixin

TEMPLATE = "web/coordinator/messages.html"

#: Wartość pola ``action``, która znaczy „wyślij naprawdę”. Każda inna (także brak) kończy się
#: podglądem – domyślnie zamknięte, bo pomyłka w tę stronę kosztuje jedno kliknięcie więcej,
#: a w drugą kilka tysięcy listów.
ACTION_SEND = "send"

#: Wartość pola ``action`` przycisku „Eksportuj do Excela” – plik z odbiorcami zamiast podglądu.
ACTION_EXPORT = "export"


def export_filename(group: str) -> str:
    """Nazwa pliku eksportu bez daty (datę dokleja ``apps.core.exports``): ``odbiorcy-<grupa>``.

    Z polskiej nazwy grupy, a nie z jej kodu, bo plik ląduje w „Pobranych” obok innych i ma się
    dać rozpoznać bez otwierania. ``slugify`` zostawia wyłącznie ASCII (nagłówek
    ``Content-Disposition``), ale „ł” nie ma rozkładu Unicode i bez podmiany by znikało.
    """
    label = str(BroadcastGroup(group).label).replace("ł", "l").replace("Ł", "L")
    return f"odbiorcy-{slugify(label)}"


#: Sól podpisu podglądu – osobna przestrzeń HMAC, żeby podpis z tego ekranu nie pasował nigdzie indziej.
PREVIEW_SALT = "apps.web.views.coordinator_messages.preview"


def preview_signature(user, form: BroadcastForm) -> str:
    """Podpis tego, co koordynator obejrzał w podglądzie: grupa, jej parametr, temat i treść.

    Parametr bierzemy z ``recipient_kwargs`` (a nie z ``target``), bo tylko on niesie **treść**
    wklejonej listy adresów – ``target`` jej celowo nie ma. Obiekty (etap, region) wchodzą
    identyfikatorem. Konto koordynatora jest w podpisie, żeby podgląd jednej osoby nie był
    przepustką dla formularza wysłanego przez drugą.

    Termin wysyłki (MSG-SCHED-01) też jest w podpisie: podgląd „wyślij od razu” nie jest przepustką
    dla komunikatu zaplanowanego, a podgląd „sobota 6:00” – dla wysyłki natychmiastowej.
    """
    parameters = {key: getattr(value, "pk", value) for key, value in form.recipient_kwargs().items()}
    send_at = form.cleaned_data.get("send_at")
    payload = json.dumps(
        [
            form.cleaned_data["group"],
            parameters,
            form.cleaned_data["subject"],
            form.cleaned_data["body"],
            getattr(user, "pk", None),
            send_at.isoformat() if send_at else None,
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
        if request.POST.get("action") == ACTION_EXPORT:
            return self._export(request)
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
                "Grupa odbiorców, temat, treść albo termin zmieniły się od podglądu – nic nie wysłano. "
                "Sprawdź podgląd poniżej i wyślij jeszcze raz.",
            )
            return self._render(request, form, preview=recipients)
        if form.cleaned_data.get("send_at"):
            return self._schedule(request, form)
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

    def _schedule(self, request, form):
        """„Zaplanuj”: wiersz w stanie „zaplanowana” – bez listy adresów (MSG-SCHED-01).

        Pusta grupa **nie** blokuje zaplanowania, inaczej niż przy wysyłce od razu: do terminu
        grupa może się zapełnić (rejestracje, wpisy do etapu), a odbiorców i tak liczy beat w chwili
        wysyłki. Pusta w tej chwili kończy się stanem „bez odbiorców” w historii.
        """
        try:
            broadcast = schedule_broadcast(
                group=form.cleaned_data["group"],
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                scheduled_for=form.cleaned_data["send_at"],
                competition=request.competition,
                actor=request.user,
                parameters=form.recipient_kwargs(),
                target=form.target(),
                request=request,
            )
        except ValidationError as error:
            # Termin, który był dobry w podglądzie, mógł się w międzyczasie zbliżyć poniżej marginesu.
            # ``messages`` (lista napisów), bo błąd ``full_clean`` modelu bywa słownikiem pól, których
            # formularz nie ma.
            form.add_error("send_at", error.messages)
            return self._render(request, form)
        django_messages.success(
            request,
            _("Komunikat zaplanowany na %(when)s. Odbiorców policzymy ponownie w chwili wysyłki.")
            % {"when": date_format(localtime(broadcast.scheduled_for), "j E Y, H:i")},
        )
        return redirect(reverse("web:coordinator-messages"))

    def _export(self, request):
        """„Eksportuj do Excela”: plik .xlsx (imię, nazwisko, adres) albo strona z błędami.

        Rolę koordynatora **tego** konkursu sprawdził już ``CoordinatorRequiredMixin``, zanim
        doszło do ``post`` – eksport nie ma własnej, luźniejszej bramki. Zakres to
        ``request.competition`` i bieżąca edycja, dokładnie jak przy podglądzie i wysyłce.

        Pusta grupa nie daje pliku z samym nagłówkiem, tylko komunikat: arkusz bez wierszy
        wygląda jak błąd eksportu, a powód („nikt się jeszcze nie zapisał”) zna tylko ekran.
        """
        form = self._form(request, request.POST, for_export=True)
        if not form.is_valid():
            return self._render(request, form)
        group = form.cleaned_data["group"]
        rows = recipient_rows(
            group,
            competition=request.competition,
            edition=current_edition(request.competition),
            **form.recipient_kwargs(),
        )
        if not rows:
            django_messages.error(
                request, _("Ta grupa nie ma ani jednego odbiorcy – nie ma czego eksportować.")
            )
            return self._render(request, form)
        dataset = Dataset(
            header=[_("Imię"), _("Nazwisko"), _("E-mail")],
            rows=iter(rows),
            count=len(rows),
            title=_("Odbiorcy"),
            filename=export_filename(group),
        )
        # Audyt przed plikiem i bez danych: grupa, jej parametr (etykieta etapu, szkoły…, jak
        # w ``broadcast.sent``) i liczba wierszy. Obiektem jest konkurs – grupa nie ma wiersza.
        audit(
            request.user,
            "export.generated",
            request.competition,
            {
                "kind": "broadcast_recipients",
                "format": "xlsx",
                "group": group,
                "target": form.target(),
                "rows": dataset.count,
            },
            request=request,
        )
        return xlsx_response(dataset)

    @staticmethod
    def _form(request, data=None, *, for_export: bool = False) -> BroadcastForm:
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
            for_export=for_export,
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
                # Termin (MSG-SCHED-01): podgląd mówi wtedy „zaplanuj”, a liczba odbiorców jest
                # liczbą dzisiejszą – szablon dopisuje, że w chwili wysyłki policzymy ją ponownie.
                "send_at": form.cleaned_data.get("send_at"),
            },
            # Rejestr wysyłek **tego** konkursu: historia komunikatów sąsiada nie jest historią
            # tego organizatora (zakres stoi w ``recent_broadcasts``, przed limitem wierszy).
            "broadcasts": recent_broadcasts(request.competition),
            # Zaplanowane komunikaty tego konkursu – osobna lista z przyciskiem „Anuluj”.
            "scheduled": scheduled_broadcasts(request.competition),
            "edition": current_edition(request.competition),
            # Mapa „grupa → pole” dla skryptu, który chowa pola nienależące do wybranej grupy,
            # i zbiór tych pól dla szablonu (tylko one dostają punkt zaczepienia skryptu).
            "parameter_map": parameter_map,
            "parameter_fields": sorted({field for fields in parameter_map.values() for field in fields}),
            # Grupa bez eksportu (wklejona lista) – skrypt chowa przy niej „Eksportuj do Excela”;
            # bez skryptu przycisk zostaje, a serwer odpowiada błędem formularza.
            "no_export_group": BroadcastGroup.CUSTOM.value,
        }
        return TemplateResponse(request, TEMPLATE, context)


class CoordinatorBroadcastCancelView(CoordinatorRequiredMixin, View):
    """``POST /coordinator/messages/<id>/cancel/`` – „Anuluj” przy zaplanowanym komunikacie.

    Wyłącznie ``POST`` (z CSRF): anulowanie zmienia stan, więc nie może dać się wywołać odnośnikiem.
    Wiersz szukamy w ``for_competition(request.competition)`` – komunikat innego konkursu to 404,
    nie 403 (``apps.web.mixins``). Komunikat, który beat zdążył już wysłać, nie daje błędu, tylko
    informację: ``cancel_scheduled_broadcast`` rozstrzyga to warunkowym ``UPDATE``.
    """

    def post(self, request, pk: int):
        broadcast = get_object_or_404(MessageBroadcast.objects.for_competition(request.competition), pk=pk)
        if cancel_scheduled_broadcast(broadcast, actor=request.user, request=request):
            django_messages.success(request, _("Zaplanowany komunikat anulowany – nie zostanie wysłany."))
        else:
            django_messages.error(
                request,
                _("Tego komunikatu nie da się już anulować – został wysłany albo anulowany wcześniej."),
            )
        return redirect(reverse("web:coordinator-messages"))
