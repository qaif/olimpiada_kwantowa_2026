"""Moderacja forum w panelu koordynatora: kolejka, wątki, działy i ustawienia.

Osobny moduł od ``forum.py`` z tego samego powodu, co przy zgłoszeniach
(``apps.web.views.coordinator_support``): tam każdy queryset zaczyna się od „co wolno mi
zobaczyć”, tutaj od „co czeka na moją decyzję”. Wspólny widok z warunkiem roli w środku metody
znaczyłby, że jedyną rzeczą stojącą między uczestnikiem a cudzym wpisem czekającym na moderację
jest ``if`` – a nie zestaw danych, do którego jego konto ma dostęp.

**Kolejność bramek jest tu odwrotna niż na ekranach uczestnika** i to jest świadome. Tam flaga
stoi przed rolą, bo rolą jest „prawie każdy zalogowany uczestnik tego konkursu”, więc 404 przed
sprawdzeniem roli jest zdaniem prostszym i prawdziwszym. Tutaj rolą jest **koordynator**, więc
flaga idzie za nią (``CoordinatorRequiredMixin`` najpierw, ``Http404`` potem) – dokładnie tak, jak
w ``coordinator_documents``: uczestnik dostaje 403 niezależnie od stanu przełącznika i nie
dowiaduje się z odpowiedzi, jak ten konkurs jest skonfigurowany.

**Czego tu nie ma:** zapisu w widoku. Każda decyzja moderatora przechodzi przez
``apps.forum.services`` – tam mieszka wpis audytowy, notatka dla autora i znacznik ``moderated_at``
(§ 2.1). Widok wyłącznie rozstrzyga, która czynność została kliknięta, i zamienia ``DomainError``
na komunikat.

**Dlaczego moderator czyta wątek na własnym ekranie** (``/coordinator/forum/t/<id>/``), a nie na
ekranie uczestnika: tamten pokazuje to, co widać, a moderacja polega na oglądaniu tego, czego nie
widać – wpisów ukrytych, odrzuconych i czekających. Wejście koordynatora na stronę uczestnika
z dodatkowym parametrem „pokaż wszystko” znaczyłoby jeden queryset obsługujący dwie sprzeczne
potrzeby i jeden warunek stojący między uczestnikiem a ukrytą wypowiedzią.
"""

from __future__ import annotations

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.core.api import DomainError
from apps.forum.forms import CategoryForm, ForumSettingsForm
from apps.forum.models import (
    FORUM_FLAG,
    ForumCategory,
    ForumPost,
    ForumReport,
    ForumThread,
    ModerationMode,
    ModerationStatus,
    display_author,
)
from apps.forum.services import (
    bulk_approve,
    effective_mode,
    moderate_post,
    moderate_thread,
    open_reports,
    pending_posts,
    pending_threads,
    resolve_report,
    save_category,
    save_settings,
    set_thread_flag,
    settings_for,
    stage_forcing_pre_moderation,
)
from apps.web.coordinator_nav import invalidate_counters
from apps.web.mixins import CoordinatorRequiredMixin

QUEUE_TEMPLATE = "web/coordinator/forum.html"
THREADS_TEMPLATE = "web/coordinator/forum_threads.html"
THREAD_TEMPLATE = "web/coordinator/forum_thread.html"
CATEGORIES_TEMPLATE = "web/coordinator/forum_categories.html"
SETTINGS_TEMPLATE = "web/coordinator/forum_settings.html"

#: Ile pozycji kolejki pokazujemy naraz. Bez stronicowania – ta sama reguła, co przy zgłoszeniach:
#: kolejka moderacyjna ma być pusta, a nie przewijana. Gdy urośnie ponad tę liczbę, problemem jest
#: zaległość, a nie brak kolejnej strony.
QUEUE_LIMIT = 200

#: Ile wątków na stronie spisu. Spis jest archiwum konkursu, więc tu stronicowanie **jest**.
THREADS_PER_PAGE = 50


class CoordinatorForumMixin(CoordinatorRequiredMixin):
    """Rola, potem przełącznik – uzasadnienie kolejności w docstringu modułu.

    Rolę odcina ``CoordinatorRequiredMixin.dispatch`` (403 dla każdego innego konta), a flagę –
    :attr:`forum_competition`, czytane na wejściu każdej metody HTTP. Bramka nie stoi we własnym
    ``dispatch``, bo wtedy rozstrzygałaby **przed** rolą: uczestnik dostawałby 404 przy
    wyłączonym forum i 403 przy włączonym, czyli odpowiedź serwisu mówiłaby mu, jak konkurs jest
    skonfigurowany.
    """

    @property
    def forum_competition(self):
        """Konkurs z żądania, o ile prowadzi forum. Inaczej 404."""
        competition = self.competition
        if competition is None or not competition.has_feature(FORUM_FLAG):
            raise Http404("Ten konkurs nie prowadzi forum.")
        return competition

    def panel_context(self) -> dict:
        """Kontekst wspólny czterech ekranów: stan forum i to, czy etap wymusza moderację.

        Moderator ma widzieć **obowiązujący** tryb, a nie zapisany: ekran ustawień pokazujący
        „po publikacji” w chwili, gdy każdy wpis i tak czeka na jego zatwierdzenie, opisywałby
        forum, którego nie ma (patrz ``apps.forum.services.effective_mode``).
        """
        competition = self.forum_competition
        return {
            "forum_settings": settings_for(competition),
            "forum_mode": effective_mode(competition),
            "open_stage": stage_forcing_pre_moderation(competition),
        }

    def report(self, request, exc: DomainError) -> None:
        messages.error(request, str(exc.detail))


def _ids(request, field: str) -> list[int]:
    """Identyfikatory zaznaczonych pozycji. Wartości niebędące liczbą po prostu odpadają.

    Cichy odrzut, a nie błąd: lista przychodzi z kratek formularza, więc nieliczba znaczy
    zepsute żądanie, a nie pomyłkę człowieka – i tak nie ma jej jak pokazać na ekranie.
    Zawężenie do konkursu robi ``bulk_approve``, nie ta funkcja.
    """
    values = []
    for raw in request.POST.getlist(field):
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            continue
    return values


class CoordinatorForumView(CoordinatorForumMixin, View):
    """``/coordinator/forum/`` – kolejka: wątki, wpisy i zgłoszenia czekające na decyzję.

    Trzy listy na jednym ekranie, bo to jedna czynność: „przejrzyj, co przyszło”. Rozbicie ich na
    trzy adresy znaczyłoby trzy miejsca, w które trzeba zajrzeć, żeby wiedzieć, czy forum jest
    obsłużone – a odznaka w menu i tak liczy je razem
    (``apps.forum.services.moderation_count``).
    """

    def get(self, request):
        competition = self.forum_competition
        threads = list(pending_threads(competition)[:QUEUE_LIMIT])
        posts = list(pending_posts(competition)[:QUEUE_LIMIT])
        reports = list(open_reports(competition)[:QUEUE_LIMIT])
        context = {
            **self.panel_context(),
            "threads": [
                {"thread": thread, "author": display_author(thread.author), "first": _first_post(thread)}
                for thread in threads
            ],
            "posts": [{"post": post, "author": display_author(post.author)} for post in posts],
            "reports": [
                {"report": report, "author": display_author(report.post.author)} for report in reports
            ],
            "queue_total": len(threads) + len(posts) + len(reports),
        }
        return TemplateResponse(request, QUEUE_TEMPLATE, context)

    def post(self, request):
        competition = self.forum_competition
        action = request.POST.get("action") or ""
        try:
            message = self._perform(request, competition, action)
        except DomainError as exc:
            self.report(request, exc)
        else:
            if message:
                messages.success(request, message)
        # Odznaka w menu liczy dokładnie te trzy listy, więc po każdej decyzji ma przestać
        # pokazywać liczbę sprzed minuty (``COUNTERS_CACHE_SECONDS``).
        invalidate_counters(competition)
        return redirect(reverse("web:coordinator-forum"))

    def _perform(self, request, competition, action: str) -> str:
        if action == "bulk-approve":
            count = bulk_approve(
                competition=competition,
                actor=request.user,
                thread_ids=_ids(request, "thread"),
                post_ids=_ids(request, "post"),
                request=request,
            )
            return f"Zatwierdzono pozycji: {count}." if count else "Nie zaznaczono żadnej pozycji."
        note = (request.POST.get("note") or "").strip()
        if action in ("approve-thread", "reject-thread"):
            thread = _thread_or_404(competition, request.POST.get("thread"))
            status = ModerationStatus.PUBLISHED if action == "approve-thread" else ModerationStatus.REJECTED
            moderate_thread(thread=thread, actor=request.user, status=status, note=note, request=request)
            return (
                f"Wątek „{thread.title}” został opublikowany."
                if status == ModerationStatus.PUBLISHED
                else f"Wątek „{thread.title}” został odrzucony."
            )
        if action in ("approve-post", "reject-post"):
            post = _post_or_404(competition, request.POST.get("post"))
            status = ModerationStatus.PUBLISHED if action == "approve-post" else ModerationStatus.REJECTED
            moderate_post(post=post, actor=request.user, status=status, note=note, request=request)
            return (
                "Wpis został opublikowany."
                if status == ModerationStatus.PUBLISHED
                else "Wpis został odrzucony – autor zobaczy uzasadnienie przy swoim wpisie."
            )
        if action == "resolve-report":
            report = _report_or_404(competition, request.POST.get("report"))
            resolve_report(report=report, actor=request.user, request=request)
            return "Zgłoszenie zostało rozpatrzone."
        raise Http404("Nieznana czynność.")


def _first_post(thread: ForumThread) -> ForumPost | None:
    """Pierwszy wpis wątku – to jego treść, i to ją czyta moderator w kolejce.

    Wątek w kolejce bez treści byłby samym tematem, a temat nie wystarcza do decyzji.
    """
    return thread.posts.order_by("created_at", "id").first()


def _thread_or_404(competition, raw) -> ForumThread:
    thread = ForumThread.objects.for_competition(competition).filter(pk=_pk(raw)).first()
    if thread is None:
        raise Http404("Nie ma takiego wątku.")
    return thread


def _post_or_404(competition, raw) -> ForumPost:
    post = ForumPost.objects.for_competition(competition).filter(pk=_pk(raw)).select_related("thread").first()
    if post is None:
        raise Http404("Nie ma takiego wpisu.")
    return post


def _report_or_404(competition, raw) -> ForumReport:
    report = (
        ForumReport.objects.for_competition(competition)
        .filter(pk=_pk(raw))
        .select_related("post", "post__thread")
        .first()
    )
    if report is None:
        raise Http404("Nie ma takiego zgłoszenia.")
    return report


def _pk(raw) -> int:
    """Identyfikator z formularza. Nie-liczba daje ``-1``, czyli wiersz, którego nie ma.

    Zamiana na 404 przez „pusty queryset” zamiast przez wyjątek konwersji: obie drogi kończą się
    tym samym zdaniem, ale ta nie wymaga ``try`` w czterech miejscach – a zawężenie do konkursu
    jedzie z managerem, więc podrzucony cudzy identyfikator i tak niczego nie znajdzie.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


class CoordinatorForumThreadsView(CoordinatorForumMixin, View):
    """``/coordinator/forum/threads/`` – spis **wszystkich** wątków konkursu z filtrem stanu.

    Filtr jest parametrem adresu, a nie formularzem POST: adres z filtrem ma dać się zapisać
    w zakładkach – ta sama reguła, co w kolejce zgłoszeń.
    """

    def get(self, request):
        competition = self.forum_competition
        status = request.GET.get("status") or ""
        threads = (
            ForumThread.objects.for_competition(competition)
            .select_related("category", "author")
            .order_by("-is_pinned", "-last_activity_at", "-id")
        )
        if status in ModerationStatus.values:
            threads = threads.filter(status=status)
        page = Paginator(threads, THREADS_PER_PAGE).get_page(request.GET.get("page"))
        context = {
            **self.panel_context(),
            "status": status,
            "status_choices": ModerationStatus.choices,
            "page_obj": page,
            "paginator": page.paginator,
            "rows": [
                {"thread": thread, "author": display_author(thread.author)} for thread in page.object_list
            ],
            "filter_query": f"status={status}" if status else "",
        }
        return TemplateResponse(request, THREADS_TEMPLATE, context)


class CoordinatorForumThreadView(CoordinatorForumMixin, View):
    """``/coordinator/forum/t/<id>/`` – wątek oczami moderatora: **wszystkie** wpisy, każdy stan.

    Stąd idą czynności na pojedynczym wpisie (ukrycie, przywrócenie, odrzucenie) oraz na wątku
    (przypięcie, zamknięcie, ukrycie). Uzasadnienie osobnego ekranu stoi w docstringu modułu.
    """

    def get(self, request, pk: int):
        return self._render(request, _thread_or_404(self.forum_competition, pk))

    def post(self, request, pk: int):
        competition = self.forum_competition
        thread = _thread_or_404(competition, pk)
        action = request.POST.get("action") or ""
        note = (request.POST.get("note") or "").strip()
        try:
            message = self._perform(request, competition, thread, action, note)
        except DomainError as exc:
            self.report(request, exc)
        else:
            messages.success(request, message)
        invalidate_counters(competition)
        return redirect(reverse("web:coordinator-forum-thread", args=[thread.pk]))

    def _perform(self, request, competition, thread: ForumThread, action: str, note: str) -> str:
        if action in ("pin", "unpin"):
            set_thread_flag(
                thread=thread,
                actor=request.user,
                field="is_pinned",
                value=action == "pin",
                request=request,
            )
            return "Wątek przypięty." if action == "pin" else "Wątek odpięty."
        if action in ("lock", "unlock"):
            set_thread_flag(
                thread=thread,
                actor=request.user,
                field="is_locked",
                value=action == "lock",
                request=request,
            )
            return "Wątek zamknięty dla nowych wpisów." if action == "lock" else "Wątek otwarty."
        if action in _THREAD_STATUS_ACTIONS:
            moderate_thread(
                thread=thread,
                actor=request.user,
                status=_THREAD_STATUS_ACTIONS[action],
                note=note,
                request=request,
            )
            return "Stan wątku został zmieniony."
        if action in _POST_STATUS_ACTIONS:
            post = _post_or_404(competition, request.POST.get("post"))
            if post.thread_id != thread.pk:
                # Wpis z innego wątku pod adresem tego wątku to zepsute żądanie, a nie pomyłka.
                raise Http404("Nie ma takiego wpisu w tym wątku.")
            moderate_post(
                post=post,
                actor=request.user,
                status=_POST_STATUS_ACTIONS[action],
                note=note,
                request=request,
            )
            return "Stan wpisu został zmieniony."
        raise Http404("Nieznana czynność.")

    def _render(self, request, thread: ForumThread):
        posts = (
            ForumPost.objects.filter(thread=thread)
            .select_related("author", "moderated_by")
            .order_by("created_at", "id")
        )
        reports = {
            report.post_id: report
            for report in ForumReport.objects.for_competition(thread.competition)
            .filter(post__thread=thread, resolved_at__isnull=True)
            .order_by("created_at")
        }
        context = {
            **self.panel_context(),
            "thread": thread,
            "thread_author": display_author(thread.author),
            "entries": [
                {
                    "post": post,
                    "author": display_author(post.author),
                    "report": reports.get(post.pk),
                }
                for post in posts
            ],
        }
        return TemplateResponse(request, THREAD_TEMPLATE, context)


#: Czynność z formularza → stan, który ma przyjąć wiersz. Słownik, a nie łańcuch ``if``: nowa
#: czynność bez wpisu tutaj po prostu nie istnieje (404), zamiast po cichu wpaść w gałąź obok.
_THREAD_STATUS_ACTIONS = {
    "thread-publish": ModerationStatus.PUBLISHED,
    "thread-hide": ModerationStatus.HIDDEN,
    "thread-reject": ModerationStatus.REJECTED,
}

_POST_STATUS_ACTIONS = {
    "post-publish": ModerationStatus.PUBLISHED,
    "post-hide": ModerationStatus.HIDDEN,
    "post-reject": ModerationStatus.REJECTED,
}


class CoordinatorForumCategoriesView(CoordinatorForumMixin, View):
    """``/coordinator/forum/categories/`` – działy forum: spis i formularz w jednym.

    Jeden ekran, a nie lista plus osobna strona formularza: dział ma cztery pola, a zakłada się go
    wtedy, gdy się patrzy na listę tych, które już są – żeby nie założyć drugiego takiego samego.
    """

    def get(self, request):
        return self._render(request, CategoryForm(), None)

    def post(self, request):
        competition = self.forum_competition
        category = None
        raw = request.POST.get("category")
        if raw:
            category = ForumCategory.objects.for_competition(competition).filter(pk=_pk(raw)).first()
            if category is None:
                raise Http404("Nie ma takiego działu.")
        form = CategoryForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, category, status=400)
        try:
            save_category(
                competition=competition,
                actor=request.user,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                ordering=form.cleaned_data["ordering"],
                is_open=form.cleaned_data["is_open"],
                category=category,
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, category, status=exc.status_code)
        messages.success(request, "Dział został zapisany.")
        return redirect(reverse("web:coordinator-forum-categories"))

    def _render(self, request, form, category, *, status: int = 200):
        context = {
            **self.panel_context(),
            "form": form,
            "category": category,
            "categories": list(
                ForumCategory.objects.for_competition(self.forum_competition).order_by(
                    "ordering", "name", "id"
                )
            ),
        }
        return TemplateResponse(request, CATEGORIES_TEMPLATE, context, status=status)


class CoordinatorForumSettingsView(CoordinatorForumMixin, View):
    """``/coordinator/forum/settings/`` – tryb moderacji i przełącznik „tylko do odczytu”.

    Ekran mówi **wprost**, gdy ustawienie jest w tej chwili nadpisane przez otwarty etap: pole
    wyboru pokazujące „po publikacji” w chwili, gdy każdy wpis i tak czeka w kolejce, byłoby
    ustawieniem, które kłamie (``apps.forum.services.effective_mode``).
    """

    def get(self, request):
        row = settings_for(self.forum_competition)
        form = ForumSettingsForm(initial={"mode": row.mode, "is_read_only": row.is_read_only})
        return self._render(request, form)

    def post(self, request):
        form = ForumSettingsForm(request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            save_settings(
                competition=self.forum_competition,
                actor=request.user,
                mode=form.cleaned_data["mode"],
                is_read_only=form.cleaned_data["is_read_only"],
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        messages.success(request, "Ustawienia forum zostały zapisane.")
        return redirect(reverse("web:coordinator-forum-settings"))

    def _render(self, request, form, *, status: int = 200):
        context = {
            **self.panel_context(),
            "form": form,
            "mode_pre": ModerationMode.PRE,
        }
        return TemplateResponse(request, SETTINGS_TEMPLATE, context, status=status)
