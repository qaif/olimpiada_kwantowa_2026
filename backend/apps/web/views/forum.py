"""Forum uczestników: lista działów, wątek, pisanie, zgłaszanie i „Twoje wpisy”.

Podział na ten moduł i ``coordinator_forum`` odpowiada podziałowi ról, a nie podziałowi kodu –
ta sama reguła, co przy zgłoszeniach (``apps.web.views.support``): tutaj każdy queryset zaczyna
się od „co wolno mi zobaczyć”, tam – od „co czeka na moją decyzję”. Gdyby oba ekrany dzieliły
widok z parametrem roli, jedyną rzeczą stojącą między uczestnikiem a cudzym wpisem czekającym
na moderację byłby warunek w środku metody. Tutaj takiego wpisu po prostu nie ma w queryseciie
(``apps.forum.services.visible_posts``), a odpowiedzią jest 404.

**Kolejność bramek** i dlaczego jest inna niż w ``coordinator_documents``: tam flaga stoi
**za** rolą, bo rolą jest koordynator i stan przełącznika nie ma być widoczny dla uczestnika.
Tutaj rolą jest „uczestnik, recenzent albo koordynator tego konkursu”, czyli prawie każdy, kto
w ogóle może się tu zalogować – więc flaga jako sekret niczego by nie chroniła, a 404 przed
sprawdzeniem roli jest zdaniem prostszym i prawdziwszym: **pod tym adresem w tym konkursie nic
nie stoi**. Osoba niezalogowana dostaje przed jednym i drugim przekierowanie na logowanie, bo
forum nie jest publiczne (uzasadnienie w ``apps.forum.services.can_read``).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import View

from apps.core.api import DomainError
from apps.forum.forms import PostForm, ReportForm, ThreadForm
from apps.forum.models import (
    FORUM_FLAG,
    POSTS_PER_PAGE,
    ForumCategory,
    ForumPost,
    ForumThread,
    ModerationMode,
    ModerationStatus,
    display_author,
)
from apps.forum.services import (
    author_badge,
    can_edit,
    can_read,
    categories_with_counts,
    create_thread,
    delete_own_post,
    edit_post,
    effective_mode,
    own_posts,
    own_threads,
    reply,
    report_post,
    settings_for,
    stage_forcing_pre_moderation,
    thread_visible_to,
    visible_posts,
    visible_threads,
)
from apps.web.throttle import ThrottledFormMixin

INDEX_TEMPLATE = "web/forum/index.html"
CATEGORY_TEMPLATE = "web/forum/category.html"
THREAD_TEMPLATE = "web/forum/thread.html"
NEW_THREAD_TEMPLATE = "web/forum/thread_new.html"
EDIT_TEMPLATE = "web/forum/post_edit.html"
REPORT_TEMPLATE = "web/forum/post_report.html"
MINE_TEMPLATE = "web/forum/mine.html"

#: Ile najnowszych wątków pokazuje strona główna forum. Krótka lista „co się dzieje”, a nie
#: archiwum – od archiwum jest dział.
LATEST_THREADS = 10

#: Ile wątków na stronie działu. Więcej niż wpisów w wątku, bo wiersz listy to jedna linijka.
THREADS_PER_PAGE = 30

#: Zdanie, które stoi nad **każdym** formularzem pisania w czasie otwartego etapu. Jedna stała,
#: bo to jest cytat z regulaminu, a nie treść ekranu – i ma brzmieć tak samo w trzech miejscach.
OPEN_STAGE_WARNING = (
    "Trwa etap przyjmujący rozwiązania. Regulamin zabrania omawiania zadań tego etapu "
    "(§ 10 ust. 2 i § 17) – wpisy o treści zadań będą odrzucane, a próba uzyskania lub podania "
    "rozwiązania jest podstawą do dyskwalifikacji. Do czasu zamknięcia etapu każdy wpis czeka "
    "na zatwierdzenie przez organizatora."
)


class ForumAccessMixin(LoginRequiredMixin):
    """Trzy bramki forum w jednym miejscu: logowanie, istnienie forum w tym konkursie, rola."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            competition = getattr(request, "competition", None)
            if competition is None or not competition.has_feature(FORUM_FLAG):
                raise Http404("Ten konkurs nie prowadzi forum.")
            if not can_read(request.user, competition):
                raise PermissionDenied("Forum jest dostępne dla uczestników tego konkursu.")
        return super().dispatch(request, *args, **kwargs)

    @property
    def competition(self):
        return getattr(self.request, "competition", None)

    def base_context(self) -> dict:
        """Kontekst wspólny każdego ekranu forum: stan forum i ostrzeżenie o otwartym etapie.

        Liczony raz na żądanie w widoku, a nie w procesorze kontekstu: procesor odpalałby się na
        **każdej** stronie serwisu, a to jest pytanie o etapy edycji, czyli zapytanie do bazy.
        """
        open_stage = stage_forcing_pre_moderation(self.competition)
        return {
            "forum_settings": settings_for(self.competition),
            "forum_mode": effective_mode(self.competition),
            "open_stage": open_stage,
            "open_stage_warning": OPEN_STAGE_WARNING if open_stage is not None else "",
        }


def render_posts(posts, user, competition) -> list[dict]:
    """Wpisy w postaci, w której czyta je szablon – **bez** obiektu autora.

    Szablon dostaje gotowy podpis i gotową odznakę, a nie ``post.author``. To nie jest wygoda:
    dopóki w kontekście stoi obiekt użytkownika, każdy przyszły ``{{ post.author.email }}``
    w szablonie jest jedną literówką od wycieku adresu (albo szkoły, albo kodu publicznego przez
    profil). Tutaj nie ma czego wypisać – patrz ``apps.forum.models.display_author``.

    Odznaka liczy się **raz na autora**, a nie raz na wpis: strona wątku to dwadzieścia wypowiedzi
    zwykle kilku osób, a odznaka kosztuje zapytanie o role. Dzięki temu koszt strony rośnie
    z liczbą rozmówców, a nie z długością rozmowy.
    """
    badges: dict[int, str] = {}
    entries = []
    for post in posts:
        if post.author_id is not None and post.author_id not in badges:
            badges[post.author_id] = author_badge(post.author, competition)
        entries.append(
            {
                "post": post,
                "author": display_author(post.author),
                "badge": badges.get(post.author_id, ""),
                "can_edit": can_edit(post, user),
                "is_own": bool(post.author_id) and post.author_id == getattr(user, "pk", None),
            }
        )
    return entries


class ForumIndexView(ForumAccessMixin, View):
    """``/forum/`` – działy razem z liczbą wątków i krótka lista ostatnich rozmów."""

    def get(self, request):
        threads = (
            visible_threads(self.competition, request.user)
            .select_related("category")
            .order_by("-last_activity_at", "-id")[:LATEST_THREADS]
        )
        context = {
            **self.base_context(),
            "categories": list(categories_with_counts(self.competition, request.user)),
            "threads": list(threads),
        }
        return TemplateResponse(request, INDEX_TEMPLATE, context)


class ForumCategoryView(ForumAccessMixin, View):
    """``/forum/<dział>/`` – wątki jednego działu, przypięte na górze."""

    def get(self, request, slug: str):
        category = ForumCategory.objects.for_competition(self.competition).filter(slug=slug).first()
        if category is None:
            raise Http404("Nie ma takiego działu.")
        threads = (
            visible_threads(self.competition, request.user)
            .filter(category=category)
            .select_related("category")
        )
        page = Paginator(threads, THREADS_PER_PAGE).get_page(request.GET.get("strona"))
        context = {
            **self.base_context(),
            "category": category,
            "page_obj": page,
            "paginator": page.paginator,
            "threads": list(page.object_list),
        }
        return TemplateResponse(request, CATEGORY_TEMPLATE, context)


class ForumThreadView(ForumAccessMixin, ThrottledFormMixin, View):
    """``/forum/t/<id>/`` – wątek ze stronicowaniem i formularzem odpowiedzi.

    Cudzy wątek (innego konkursu, odrzucony, czekający na moderację) to **404**, a nie 403:
    identyfikatory są kolejne, więc 403 byłoby licznikiem rozmów w serwisie (§ 3.6).
    """

    throttle_scope = "forum"

    def get(self, request, pk: int):
        return self._render(request, self._thread(request, pk), PostForm())

    def post(self, request, pk: int):
        thread = self._thread(request, pk)
        form = PostForm(request.POST)
        if not form.is_valid():
            return self._render(request, thread, form, status=400)
        try:
            reply(
                user=request.user,
                competition=self.competition,
                thread=thread,
                body=form.cleaned_data["body"],
                request=request,
            )
        except DomainError as exc:
            messages.error(request, str(exc.detail))
            return self._render(request, thread, PostForm(), status=exc.status_code)
        messages.success(request, self._posted_message())
        return redirect(f"{reverse('web:forum-thread', args=[thread.pk])}?strona=ostatnia")

    def _posted_message(self) -> str:
        if effective_mode(self.competition) == ModerationMode.PRE:
            return "Wpis czeka na zatwierdzenie przez organizatora. Widzisz go tylko Ty."
        return "Wpis został dodany."

    def _thread(self, request, pk: int) -> ForumThread:
        thread = (
            visible_threads(self.competition, request.user).filter(pk=pk).select_related("category").first()
        )
        if thread is None:
            raise Http404("Nie ma takiego wątku.")
        return thread

    def _render(self, request, thread: ForumThread, form, *, status: int = 200):
        posts = visible_posts(thread, request.user).select_related("author")
        paginator = Paginator(posts, POSTS_PER_PAGE)
        number = request.GET.get("strona")
        # „ostatnia” zamiast numeru: po dodaniu wpisu wracamy na jego stronę, a nie na pierwszą.
        # Numer wyliczony w widoku rozjechałby się z paginatorem przy pierwszym wpisie ukrytym.
        page = paginator.get_page(paginator.num_pages if number == "ostatnia" else number)
        base = self.base_context()
        context = {
            **base,
            "thread": thread,
            "page_obj": page,
            "paginator": paginator,
            "entries": render_posts(page.object_list, request.user, self.competition),
            "form": form,
            # Trzy warunki, a nie dwa: „tylko do odczytu” jest tą samą odmową, co zamknięty wątek,
            # i musi stać **tutaj**, a nie w szablonie. Inaczej każdy kolejny szablon forum
            # wyprowadzałby tę regułę od nowa, a pierwszy, który o niej zapomni, pokaże formularz
            # odpowiedzi, po którym ``ensure_can_write`` i tak odmówi (``FORUM_READ_ONLY``).
            "can_write": (
                not thread.is_locked and thread.is_published and not base["forum_settings"].is_read_only
            ),
        }
        return TemplateResponse(request, THREAD_TEMPLATE, context, status=status)


class ForumThreadCreateView(ForumAccessMixin, ThrottledFormMixin, View):
    """``/forum/new/`` – nowy wątek. Dział wybiera się z listy **tego** konkursu."""

    throttle_scope = "forum"

    def get(self, request):
        return self._render(request, self._form(request))

    def post(self, request):
        form = self._form(request, data=request.POST)
        if not form.is_valid():
            return self._render(request, form, status=400)
        try:
            thread = create_thread(
                user=request.user,
                competition=self.competition,
                category=form.cleaned_data["category"],
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                request=request,
            )
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, form, status=exc.status_code)
        messages.success(request, self._created_message())
        return redirect(reverse("web:forum-thread", args=[thread.pk]))

    def _created_message(self) -> str:
        if effective_mode(self.competition) == ModerationMode.PRE:
            return "Wątek czeka na zatwierdzenie przez organizatora. Widzisz go tylko Ty."
        return "Wątek został założony."

    def _form(self, request, data=None) -> ThreadForm:
        categories = ForumCategory.objects.for_competition(self.competition).filter(is_open=True)
        return ThreadForm(data, categories=categories)

    def _render(self, request, form, *, status: int = 200):
        return TemplateResponse(
            request, NEW_THREAD_TEMPLATE, {**self.base_context(), "form": form}, status=status
        )


class ForumPostEditView(ForumAccessMixin, ThrottledFormMixin, View):
    """``/forum/p/<id>/edit/`` – poprawka własnego wpisu w oknie kwadransa."""

    throttle_scope = "forum"

    def get(self, request, pk: int):
        post = self._post(request, pk)
        return self._render(request, post, PostForm(initial={"body": post.body}))

    def post(self, request, pk: int):
        post = self._post(request, pk)
        form = PostForm(request.POST)
        if not form.is_valid():
            return self._render(request, post, form, status=400)
        try:
            edit_post(post=post, user=request.user, body=form.cleaned_data["body"], request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, post, form, status=exc.status_code)
        messages.success(request, "Wpis został poprawiony.")
        return redirect(reverse("web:forum-thread", args=[post.thread_id]))

    def _post(self, request, pk: int) -> ForumPost:
        post = (
            ForumPost.objects.for_competition(self.competition)
            .filter(pk=pk, author=request.user)
            .select_related("thread")
            .first()
        )
        if post is None:
            raise Http404("Nie ma takiego wpisu.")
        return post

    def _render(self, request, post: ForumPost, form, *, status: int = 200):
        context = {
            **self.base_context(),
            "post": post,
            "form": form,
            "can_edit": can_edit(post, request.user),
        }
        return TemplateResponse(request, EDIT_TEMPLATE, context, status=status)


class ForumPostDeleteView(ForumAccessMixin, ThrottledFormMixin, View):
    """``/forum/p/<id>/delete/`` – usunięcie własnego wpisu (miękkie, do stanu ukrytego)."""

    throttle_scope = "forum"

    def post(self, request, pk: int):
        post = ForumPost.objects.for_competition(self.competition).filter(pk=pk, author=request.user).first()
        if post is None:
            raise Http404("Nie ma takiego wpisu.")
        try:
            delete_own_post(post=post, user=request.user, request=request)
        except DomainError as exc:
            messages.error(request, str(exc.detail))
        else:
            messages.success(request, "Wpis został usunięty.")
        return redirect(reverse("web:forum-thread", args=[post.thread_id]))


class ForumPostReportView(ForumAccessMixin, ThrottledFormMixin, View):
    """``/forum/p/<id>/report/`` – „Zgłoś wpis”."""

    throttle_scope = "forum"

    def get(self, request, pk: int):
        return self._render(request, self._post(request, pk), ReportForm())

    def post(self, request, pk: int):
        post = self._post(request, pk)
        form = ReportForm(request.POST)
        if not form.is_valid():
            return self._render(request, post, form, status=400)
        try:
            report_post(post=post, user=request.user, reason=form.cleaned_data["reason"], request=request)
        except DomainError as exc:
            form.add_error(None, str(exc.detail))
            return self._render(request, post, form, status=exc.status_code)
        messages.success(request, "Zgłoszenie trafiło do organizatora. Dziękujemy.")
        return redirect(reverse("web:forum-thread", args=[post.thread_id]))

    def _post(self, request, pk: int) -> ForumPost:
        post = (
            ForumPost.objects.for_competition(self.competition)
            .filter(pk=pk, status=ModerationStatus.PUBLISHED)
            .select_related("thread")
            .first()
        )
        if post is None:
            raise Http404("Nie ma takiego wpisu.")
        return post

    def _render(self, request, post: ForumPost, form, *, status: int = 200):
        context = {
            **self.base_context(),
            "post": post,
            "author": display_author(post.author),
            "form": form,
        }
        return TemplateResponse(request, REPORT_TEMPLATE, context, status=status)


class ForumMyPostsView(ForumAccessMixin, View):
    """``/forum/mine/`` – „Twoje wpisy” razem ze stanem i notatką moderatora.

    To jest **jedyne** miejsce, w którym autor dowiaduje się, że jego wpis został odrzucony i
    dlaczego. Listu o tym nie wysyłamy (uzasadnienie w docstringu ``apps.forum.services``), więc
    ten ekran musi być kompletny: są tu wpisy czekające, odrzucone, ukryte i opublikowane.

    **Dwie listy, nie jedna**, bo odrzucenie wątku i odrzucenie wpisu są dwiema różnymi decyzjami
    i niosą dwa różne uzasadnienia. Wątek odrzucony **po** zatwierdzeniu swojego pierwszego wpisu
    zostawiłby ten wpis w stanie „opublikowany” (``moderate_thread`` nie cofa cudzych decyzji), więc
    sama lista wpisów pokazywałaby autorowi zieloną odznakę pod tematem, którego nikt już nie widzi.

    Każdy wiersz niesie ``thread_visible``: odrzucony i ukryty wątek daje 404 także swojemu
    autorowi, a odnośnik, o którym z góry wiadomo, że nie zadziała, jest gorszy niż jego brak.
    """

    #: Ile pozycji wypisujemy. Ekran jest historią własnych wypowiedzi, a nie archiwum forum –
    #: autor, który napisał ich więcej, szuka konkretnej w wątku, a nie na tej liście.
    LIMIT = 200

    def get(self, request):
        posts = own_posts(request.user, self.competition)[: self.LIMIT]
        threads = own_threads(request.user, self.competition)[: self.LIMIT]
        context = {
            **self.base_context(),
            "posts": [
                {"post": post, "thread_visible": thread_visible_to(post.thread, request.user)}
                for post in posts
            ],
            "threads": [
                {"thread": thread, "visible": thread_visible_to(thread, request.user)} for thread in threads
            ],
        }
        return TemplateResponse(request, MINE_TEMPLATE, context)
