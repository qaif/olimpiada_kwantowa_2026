"""Komisja w panelu koordynatora: lista członków i karta jednej osoby.

Dwa ekrany **wyłącznie do odczytu**: wszystko, co da się tu zrobić, idzie przez adresy akcji,
które już istnieją (zatwierdzenie, województwo, odebranie recenzji, korekta punktów, przydział,
reguły, przypomnienie). Karta nie ma ani jednej własnej reguły domenowej – zbiera dane
(``apps.accounts.member_card``) i stawia obok nich formularze cudzych, sprawdzonych endpointów.

Dlaczego to nie jest ten sam ekran, co „Komitet” w menu: tamten jest kolejką spraw do załatwienia
(zaproszenia, wnioski o zatwierdzenie), a ten – spisem ludzi, którzy już pracują. Zaproszeń tu
świadomie nie ma, żeby nie było dwóch miejsc, w których wysyła się kod.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.accounts.anonymised import anonymised_q
from apps.accounts.member_card import member_card, member_list_rows
from apps.accounts.models import CommitteeMember, CommitteeStatus, Region
from apps.accounts.services import CUSTOM_REGIONS_FLAG
from apps.competitions.services import current_edition
from apps.grading.models import ReviewStatus
from apps.web.forms import VOIVODESHIP_CHOICES
from apps.web.list_controls import ListControls
from apps.web.mixins import CoordinatorRequiredMixin

MEMBERS_TEMPLATE = "web/coordinator/members.html"
MEMBER_DETAIL_TEMPLATE = "web/coordinator/member_detail.html"


class CommitteeMembersView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/members/`` – wszyscy członkowie komisji z obciążeniem i stanem konta.

    Filtry jadą w adresie (GET), a nie w sesji: zawężona lista („zawieszeni”, „etap finału”) ma dać
    się odświeżyć, wysłać odnośnikiem do reszty komitetu i wrócić do niej przyciskiem „wstecz”.

    Stronicowania tu nie ma świadomie. Komisja olimpiady liczy kilkadziesiąt osób i cała mieści się
    na jednej stronie; podział na strony kosztowałby możliwość przejrzenia obciążenia jednym rzutem
    oka, czyli dokładnie to, po co ten ekran istnieje.

    Członkowie z kontem usuniętym na żądanie są domyślnie schowani (przełącznik „Pokaż usunięte
    konta”, ``apps.web.list_controls``); liczba ukrytych to jeden ``COUNT`` z tym samym filtrem
    statusu, co lista.
    """

    template_name = MEMBERS_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET
        status = params.get("status", "")
        if status not in dict(CommitteeStatus.choices):
            # Nieznana wartość znaczy „bez filtra”, a nie 404: parametr pochodzi z adresu, który
            # ktoś mógł skrócić ręcznie, a lista bez zawężenia jest poprawną odpowiedzią.
            status = ""
        edition = current_edition(self.competition)
        stages = list(edition.stages.order_by("opens_at", "id")) if edition else []
        stage_id = _int_or_none(params.get("stage"))
        if stage_id is not None and stage_id not in {stage.pk for stage in stages}:
            stage_id = None
        controls = ListControls(self.request, ())
        if not controls.show_deleted and self.competition is not None:
            hidden = CommitteeMember.objects.for_competition(self.competition).filter(anonymised_q("user"))
            controls.hidden_deleted = (hidden.filter(status=status) if status else hidden).count()
        context.update(
            {
                # Wiersze przychodzą z serwisu kont (jedna definicja obciążenia dla panelu
                # i dla przydziału), a zawężenie do konkursu robimy **na wyniku**: ``CommitteeMember``
                # ma własny klucz obcy, więc pytanie „czyj to członek komisji” ma tu jedną,
                # tanią odpowiedź – bez drugiej kopii reguły „aktywny recenzent” w panelu.
                "rows": _with_region_labels(
                    _rows_of_competition(
                        member_list_rows(
                            status=status, stage_id=stage_id, include_deleted=controls.show_deleted
                        ),
                        self.competition,
                    ),
                    self.competition,
                ),
                "controls": controls,
                "status": status,
                "status_choices": CommitteeStatus.choices,
                "stages": stages,
                "stage_id": stage_id,
                "edition": edition,
            }
        )
        return context


class CommitteeMemberCardView(CoordinatorRequiredMixin, TemplateView):
    """``/coordinator/members/<pk>/`` – karta jednego członka komisji.

    Wszystko o tej osobie i wszystko, co wolno z nią zrobić, na jednej stronie: dane konta,
    obciążenie w rozbiciu na etapy, tabela recenzji z korektą punktów i odebraniem pracy, przydział
    nowej pracy, reguły zadań, kalibracja, zgłoszenia i ślad audytowy.
    """

    template_name = MEMBER_DETAIL_TEMPLATE

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queryset = CommitteeMember.objects.for_competition(self.competition).select_related(
            "user", "approved_by"
        )
        # Podział terytorialny konkursu (§ 1.4). Przy wyłączonej fladze karta nie dotyka kolumny
        # ``region`` ani razu – ani złączeniem, ani odczytem, ani zmienną w kontekście.
        by_region = _regions_enabled(self.competition)
        if by_region:
            queryset = queryset.select_related("region")
        member = get_object_or_404(queryset, pk=self.kwargs["pk"])
        context.update(member_card(member))
        context.update(
            {
                "voivodeship_choices": VOIVODESHIP_CHOICES,
                "region_label": member.region.name if by_region and member.region_id else "",
                "pending_status": CommitteeStatus.PENDING,
                # Odebrać wolno recenzję w każdym stanie poza anulowaną – także wystawioną.
                # Czy w tej konkretnej sprawie wolno (ogłoszone wyniki, ocena rozstrzygnięta przez
                # człowieka), rozstrzyga serwis: karta nie powiela reguły, tylko nie chowa przycisku.
                "withdrawable_statuses": (
                    ReviewStatus.ASSIGNED,
                    ReviewStatus.DRAFT,
                    ReviewStatus.SUBMITTED,
                ),
                # Telefonu członek komisji w modelu nie ma (ma go uczestnik i opiekun szkolny),
                # ale karta czyta pole przez ``getattr``: gdy pojawi się, sekcja „Dane” pokaże je
                # bez zmiany szablonu, a dopóki go nie ma – po prostu nie stoi tam pusta rubryka.
                "phone": getattr(member, "phone", "") or getattr(member.user, "phone", ""),
            }
        )
        return context


def _regions_enabled(competition) -> bool:
    """Czy ten konkurs ma własny podział terytorialny (§ 1.4).

    Flagę czyta widok, nigdy szablon (§ 2.1): flaga w szablonie jest flagą, której nie widać
    w teście widoku.
    """
    return competition is not None and competition.has_feature(CUSTOM_REGIONS_FLAG)


def _with_region_labels(rows: list[dict], competition) -> list[dict]:
    """Dopisuje wierszom listy nazwę regionu – **jednym** zapytaniem i tylko przy włączonej fladze.

    Nie ``select_related``: wiersze składa ``apps.accounts.member_card.member_list_rows``, wspólne
    dla panelu i dla przydziału, a odczyt ``member.region`` w pętli byłby zapytaniem na wiersz.
    Słownik regionów konkursu to kilkanaście par i jedno zapytanie niezależne od liczby członków.

    Przy wyłączonej fladze – czyli w Konkursie #1 – funkcja nie wykonuje ani jednego zapytania
    i nie dokłada wierszom ani jednego klucza; lista wygląda wtedy co do znaku tak, jak dziś.
    """
    if not _regions_enabled(competition):
        return rows
    names = dict(Region.objects.for_competition(competition).values_list("id", "name"))
    for row in rows:
        row["region_label"] = names.get(getattr(row["member"], "region_id", None), "")
    return rows


def _rows_of_competition(rows: list[dict], competition) -> list[dict]:
    """Zostawia wiersze członków **tego** konkursu, w zastanej kolejności.

    Wiersze bez konkursu (``competition_id IS NULL``) zostają i to jest świadome przez jedno
    wydanie – ta sama reguła, co w ``apps.accounts.services.participant_for``: w wydaniu B
    kolumna dopiero powstaje, a wydanie D zamyka ją na ``NOT NULL`` i ta gałąź znika sama.
    """
    if competition is None:
        return []
    return [row for row in rows if getattr(row["member"], "competition_id", None) in (None, competition.pk)]


def _int_or_none(raw) -> int | None:
    """Parametr zapytania jako liczba albo ``None``. Śmieci w adresie nie są błędem użytkownika."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
