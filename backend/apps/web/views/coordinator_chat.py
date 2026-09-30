"""Wiadomości w panelu koordynatora: wspólna skrzynka organizatora, kolejka moderacji i ustawienia.

Skrzynka organizatora jest **wspólna**: każdy koordynator konkursu widzi każdą rozmowę
organizatorską i odpowiada w niej w imieniu zespołu (podpis „Organizator · Imię N.”). Stan
„przeczytane” jest jeden na rozmowę – koordynator, który otworzył wątek, gasi kropkę wszystkim,
bo sprawa nie czeka już na nikogo.

**Czego tu nie ma i nie będzie:** ekranu „przeglądaj rozmowy uczestników”. Wątek pod
``/coordinator/chat/<id>/`` otwiera wyłącznie rozmowę, w której organizator jest stroną
(``apps.chat.services.organizer_conversation``), a treść rozmów między uczestnikami dociera do
koordynatora tylko przez kolejkę moderacji – w zakresie, który nadawca znał w chwili wysłania
(``apps.chat.services.moderator_visible_q``).

**Kolejność bramek**: rola (403) przed przełącznikiem modułu – jak na forum w panelu. Wyłączony
moduł nie jest tu 404, tylko przekierowaniem na ekran ustawień: koordynator jest jedyną osobą, która
może go włączyć, a pozycja menu ma go tam doprowadzić, zamiast kończyć się ślepą stroną.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.accounts.models import Participant
from apps.chat import services as chat
from apps.chat.forms import PEER_MODE_HELP, ChatSettingsForm, MessageForm
from apps.chat.models import MessageReport, MessageStatus, PeerMode, SenderRole
from apps.core.api import DomainError
from apps.web.coordinator_nav import invalidate_counters
from apps.web.mixins import CoordinatorRequiredMixin
from apps.web.throttle import ThrottledFormMixin
from apps.web.views.chat import organizer_author, seen_by_human

INBOX_TEMPLATE = "web/coordinator/chat.html"
THREAD_FRAGMENT = "web/chat/_thread.html"
MESSAGES_FRAGMENT = "web/chat/_messages.html"
MODERATION_TEMPLATE = "web/coordinator/chat_moderation.html"
SETTINGS_TEMPLATE = "web/coordinator/chat_settings.html"

#: Ile pozycji kolejki naraz – bez stronicowania, jak kolejka forum: ma być pusta, nie przewijana.
QUEUE_LIMIT = 200

#: Ile rozmów w skrzynce naraz. Starsze rozmowy wracają na górę z pierwszą nową wiadomością.
INBOX_LIMIT = 300


def full_name(participant) -> str:
    """Pełne dane uczestnika dla koordynatora: imię, nazwisko i kod – jak na karcie uczestnika.

    Koordynator jest administratorem tych danych i widzi je w całym panelu; skrót „Imię N.” jest
    ochroną **przed innymi uczestnikami**, nie przed organizatorem.
    """
    if participant is None:
        return "Użytkownik usunięty"
    user = participant.user
    name = f"{user.first_name} {user.last_name}".strip() or user.email
    return f"{name} ({participant.public_code})"


def _pk(raw) -> int:
    try:
        return int(raw)
    except TypeError, ValueError:
        return -1


class CoordinatorChatMixin(CoordinatorRequiredMixin):
    """Rola koordynatora, potem przełącznik modułu (poza ekranem ustawień)."""

    requires_enabled = True
    _role_cache: bool | None = None

    def has_role(self, user) -> bool:
        if self._role_cache is None:
            self._role_cache = super().has_role(user)
        return self._role_cache

    def dispatch(self, request, *args, **kwargs):
        if (
            self.requires_enabled
            and request.user.is_authenticated
            and self.has_role(request.user)
            and not chat.is_enabled(self.competition)
        ):
            messages.info(request, "Wiadomości są w tym konkursie wyłączone – włączysz je tutaj.")
            return redirect(reverse("web:coordinator-chat-settings"))
        return super().dispatch(request, *args, **kwargs)

    def is_htmx(self) -> bool:
        return bool(self.request.headers.get("HX-Request"))


class CoordinatorInboxMixin(CoordinatorChatMixin):
    def inbox_rows(self, *, unread_only: bool = False, current_pk=None) -> list[dict]:
        rows = []
        for conversation in chat.organizer_inbox(self.competition, unread_only=unread_only)[:INBOX_LIMIT]:
            rows.append(
                {
                    "conversation": conversation,
                    "label": full_name(conversation.participant),
                    "is_organizer": False,
                    "snippet": conversation.last_body,
                    "hidden": conversation.last_status == MessageStatus.HIDDEN,
                    "own": conversation.last_role == SenderRole.ORGANIZER,
                    "at": conversation.last_created_at,
                    "unread": conversation.organizer_unread_since is not None
                    and conversation.pk != current_pk,
                    "current": conversation.pk == current_pk,
                    "url": reverse("web:coordinator-chat-thread", args=[conversation.pk]),
                }
            )
        return rows

    def entries(self, conversation) -> list[dict]:
        rows = list(chat.organizer_messages(conversation).order_by("-created_at", "-id")[:200])
        entries = []
        for message in reversed(rows):
            own = message.sender_role == SenderRole.ORGANIZER
            entries.append(
                {
                    "message": message,
                    "author": organizer_author(message.sender)
                    if own
                    else full_name(conversation.participant),
                    "own": own,
                    "organizer": own,
                    "can_report": False,
                    "reported": False,
                }
            )
        return entries

    def thread_context(self, conversation, form=None, *, error: str = "", form_action: str = "", label=""):
        refusal = ""
        if conversation is not None and conversation.participant_id is None:
            refusal = "Konto tego uczestnika zostało usunięte – nie ma do kogo pisać."
        return {
            "conversation": conversation,
            "thread_label": full_name(conversation.participant) if conversation is not None else label,
            "is_peer": False,
            "entries": self.entries(conversation) if conversation is not None else [],
            "form": form or MessageForm(),
            "form_error": error,
            "form_action": form_action
            or (
                reverse("web:coordinator-chat-thread", args=[conversation.pk])
                if conversation is not None
                else ""
            ),
            "poll_url": (
                f"{reverse('web:coordinator-chat-thread', args=[conversation.pk])}?fragment=messages"
                f"&v={chat.thread_version(conversation)}"
                if conversation is not None
                else ""
            ),
            "refusal": refusal,
            "can_block": False,
            "privacy_notice": "",
            "organizer_view": True,
            "participant_card_url": (
                reverse("web:coordinator-participant", args=[conversation.participant_id])
                if conversation is not None and conversation.participant_id
                else ""
            ),
        }


class CoordinatorChatView(CoordinatorInboxMixin, View):
    """``/coordinator/chat/`` – skrzynka rozmów organizatorskich; ``?filter=unread`` – nieprzeczytane."""

    def get(self, request):
        unread_only = request.GET.get("filter") == "unread"
        context = {
            "rows": self.inbox_rows(unread_only=unread_only),
            "unread_only": unread_only,
            "thread_open": False,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context)


class CoordinatorChatThreadView(CoordinatorInboxMixin, ThrottledFormMixin, View):
    """``/coordinator/chat/<id>/`` – wątek z uczestnikiem. Rozmowa między uczestnikami tu nie wejdzie.

    Limit ``chat`` także po stronie organizatora: konto koordynatora przejęte albo skrypt w jego
    przeglądarce nie może zasypać uczestników wiadomościami (i listami o nich).
    """

    throttle_scope = "chat"

    def _conversation(self, pk):
        try:
            return chat.organizer_conversation(self.competition, pk)
        except DomainError as exc:
            raise Http404(str(exc.detail)) from exc

    def get(self, request, pk: int):
        conversation = self._conversation(pk)
        fragment = request.GET.get("fragment") == "messages"
        # Odczyt jest wspólny dla całego zespołu, więc schowana karta jednego koordynatora nie może
        # gasić odznaki pozostałym: odpytanie w tle nie oznacza przeczytania (``seen_by_human``).
        if not fragment or seen_by_human(request):
            was_unread = conversation.organizer_unread_since is not None
            chat.mark_organizer_read(conversation)
            if was_unread:
                invalidate_counters(self.competition)
        if fragment:
            if request.GET.get("v") == chat.thread_version(conversation):
                return HttpResponse(status=204)
            return TemplateResponse(request, MESSAGES_FRAGMENT, self.thread_context(conversation))
        context = {
            "rows": self.inbox_rows(current_pk=conversation.pk),
            **self.thread_context(conversation),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context)

    def post(self, request, pk: int):
        conversation = self._conversation(pk)
        form = MessageForm(request.POST)
        error = ""
        if form.is_valid():
            try:
                chat.send_organizer_message(
                    user=request.user,
                    competition=self.competition,
                    conversation=conversation,
                    body=form.cleaned_data["body"],
                    request=request,
                )
            except DomainError as exc:
                error = str(exc.detail)
            else:
                invalidate_counters(self.competition)
                if not self.is_htmx():
                    return redirect(reverse("web:coordinator-chat-thread", args=[conversation.pk]))
                form = MessageForm()
        if self.is_htmx():
            return TemplateResponse(
                request, THREAD_FRAGMENT, self.thread_context(conversation, form, error=error)
            )
        if error:
            messages.error(request, error)
        context = {
            "rows": self.inbox_rows(current_pk=conversation.pk),
            **self.thread_context(conversation, form, error=error),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context, status=400)


class CoordinatorChatNewView(CoordinatorInboxMixin, ThrottledFormMixin, View):
    """``/coordinator/chat/new/<participant_pk>/`` – „Napisz wiadomość” z karty uczestnika.

    Istniejąca rozmowa z tym uczestnikiem – przekierowanie do niej. Rozmowa powstaje dopiero
    z pierwszą wiadomością, jak po stronie uczestnika. Uczestnik innego konkursu – 404.
    """

    throttle_scope = "chat"

    def _participant(self, participant_pk):
        participant = (
            Participant.objects.filter(competition=self.competition, pk=participant_pk)
            .select_related("user")
            .first()
        )
        if participant is None:
            raise Http404("Nie ma takiego uczestnika.")
        return participant

    def _render(self, request, participant, form=None, *, status=200):
        context = {
            "rows": self.inbox_rows(),
            **self.thread_context(None, form, form_action=request.path, label=full_name(participant)),
            "participant_card_url": reverse("web:coordinator-participant", args=[participant.pk]),
            "thread_open": True,
        }
        return TemplateResponse(request, INBOX_TEMPLATE, context, status=status)

    def get(self, request, participant_pk: int):
        participant = self._participant(participant_pk)
        existing = chat.organizer_conversation_of(participant)
        if existing is not None:
            return redirect(reverse("web:coordinator-chat-thread", args=[existing.pk]))
        return self._render(request, participant)

    def post(self, request, participant_pk: int):
        participant = self._participant(participant_pk)
        form = MessageForm(request.POST)
        if not form.is_valid():
            return self._render(request, participant, form, status=400)
        try:
            message = chat.organizer_writes_to(
                user=request.user,
                competition=self.competition,
                participant=participant,
                body=form.cleaned_data["body"],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, participant, form, status=exc.status_code)
        return redirect(reverse("web:coordinator-chat-thread", args=[message.conversation_id]))


class CoordinatorChatModerationView(CoordinatorChatMixin, View):
    """``/coordinator/chat/moderation/`` – czekające, nieprzejrzane i zgłoszone wiadomości.

    Przy każdej pozycji stoi nadawca, odbiorca i kilka poprzednich wiadomości rozmowy – wyłącznie
    tych, które koordynator ma prawo czytać (``apps.chat.services.moderation_contexts``).
    """

    def get(self, request):
        competition = self.competition
        pending = list(chat.pending_messages(competition)[:QUEUE_LIMIT])
        review = list(chat.review_messages(competition)[:QUEUE_LIMIT])
        reports = list(chat.open_reports(competition)[:QUEUE_LIMIT])
        row = chat.settings_for(competition)
        # Kontekst całej kolejki jednym przebiegiem (``moderation_contexts``) – nie zapytanie na pozycję.
        self.contexts = chat.moderation_contexts([*pending, *review, *(report.message for report in reports)])
        context = {
            "pending": [self._item(message) for message in pending],
            "review": [self._item(message) for message in review],
            "reports": [{**self._item(report.message), "report": report} for report in reports],
            "queue_total": len(pending) + len(review) + len(reports),
            "peer_mode": chat.effective_peer_mode(competition, row=row),
            "saved_mode": row.peer_mode,
            "mode_none": PeerMode.NONE,
            "mode_off": PeerMode.OFF,
        }
        return TemplateResponse(request, MODERATION_TEMPLATE, context)

    def _item(self, message) -> dict:
        conversation = message.conversation
        low_high = [conversation.participant_low, conversation.participant_high]
        sender = next(
            (p for p in low_high if p is not None and message.sender_id and p.user_id == message.sender_id),
            None,
        )
        recipient = next((p for p in low_high if p is not None and p is not sender), None)
        return {
            "message": message,
            "sender": full_name(sender),
            "recipient": full_name(recipient),
            "context": [
                {"message": prior, "author": full_name(_side(conversation, prior))}
                for prior in self.contexts.get(message.pk, [])
            ],
        }

    def post(self, request):
        competition = self.competition
        action = request.POST.get("action") or ""
        try:
            message = self._perform(request, competition, action)
        except DomainError as exc:
            if exc.status_code == 404:
                raise Http404(str(exc.detail)) from exc
            messages.error(request, str(exc.detail))
        else:
            if message:
                messages.success(request, message)
        invalidate_counters(competition)
        return redirect(reverse("web:coordinator-chat-moderation"))

    def _perform(self, request, competition, action: str) -> str:
        if action == "bulk-approve":
            ids = [_pk(raw) for raw in request.POST.getlist("message")]
            count = chat.bulk_approve(competition=competition, actor=request.user, ids=ids, request=request)
            return f"Zaakceptowano wiadomości: {count}." if count else "Nie zaznaczono żadnej wiadomości."
        if action == "resolve-report":
            report = (
                MessageReport.objects.for_competition(competition)
                .filter(pk=_pk(request.POST.get("report")))
                .first()
            )
            if report is None:
                raise Http404("Nie ma takiego zgłoszenia.")
            chat.resolve_report(report=report, actor=request.user, request=request)
            return "Zgłoszenie zostało zamknięte."
        message = chat.peer_message(competition, _pk(request.POST.get("message")))
        note = request.POST.get("note") or ""
        if action == "approve":
            result = chat.approve(message=message, actor=request.user, request=request)
            if result.status != MessageStatus.PUBLISHED:
                raise DomainError(
                    "Tej wiadomości nie można już doręczyć (odbiorca jej nie przyjmuje, konto nie "
                    "istnieje albo kanał jest wyłączony) – została odrzucona.",
                    "CHAT_UNDELIVERABLE",
                )
            return "Wiadomość została doręczona."
        if action == "reject":
            chat.reject(message=message, actor=request.user, note=note, request=request)
            return "Wiadomość została odrzucona – nadawca zobaczy uzasadnienie."
        if action == "hide":
            chat.hide(message=message, actor=request.user, note=note, request=request)
            return "Wiadomość została ukryta."
        if action == "reviewed":
            chat.mark_reviewed(message=message, actor=request.user, request=request)
            return "Wiadomość oznaczona jako przejrzana."
        raise Http404("Nieznana czynność.")


def _side(conversation, message):
    """Uczestnik, który wysłał ``message`` w rozmowie między uczestnikami – albo ``None``."""
    for participant in (conversation.participant_low, conversation.participant_high):
        if participant is not None and message.sender_id and participant.user_id == message.sender_id:
            return participant
    return None


class CoordinatorChatSettingsView(CoordinatorChatMixin, View):
    """``/coordinator/chat/settings/`` – moduł włączony/wyłączony i tryb rozmów między uczestnikami.

    Dostępny także przy wyłączonym module – to jedyne miejsce, w którym da się go włączyć.
    """

    requires_enabled = False

    def get(self, request):
        row = chat.settings_for(self.competition)
        form = ChatSettingsForm(
            initial={"enabled": row.enabled, "peer_mode": row.peer_mode, "e2e_enabled": row.e2e_enabled}
        )
        return self._render(request, form, row)

    def post(self, request):
        form = ChatSettingsForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, chat.settings_for(self.competition), status=400)
        try:
            chat.save_settings(
                competition=self.competition,
                actor=request.user,
                enabled=form.cleaned_data["enabled"],
                peer_mode=form.cleaned_data["peer_mode"],
                e2e_enabled=form.cleaned_data["e2e_enabled"],
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, chat.settings_for(self.competition), status=exc.status_code)
        invalidate_counters(self.competition)
        messages.success(request, "Ustawienia wiadomości zostały zapisane.")
        return redirect(reverse("web:coordinator-chat-settings"))

    def _render(self, request, form, row, *, status: int = 200):
        stage = chat.forcing_stage(self.competition)
        selected = form["peer_mode"].value() or row.peer_mode
        context = {
            "form": form,
            "chat_settings": row,
            "forcing_stage": stage,
            "effective_mode": chat.effective_peer_mode(self.competition, row=row),
            "modes": [
                {
                    "value": value,
                    "label": label,
                    "help": PEER_MODE_HELP[PeerMode(value)],
                    "checked": value == selected,
                }
                for value, label in PeerMode.choices
            ],
        }
        return TemplateResponse(request, SETTINGS_TEMPLATE, context, status=status)
