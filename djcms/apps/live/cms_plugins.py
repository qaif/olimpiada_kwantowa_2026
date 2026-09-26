"""Wtyczki żywe django CMS (§ 6.2 docs/tasks/DJ-01.md): terminy etapów, zadania, wyniki, archiwum,
a od DJ-02 (D9) także strony-dane redagowane dalej w Wagtailu: warsztaty i partnerzy.

Wspólne dla wszystkich:

- ``cache = False``. ``CMS_PLACEHOLDER_CACHE`` jest włączony dla treści redakcyjnej, a placeholder
  z choć jedną wtyczką bez bufora nie trafia do bufora w całości – stan etapu zmienia się z
  zegarem, a bufor placeholdera trzymałby „przed otwarciem” także po otwarciu (i odwrotnie),
- dane wyłącznie z API aplikacji głównej (``apps.live.data.fetch`` → ``apps.live.client``), jedno
  pobranie endpointu na odsłonę (pamięć żądania). Przy martwym API wtyczka rysuje
  ``{% dj_unavailable %}`` zamiast danych – strona ma kod 200 i ``X-Djcms-Degraded: 1``,
- szablony w ``templates/dj/live/`` są portem szablonów Wagtaila: te same znaczniki i klasy CSS,
  a daty i punkty jako gotowe napisy z API (``*_local_time``, ``*_display``).
"""

from __future__ import annotations

from cms.models import CMSPlugin
from cms.plugin_base import CMSPluginBase
from cms.plugin_pool import plugin_pool

from . import data
from .models import Problems, StageTimeline, WorkshopSchedule

MODULE = "Dane zawodów (z systemu)"


class LivePluginBase(CMSPluginBase):
    module = MODULE
    cache = False
    #: Wtyczki żywe nie mają dzieci – nic redakcyjnego nie wchodzi między dane zawodów.
    allow_children = False

    @staticmethod
    def request_from(context):
        return context.get("request")


@plugin_pool.register_plugin
class StageTimelinePlugin(LivePluginBase):
    """Terminy etapów bieżącej edycji (``GET stages``) – blok w treści albo sekcja strony głównej."""

    model = StageTimeline
    name = "Terminy etapów (z systemu)"
    render_template = "dj/live/stage_timeline.html"
    fields = ("variant", "heading")

    def get_render_template(self, context, instance, placeholder):
        if instance.variant == StageTimeline.Variant.HOME:
            return "dj/live/stage_timeline_home.html"
        return self.render_template

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        live = data.fetch("stages", self.request_from(context))
        context.update(
            {"live": live, "rows": data.dicts(live.data.get("rows")), "edition": live.data.get("edition")}
        )
        return context


@plugin_pool.register_plugin
class ProblemsPlugin(LivePluginBase):
    """Zadania bieżącego etapu (po ``opens_at``) i arkusz treningowy (``GET problems``)."""

    model = Problems
    name = "Zadania etapu (z systemu)"
    render_template = "dj/live/problems.html"
    fields = ("closed_notice",)

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        live = data.fetch("problems", self.request_from(context))
        context.update(data.problems_context(live, instance.closed_notice))
        return context


@plugin_pool.register_plugin
class ResultsPlugin(LivePluginBase):
    """Ogłoszone tabele wyników bieżącej edycji i odnośniki do wcześniejszych (``GET results``).

    Wyłącznie zamrożony snapshot publikacji, i to po białej liście kluczy aplikacji głównej
    (reguła 3 z § 7) – wtyczka nie ma pól i nie ma skąd wziąć niczego innego.
    """

    model = CMSPlugin
    name = "Tabele wyników (z systemu)"
    render_template = "dj/live/results.html"

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        context.update(data.results_context(data.fetch("results", self.request_from(context))))
        return context


@plugin_pool.register_plugin
class ArchiveResultsPlugin(LivePluginBase):
    """Odnośniki do ogłoszonych tabel edycji archiwalnej (``GET editions/<id>/results``).

    Edycję wskazuje rozszerzenie strony ``ArchiveMeta`` (DJ-01e) treści, w której stoi wtyczka
    (``placeholder.source``), a nie pole wtyczki – jak ``ArchiveEditionPage.edition`` w Wagtailu.
    """

    model = CMSPlugin
    name = "Wyniki edycji archiwalnej (z systemu)"
    render_template = "dj/live/archive_results.html"

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        source = getattr(placeholder, "source", None) or data.current_content(context)
        context["archive"] = data.archive_results(source, self.request_from(context))
        return context


@plugin_pool.register_plugin
class WorkshopSchedulePlugin(LivePluginBase):
    """Strona „Warsztaty” na żywo (``GET workshops``, DJ-02 D9): tabele harmonogramu albo wprowadzenie.

    Tabela warsztatów w Wagtailu jest źródłem obecności i zaświadczeń w aplikacji głównej, więc
    strona publiczna nie może pokazywać jej kopii z dnia importu. Wprowadzenie (``page.intro``)
    przychodzi z API **bez** sanityzacji – przechodzi przez ``djangocms_text.html.clean_html``
    (``data.rich_text``), zanim trafi do szablonu.
    """

    model = WorkshopSchedule
    name = "Warsztaty (z systemu)"
    render_template = "dj/live/workshop_schedule.html"
    fields = ("part",)

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        live = data.fetch("workshops", self.request_from(context))
        context.update(data.workshops_context(live, instance.part))
        return context


@plugin_pool.register_plugin
class PartnersLivePlugin(LivePluginBase):
    """Strona partnerów na żywo (``GET partners``, DJ-02 D9): wprowadzenie, grupy kart, „Zostań partnerem”.

    Partnerzy zostają redagowani w ``PartnersPage`` Wagtaila (ten sam wpis zasila slider sponsorów
    w ramie), więc djcms ich nie kopiuje. Bez pól: wszystko, co rysuje, jest danymi z API, a tekst
    formatowany (``intro``, ``become_partner_body``) przechodzi przez sanityzator (``data.rich_text``).
    """

    model = CMSPlugin
    name = "Partnerzy (z systemu)"
    render_template = "dj/live/partners.html"

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        context.update(data.partners_context(data.fetch("partners", self.request_from(context))))
        return context
