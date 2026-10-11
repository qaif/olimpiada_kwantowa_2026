"""Wiadomości uczestnika: skrzynka, wątek, katalog, „Napisz do organizatora”, zgłoszenie i blokada.

Podział na ten moduł i ``coordinator_chat`` jest podziałem ról – ta sama reguła, co przy forum
i zgłoszeniach: tutaj każdy queryset zaczyna się od „moja strona rozmowy”
(``apps.chat.services.membership``), więc cudza rozmowa nie jest „zabroniona”, tylko jej **nie ma**
(404). Identyfikator rozmowy w adresie jest kolejnym numerem, dlatego 403 byłoby licznikiem rozmów.

**Kolejność bramek:** logowanie → rola uczestnika tego konkursu (403) → moduł włączony (404).
Rola idzie przed przełącznikiem, jak w panelu koordynatora: recenzent i opiekun dostają 403
niezależnie od konfiguracji i odpowiedź nie mówi im, jak konkurs jest ustawiony.

**HTMX bez ani jednej linijki JavaScriptu na stronie.** Formularz wysyła się ``hx-post``
i podmienia cały wątek (lista wiadomości + pusty formularz), a lista wiadomości odpytuje własny
fragment co 15 s (``hx-trigger="every 15s"`` – ten sam wzorzec, co ``_ai_problem.html``). Bez
JavaScriptu działa to samo zwykłym ``POST`` z przekierowaniem.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.generic import View

from apps.chat import notifications
from apps.chat import services as chat
from apps.chat.forms import ChatKeyForm, ChatPreferencesForm, MessageForm, ReportForm
from apps.chat.models import (
    DIRECTORY_PAGE_SIZE,
    MIN_KDF_ITERATIONS,
    THREAD_LIMIT,
    ChatProfile,
    ConversationKind,
    Message,
    MessageReport,
    MessageStatus,
    PeerMode,
    SenderRole,
)
from apps.core.api import DomainError
from apps.forum.models import display_author
from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import ThrottledFormMixin

INBOX_TEMPLATE = "web/chat/inbox.html"
THREAD_FRAGMENT = "web/chat/_thread.html"
MESSAGES_FRAGMENT = "web/chat/_messages.html"
DIRECTORY_TEMPLATE = "web/chat/directory.html"

#: Nagłówek nad wiadomością organizatora. Uczestnik ma odróżnić zdanie organizatora od zdania
#: kolegi – a przy wspólnej skrzynce także wiedzieć, **kto** z zespołu odpowiedział.
ORGANIZER_LABEL = gettext_lazy("Organizator")

#: Zdania nad formularzem rozmowy między uczestnikami – zależne od trybu (§ 7 zadania). To jest
#: obietnica dla nadawcy o tym, kto może przeczytać jego wiadomość, więc brzmi dokładnie tak,
#: jak działa kolejka moderacji (``apps.chat.services.moderator_visible_q``).
PRIVACY_NOTICE = {
    PeerMode.PRE: gettext_lazy(
        "Wiadomości mogą być czytane przez organizatora w ramach moderacji. Każda wiadomość czeka "
        "na akceptację organizatora – odbiorca zobaczy ją dopiero po niej."
    ),
    PeerMode.POST: gettext_lazy(
        "Wiadomości mogą być czytane przez organizatora w ramach moderacji. Dochodzą od razu, "
        "a organizator przegląda je po fakcie."
    ),
    PeerMode.NONE: gettext_lazy("Organizator widzi tylko zgłoszone wiadomości."),
}

#: Zdanie nad formularzem rozmowy szyfrowanej (§ 11). Mówi też, co się dzieje przy zgłoszeniu –
#: to jedyna chwila, w której treść takiej rozmowy trafia do kogokolwiek poza jej stronami.
ENCRYPTED_NOTICE = gettext_lazy(
    "Rozmowa szyfrowana end-to-end: treść znają tylko Wasze przeglądarki – nie zna jej serwer ani "
    "organizator. Jeśli zgłosisz wiadomość, jej odszyfrowana treść trafi do organizatora."
)

#: Notka w wątku szyfrowanym, gdy druga strona zmieniła klucz (np. zapomniała hasła do wiadomości).
KEY_CHANGED_NOTICE = gettext_lazy(
    "Klucz szyfrowania tej osoby zmienił się. Jeśli to niespodzianka, porównajcie odciski kluczy na żywo."
)


def _grouped(fingerprint: str) -> str:
    """Odcisk klucza w grupach po cztery znaki – do porównania na głos albo na dwóch ekranach."""
    return " ".join(fingerprint[index : index + 4] for index in range(0, len(fingerprint), 4))


def seen_by_human(request) -> bool:
    """Czy odpytanie wątku przyszło z widocznej karty z fokusem (``static/js/chat.js``)."""
    return request.headers.get("X-Chat-Seen") == "1"


def organizer_author(user) -> str:
    """Imię członka zespołu organizatora pod jego wiadomością – **bez** słowa „Organizator”.

    Do pakietu 5 podpis był napisem „Organizator · Imię N.”, czyli zwykłym tekstem w tym samym
    miejscu, w którym stoi imię uczestnika. Uczestnik z imieniem „Organizator · Anna” wyglądał
    więc w rozmowie dokładnie jak organizator. Znacznik roli jest teraz **osobnym elementem**
    szablonu (odznaka w ``templates/web/chat/_messages.html``), sterowanym wyłącznie przez
    ``sender_role`` wiadomości – żadne pole, które wpisuje użytkownik, nie może go wytworzyć.
    Konto skasowane (``None``) zostaje z samą odznaką.
    """
    if user is None:
        return ""
    return display_author(user)


def conversation_label(conversation, participant) -> str:
    """Kto jest po drugiej stronie – w skrzynce i w nagłówku wątku uczestnika."""
    if conversation.kind == ConversationKind.ORGANIZER:
        return ORGANIZER_LABEL
    other = chat.other_participant(conversation, participant)
    return display_author(other.user if other is not None else None)


class ChatParticipantMixin(ParticipantRequiredMixin):
    """Rola uczestnika (403), potem przełącznik modułu (404) – kolejność w docstringu modułu."""

    _role_cache: bool | None = None

    def has_role(self, user) -> bool:
        # Pamiętane na czas żądania: ``dispatch`` niżej pyta o rolę przed bramką przełącznika,
        # a ``RoleRequiredMixin.dispatch`` – drugi raz. Dwa razy te same dwa zapytania to koszt
        # bez żadnej informacji.
        if self._role_cache is None:
            self._role_cache = super().has_role(user)
        return self._role_cache

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and self.has_role(request.user):
            if self.competition is None or not self.chat_settings.enabled:
                raise Http404("Wiadomości są w tym konkursie wyłączone.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def me(self):
        if not hasattr(self, "_me"):
            self._me = self.participant
        return self._me

    def is_htmx(self) -> bool:
        return bool(self.request.headers.get("HX-Request"))

    # --- kontekst wspólny --------------------------------------------------------------------------

    @property
    def chat_settings(self):
        if not hasattr(self, "_chat_settings"):
            self._chat_settings = chat.settings_for(self.competition)
        return self._chat_settings

    @property
    def my_key(self):
        if not hasattr(self, "_my_key"):
            self._my_key = chat.key_for(self.me)
        return self._my_key

    def e2e_context(self) -> dict:
        """Konfiguracja skryptu szyfrowania (``static/js/chat-ui.js``) – albo pusty słownik.

        Na stronie stoi wyłącznie to, co i tak należy do tej osoby: jej klucz publiczny, odcisk
        i **owinięta** kopia klucza prywatnego (bez hasła do wiadomości jest bezużyteczna). Blok jest
        tylko tam, gdzie szyfrowanie ma sens: organizator je włączył albo ta osoba ma już klucz
        i rozmowy szyfrowane z czasów, gdy było włączone.
        """
        key = self.my_key
        if not self.chat_settings.e2e_enabled and key is None:
            return {}
        return {
            "e2e": {
                "enabled": self.chat_settings.e2e_enabled,
                "key": key,
                "fingerprint": _grouped(key.fingerprint) if key is not None else "",
                "key_url": reverse("web:chat-key"),
                "user_id": self.request.user.pk,
                "competition_id": self.competition.pk,
            }
        }

    def inbox_rows(self, current_pk=None) -> list[dict]:
        rows = []
        for member in chat.participant_inbox(self.me):
            conversation = member.conversation
            rows.append(
                {
                    "conversation": conversation,
                    "label": conversation_label(conversation, self.me),
                    "is_organizer": conversation.kind == ConversationKind.ORGANIZER,
                    "snippet": member.last_body,
                    "hidden": member.last_status == MessageStatus.HIDDEN,
                    "own": member.last_sender_id == self.request.user.pk,
                    "at": member.last_created_at,
                    "unread": member.unread_since is not None and conversation.pk != current_pk,
                    "current": conversation.pk == current_pk,
                    "url": reverse("web:chat-thread", args=[conversation.pk]),
                    # Skrót wiadomości szyfrowanej odszyfrowuje przeglądarka (§ 11.3).
                    "encrypted": bool(member.last_ciphertext),
                    "ciphertext": member.last_ciphertext,
                    "iv": member.last_iv,
                    "sender_id": member.last_sender_id,
                    "sender_key": member.last_sender_key,
                    "recipient_key": member.last_recipient_key,
                }
            )
        return rows

    def entries(self, conversation) -> list[dict]:
        """Wiadomości w postaci dla szablonu – z gotowym podpisem, **bez** obiektu nadawcy.

        Ta sama zasada, co ``apps.web.views.forum.render_posts``: szablon nie dostaje obiektu
        użytkownika, więc żadna przyszła poprawka nie wypisze przez niego adresu ani kodu.

        W rozmowie szyfrowanej między wiadomości wchodzi notka „Klucz szyfrowania tej osoby zmienił
        się” – w miejscu, w którym klucz drugiej strony użyty w wiadomościach jest inny niż
        poprzednio, i na końcu, gdy jej bieżący klucz jest inny niż w ostatniej wiadomości (§ 11.3).
        """
        user_id = self.request.user.pk
        reported = set(
            MessageReport.objects.filter(message__conversation=conversation, reporter_id=user_id).values_list(
                "message_id", flat=True
            )
        )
        rows = list(
            chat.visible_messages(conversation, self.me)
            .select_related("sender")
            .order_by("-created_at", "-id")[:THREAD_LIMIT]
        )
        entries = []
        other_key_seen = ""
        for message in reversed(rows):
            own = message.sender_role == SenderRole.PARTICIPANT and message.sender_id == user_id
            if message.sender_role == SenderRole.ORGANIZER:
                author = organizer_author(message.sender)
            else:
                author = _("Ty") if own else display_author(message.sender)
            if message.is_encrypted:
                other_key = message.recipient_public_key if own else message.sender_public_key
                if other_key_seen and other_key != other_key_seen:
                    entries.append({"notice": KEY_CHANGED_NOTICE})
                other_key_seen = other_key
            entries.append(
                {
                    "message": message,
                    "author": author,
                    "own": own,
                    "organizer": message.sender_role == SenderRole.ORGANIZER,
                    "can_report": (
                        conversation.kind == ConversationKind.PEER
                        and not own
                        and message.status == MessageStatus.PUBLISHED
                        and message.pk not in reported
                    ),
                    "reported": message.pk in reported,
                }
            )
        if conversation.is_encrypted and other_key_seen:
            other = chat.key_for(chat.other_participant(conversation, self.me))
            if other is not None and other.public_key != other_key_seen:
                entries.append({"notice": KEY_CHANGED_NOTICE})
        return entries

    def thread_context(self, conversation, form=None, *, error: str = "", form_action: str = "") -> dict:
        """Kontekst wątku – istniejącego albo „nowego” (``conversation=None``: pierwsza wiadomość)."""
        is_peer = conversation.kind == ConversationKind.PEER if conversation is not None else self.new_is_peer
        row = self.chat_settings
        # Etap liczony razem z trybem (``mode_and_stage``): ekran mówi, **który** etap wymusił
        # premoderację albo wstrzymał rozmowy szyfrowane, a pytanie o etapy nie ma iść dwa razy.
        # Polityka rozmowy (ALUM-01 § 5.3): rozmowa mentorska ma własny tryb i własną notkę.
        policy = chat.peer_policy(conversation) if is_peer else None
        if conversation is not None and is_peer:
            mode, stage = chat.conversation_mode_and_stage(conversation, row=row, policy=policy)
        else:
            mode, stage = (
                chat.mode_and_stage(self.competition, row=row) if is_peer else (PeerMode(row.peer_mode), None)
            )
        refusal = ""
        blocked_by_me = False
        other = None
        other_key = None
        encrypted = bool(conversation is not None and conversation.is_encrypted)
        if conversation is not None and is_peer:
            refusal = chat.peer_write_refusal(
                conversation, self.me, mode=mode, row=row, stage=stage, policy=policy
            )
            other = chat.other_participant(conversation, self.me)
            blocked_by_me = chat.has_blocked(self.me, other)
            if encrypted:
                other_key = chat.key_for(other)
        if encrypted:
            privacy = ENCRYPTED_NOTICE
        else:
            privacy = PRIVACY_NOTICE.get(mode, "") if is_peer else ""
        return {
            "conversation": conversation,
            "thread_label": (
                conversation_label(conversation, self.me) if conversation is not None else self.new_label
            ),
            "is_peer": is_peer,
            "is_encrypted": encrypted,
            "entries": self.entries(conversation) if conversation is not None else [],
            "form": form or MessageForm(),
            "form_error": error,
            "form_action": form_action
            or (reverse("web:chat-thread", args=[conversation.pk]) if conversation is not None else ""),
            "poll_url": (
                f"{reverse('web:chat-thread', args=[conversation.pk])}?fragment=messages"
                f"&v={chat.thread_version(conversation, self.me)}"
                if conversation is not None
                else ""
            ),
            "refusal": refusal,
            "needs_key": refusal == chat.NEEDS_KEY,
            "can_block": is_peer and other is not None,
            "blocked_by_me": blocked_by_me,
            "privacy_notice": privacy,
            "policy_notice": policy.notice if policy is not None else "",
            # Etap pokazujemy nad formularzem tylko wtedy, gdy **zaostrzył** tryb rozmowy jawnej;
            # rozmowę szyfrowaną wstrzymuje, a to mówi już zdanie odmowy.
            "forcing_stage": stage if not encrypted else None,
            "report_form": ReportForm(),
            "thread_limit": THREAD_LIMIT,
            "my_key": self.my_key if encrypted else None,
            "other_key": other_key,
            "my_fingerprint": _grouped(self.my_key.fingerprint) if encrypted and self.my_key else "",
            "other_fingerprint": _grouped(other_key.fingerprint) if other_key is not None else "",
            "user_id": self.request.user.pk,
            # Etykieta „bez szyfrowania” przy rozmowach jawnych ma sens tylko tam, gdzie szyfrowanie
            # w ogóle istnieje – także w fragmencie htmx, który nie przechodzi przez ``page_context``.
            **self.e2e_context(),
        }

    #: Etykieta i rodzaj „nowego” wątku – ustawiają widoki pierwszej wiadomości.
    new_label = ""
    new_is_peer = False

    def page_context(self, *, current_pk=None) -> dict:
        return {
            "rows": self.inbox_rows(current_pk),
            # Etap nie otwiera ani nie zamyka kanału (zaostrza tylko moderację), więc do pytania
            # „czy pokazać »Nowa rozmowa«” wystarcza zapisane ustawienie.
            "peer_open": self.chat_settings.peer_mode != PeerMode.OFF,
            "settings_url": f"{reverse('web:profile')}#wiadomosci",
            **self.e2e_context(),
        }


class ChatInboxView(ChatParticipantMixin, View):
    """``/me/messages/`` – lista rozmów; po prawej zaproszenie do wybrania rozmowy albo stan pusty."""

    def get(self, request):
        context = {**self.page_context(), "thread_open": False}
        return TemplateResponse(request, INBOX_TEMPLATE, context)


class ChatThreadView(ChatParticipantMixin, ThrottledFormMixin, View):
    """``/me/messages/<id>/`` – wątek. ``?fragment=messages`` oddaje samą listę do odpytywania."""

    throttle_scope = "chat"

    def get(self, request, pk: int):
        member = self._member(pk)
        conversation = member.conversation
        if request.GET.get("fragment") == "messages":
            # Odpytanie w tle nie jest przeczytaniem: odczyt tylko z nagłówkiem od ``chat.js``
            # (karta widoczna i z fokusem). Przed sprawdzeniem wersji – 204 też może być
            # odpowiedzią na „spojrzałem na wątek”.
            if seen_by_human(request):
                chat.mark_read(member)
            # Nic się nie zmieniło od poprzedniego odpytania – 204, htmx niczego nie podmienia
            # (``apps.chat.services.thread_version``).
            if request.GET.get("v") == chat.thread_version(conversation, self.me):
                return HttpResponse(status=204)
            context = self.thread_context(conversation)
            return TemplateResponse(request, MESSAGES_FRAGMENT, context)
        chat.mark_read(member)
        context = {
            **self.page_context(current_pk=conversation.pk),
            **self.thread_context(conversation),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context)

    def post(self, request, pk: int):
        member = self._member(pk)
        conversation = member.conversation
        form = MessageForm(request.POST)
        error = ""
        if form.is_valid():
            try:
                message = chat.send_participant_message(
                    user=request.user,
                    competition=self.competition,
                    conversation=conversation,
                    body=form.cleaned_data["body"],
                    ciphertext=form.cleaned_data["ciphertext"],
                    iv=form.cleaned_data["iv"],
                    sender_fingerprint=form.cleaned_data["sender_fingerprint"],
                    recipient_fingerprint=form.cleaned_data["recipient_fingerprint"],
                    request=request,
                )
            except DomainError as exc:
                error = str(exc.detail)
            else:
                if not self.is_htmx():
                    if message.status == MessageStatus.PENDING:
                        messages.success(request, _("Wiadomość czeka na akceptację organizatora."))
                    return redirect(reverse("web:chat-thread", args=[conversation.pk]))
                form = MessageForm()
        if self.is_htmx():
            # Fragment ze statusem 200 także przy błędzie: htmx domyślnie nie podmienia treści dla
            # 4xx, a komunikat ma stanąć nad formularzem, nie zniknąć w konsoli przeglądarki.
            return TemplateResponse(
                request, THREAD_FRAGMENT, self.thread_context(conversation, form, error=error)
            )
        if error:
            messages.error(request, error)
        context = {
            **self.page_context(current_pk=conversation.pk),
            **self.thread_context(conversation, form, error=error),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context, status=400)

    def _member(self, pk):
        try:
            return chat.membership(self.me, pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc


class ChatNewThreadMixin(ChatParticipantMixin):
    """Wspólne dla „pierwszej wiadomości”: do organizatora i do osoby z katalogu.

    Rozmowa powstaje dopiero **z pierwszą wiadomością** (``POST``). Wejście na adres ``GET``-em nie
    zakłada w bazie niczego: pusta rozmowa, w której nikt nic nie napisał, byłaby wierszem bez treści
    na liście drugiej strony – i zapisem wykonanym w środku ``GET``-a.
    """

    def render_new(self, request, form=None, *, status: int = 200):
        context = {
            **self.page_context(),
            **self.thread_context(None, form, form_action=request.path),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context, status=status)


class ChatOrganizerView(ChatNewThreadMixin, ThrottledFormMixin, View):
    """``/me/messages/organizer/`` – „Napisz do organizatora”. Istniejąca rozmowa: przekierowanie."""

    throttle_scope = "chat"
    new_label = ORGANIZER_LABEL

    def get(self, request):
        existing = chat.organizer_conversation_of(self.me)
        if existing is not None:
            return redirect(reverse("web:chat-thread", args=[existing.pk]))
        return self.render_new(request)

    def post(self, request):
        form = MessageForm(request.POST)
        if not form.is_valid():
            return self.render_new(request, form, status=400)
        try:
            message = chat.write_to_organizer(
                user=request.user,
                competition=self.competition,
                body=form.cleaned_data["body"],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self.render_new(request, form, status=exc.status_code)
        return redirect(reverse("web:chat-thread", args=[message.conversation_id]))


class ChatDirectoryView(ChatParticipantMixin, View):
    """``/me/messages/new/`` – katalog uczestników, którzy sami się do niego zapisali.

    Wiersz katalogu to **wyłącznie** podpis (``display_author``) i województwo. Szablon dostaje gotowe
    napisy i token, a nie profil ani konto – z tego samego powodu, co wpisy forum.
    """

    def get(self, request):
        if chat.effective_peer_mode(self.competition) == PeerMode.OFF:
            raise Http404("Rozmowy między uczestnikami są wyłączone.")
        query = (request.GET.get("q") or "").strip()[:60]
        rows = chat.directory(self.me, query)
        page = Paginator(rows, DIRECTORY_PAGE_SIZE).get_page(request.GET.get("page"))
        context = {
            **self.page_context(),
            "query": query,
            "page_obj": page,
            "paginator": page.paginator,
            "people": [
                {
                    "label": display_author(profile.participant.user),
                    "district": profile.participant.get_district_display(),
                    "url": reverse("web:chat-start", args=[profile.token]),
                }
                for profile in page.object_list
            ],
            "own_profile": chat.profile_for(self.me),
        }
        return TemplateResponse(request, DIRECTORY_TEMPLATE, context)


class ChatStartView(ChatNewThreadMixin, ThrottledFormMixin, View):
    """``/me/messages/new/<token>/`` – pierwsza wiadomość do osoby z katalogu.

    Rozmowa z tą osobą już istnieje – przekierowanie do niej. Osoby nie ma w katalogu (wypisała się,
    zablokowała mnie, konto nieaktywne, kanał wyłączony) – 404, bez mówienia, który to powód.
    """

    throttle_scope = "chat"
    new_is_peer = True

    def _profile(self, token: str) -> ChatProfile:
        profile = (
            ChatProfile.objects.for_competition(self.competition)
            .filter(token=token)
            .select_related("participant__user")
            .first()
        )
        if profile is None or profile.participant_id == self.me.pk:
            raise Http404("Nie ma takiej osoby w katalogu.")
        return profile

    def get(self, request, token: str):
        if self.chat_settings.peer_mode == PeerMode.OFF:
            raise Http404("Rozmowy między uczestnikami są wyłączone.")
        profile = self._profile(token)
        existing = chat.find_peer_conversation(self.me, profile.participant)
        if existing is not None and (
            existing.is_encrypted or chat.visible_messages(existing, self.me).exists()
        ):
            return redirect(reverse("web:chat-thread", args=[existing.pk]))
        if not chat.can_start_with(self.me, profile):
            raise Http404("Nie ma takiej osoby w katalogu.")
        self.new_label = display_author(profile.participant.user)
        return self.render_new(request)

    def render_new(self, request, form=None, *, status: int = 200):
        response = super().render_new(request, form, status=status)
        # Przy szyfrowaniu pierwsza wiadomość nie powstaje na tej stronie: najpierw zakłada się
        # (pustą) rozmowę, bo z jej identyfikatora wyprowadza się klucz rozmowy – patrz
        # ``apps.chat.services.open_encrypted_conversation``.
        response.context_data["start_encrypted"] = bool(self.chat_settings.e2e_enabled)
        response.context_data["has_key"] = self.my_key is not None
        return response

    def post(self, request, token: str):
        profile = self._profile(token)
        self.new_label = display_author(profile.participant.user)
        if request.POST.get("action") == "open-encrypted":
            try:
                conversation = chat.open_encrypted_conversation(
                    user=request.user, competition=self.competition, token=token, request=request
                )
            except DomainError as exc:
                if exc.status_code == 404:
                    raise Http404(str(exc.detail)) from exc
                messages.error(request, str(exc.detail))
                return self.render_new(request, status=exc.status_code)
            return redirect(reverse("web:chat-thread", args=[conversation.pk]))
        form = MessageForm(request.POST)
        if not form.is_valid():
            return self.render_new(request, form, status=400)
        try:
            message = chat.start_peer_conversation(
                user=request.user,
                competition=self.competition,
                token=token,
                body=form.cleaned_data["body"],
                request=request,
            )
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            messages.error(request, str(exc.detail))
            return self.render_new(request, form, status=exc.status_code)
        if message.status == MessageStatus.PENDING:
            messages.success(request, _("Wiadomość czeka na akceptację organizatora."))
        return redirect(reverse("web:chat-thread", args=[message.conversation_id]))


class ChatReportView(ChatParticipantMixin, ThrottledFormMixin, View):
    """``/me/messages/<id>/report/`` – „Zgłoś” wiadomość drugiej strony."""

    throttle_scope = "chat"

    def post(self, request, pk: int):
        try:
            member = chat.membership(self.me, pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        form = ReportForm(request.POST)
        target = reverse("web:chat-thread", args=[pk])
        if not form.is_valid():
            messages.error(request, _("Napisz krótko, dlaczego zgłaszasz tę wiadomość."))
            return redirect(target)
        message = Message.objects.filter(
            conversation=member.conversation, pk=form.cleaned_data["message"]
        ).first()
        if message is None:
            raise Http404("Nie ma takiej wiadomości.")
        try:
            chat.report_message(
                user=request.user,
                competition=self.competition,
                message=message,
                reason=form.cleaned_data["reason"],
                reported_plaintext=form.cleaned_data["reported_plaintext"],
                request=request,
            )
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, _("Zgłoszenie trafiło do organizatora. Dziękujemy."))
        return redirect(target)


class ChatBlockView(ChatParticipantMixin, ThrottledFormMixin, View):
    """``/me/messages/<id>/block/`` – „Zablokuj” / „Odblokuj” drugą stronę rozmowy."""

    throttle_scope = "chat"

    def post(self, request, pk: int):
        try:
            member = chat.membership(self.me, pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc
        conversation = member.conversation
        if conversation.kind != ConversationKind.PEER:
            raise Http404("Organizatora nie da się zablokować.")
        try:
            if request.POST.get("action") == "unblock":
                chat.unblock(participant=self.me, conversation=conversation)
                messages.success(request, _("Odblokowano – ta osoba znów może do Ciebie pisać."))
            else:
                chat.block(participant=self.me, conversation=conversation)
                messages.success(
                    request, _("Zablokowano. Ta osoba nie może do Ciebie pisać ani zacząć nowej rozmowy.")
                )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        return redirect(reverse("web:chat-thread", args=[pk]))


KEY_TEMPLATE = "web/chat/key.html"


class ChatKeyView(ChatParticipantMixin, ThrottledFormMixin, View):
    """``/me/messages/key/`` – klucz szyfrowania: utworzenie, odblokowanie na tym urządzeniu, nowy klucz.

    Parę kluczy tworzy i owija hasłem przeglądarka (``static/js/chat-ui.js``); tu przychodzą wyłącznie
    klucz publiczny i owinięta kopia prywatnego. Ekran istnieje, gdy organizator włączył szyfrowanie
    albo ta osoba ma już klucz (musi móc go odblokować, żeby czytać dawne rozmowy szyfrowane).
    """

    throttle_scope = "chat"

    def _check(self):
        if not self.chat_settings.e2e_enabled and self.my_key is None:
            raise Http404("Szyfrowanie wiadomości jest wyłączone.")

    def get(self, request):
        self._check()
        return TemplateResponse(request, KEY_TEMPLATE, self._context())

    def post(self, request):
        self._check()
        form = ChatKeyForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Nie udało się zapisać klucza – spróbuj ponownie."))
            return TemplateResponse(request, KEY_TEMPLATE, self._context(), status=400)
        try:
            chat.save_key(
                user=request.user,
                competition=self.competition,
                public_key=form.cleaned_data["public_key"],
                wrapped_private_key=form.cleaned_data["wrapped_private_key"],
                kdf_salt=form.cleaned_data["kdf_salt"],
                wrap_iv=form.cleaned_data["wrap_iv"],
                kdf_iterations=form.cleaned_data["kdf_iterations"],
                replace=form.cleaned_data["replace"],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return TemplateResponse(request, KEY_TEMPLATE, self._context(), status=exc.status_code)
        messages.success(request, _("Klucz szyfrowania jest gotowy."))
        return redirect(reverse("web:chat-key"))

    def _context(self) -> dict:
        return {**self.page_context(), "key": self.my_key, "min_iterations": MIN_KDF_ITERATIONS}


# --- ustawienia na ekranie „Edycja danych” ---------------------------------------------------------------


def preferences_context(request) -> dict:
    """Sekcja „Wiadomości” na ekranie „Edycja danych” – albo pusty słownik.

    Tylko tam, gdzie moduł **jest**: włączony w tym konkursie i osoba, która w nim pisze (uczestnik
    albo koordynator). Przełącznik katalogu dostaje wyłącznie uczestnik.
    """
    competition = getattr(request, "competition", None)
    user = request.user
    if not chat.is_enabled(competition):
        return {}
    participant = chat.chat_participant(user, competition)
    if participant is None and not chat.is_organizer(user, competition):
        return {}
    initial = {"email_on_message": notifications.preferences_for(user).email_on_message}
    if participant is not None:
        initial["discoverable"] = chat.profile_for(participant).discoverable
    return {
        "chat_preferences_form": ChatPreferencesForm(initial=initial, participant=participant is not None)
    }


class ChatPreferencesView(LoginRequiredMixin, View):
    """``/account/chat-settings/`` – zapis sekcji „Wiadomości” z ekranu „Edycja danych”. Tylko ``POST``."""

    def post(self, request):
        from apps.web.views.account import profile_url

        competition = getattr(request, "competition", None)
        participant = chat.chat_participant(request.user, competition)
        # Ta sama bramka, co sekcja na ekranie „Edycja danych”: ustawienia Wiadomości ma wyłącznie
        # ktoś, kto w tym konkursie w nich pisze. Recenzent czy opiekun nie ma tu czego zapisywać.
        if participant is None and not chat.is_organizer(request.user, competition):
            raise PermissionDenied("Wiadomości są dostępne dla uczestników i organizatora tego konkursu.")
        form = ChatPreferencesForm(request.POST, participant=participant is not None)
        target = f"{profile_url(request)}#wiadomosci"
        if not form.is_valid():
            messages.error(request, _("Nie udało się zapisać ustawień wiadomości."))
            return redirect(target)
        notifications.save_preferences(request.user, email_on_message=form.cleaned_data["email_on_message"])
        if participant is not None and "discoverable" in form.cleaned_data:
            chat.set_discoverable(participant, form.cleaned_data["discoverable"])
        messages.success(request, _("Ustawienia wiadomości zostały zapisane."))
        return redirect(target)
