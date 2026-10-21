"""``/me/notifications/`` – lista powiadomień uczestnika, otwarcie pozycji i „oznacz wszystkie”.

Dostęp: wyłącznie uczestnik tego konkursu (``ParticipantRequiredMixin``). Każde zapytanie idzie po
parze (konto, konkurs) – pozycja innej osoby albo innego konkursu to 404, nie 403: nie ma się
dowiedzieć, że taki numer w ogóle istnieje.

Otwarcie pozycji to GET (zwykły odnośnik, działa z klawiatury i w nowej karcie), który oznacza ją
jako przeczytaną i przekierowuje do celu. Cel jest nazwą adresu z bazy, nigdy parametrem żądania –
więc przekierowanie nie może wyprowadzić poza serwis.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

from apps.web.mixins import ParticipantRequiredMixin
from apps.web.throttle import PerAccountThrottleMixin

from . import services
from .models import Notification


class NotificationListView(ParticipantRequiredMixin, TemplateView):
    template_name = "notifications/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = services.page_for(self.request.user, self.competition, self.request.GET.get("page"))
        context.update(
            {
                "page_obj": page,
                "rows": [services.present(row) for row in page.object_list],
                "unread": services.unread_count(self.request),
                "retention_days": services.retention_days(),
            }
        )
        return context


class NotificationOpenView(ParticipantRequiredMixin, View):
    def get(self, request, pk: int):
        try:
            url = services.open_notification(request.user, self.competition, pk)
        except Notification.DoesNotExist as exc:
            raise Http404 from exc
        return HttpResponseRedirect(url or reverse("web:participant-notifications"))


class NotificationReadAllView(ParticipantRequiredMixin, PerAccountThrottleMixin, View):
    #: Kubełek konta 60/min – zapis dotyczy wyłącznie własnych wierszy, limit chroni tylko bazę.
    throttle_scope = "anon"

    def post(self, request):
        changed = services.mark_all_read(request.user, self.competition)
        services.forget_unread_count(request)
        if changed:
            messages.success(request, _("Wszystkie powiadomienia oznaczono jako przeczytane."))
        return HttpResponseRedirect(reverse("web:participant-notifications"))
