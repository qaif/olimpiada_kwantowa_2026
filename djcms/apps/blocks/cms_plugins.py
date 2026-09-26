"""Wtyczki redakcyjne django CMS – odpowiedniki bloków Wagtaila (§ 6.2 docs/tasks/DJ-01.md).

Szablony w ``templates/dj/blocks/`` to **porty** ``backend/templates/cms/blocks/*`` i odpowiednich
fragmentów szablonów stron: te same znaczniki i klasy CSS, bo arkusz (``backend/static/css``) jest
ten sam. Zmieniając szablon po stronie Wagtaila, zmień też port (ryzyko 3 speca).

Gdzie które wtyczki wolno wstawić, mówi ``CMS_PLACEHOLDER_CONF`` (``config/settings/base.py``,
tabela 6.1). Wtyczki-dzieci (pary definicji, wiersze harmonogramu, kroki) dodatkowo wymagają
rodzica (``require_parent``) – nie da się ich postawić luzem w slocie.

``cache = False`` mają tylko wtyczki, których wynik zależy od czegoś poza własnymi polami: sekcja
kroków (przycisk rejestracji z ramy ``chrome``). Reszta trafia do bufora placeholderów.
"""

from __future__ import annotations

import logging

from cms.models import CMSPlugin
from cms.plugin_base import CMSPluginBase
from cms.plugin_pool import plugin_pool

from . import models
from .plugin_sets import DOC_PLUGINS

logger = logging.getLogger(__name__)

MODULE_CONTENT = "Treść"
MODULE_HOME = "Strona główna"
MODULE_PARTNERS = "Partnerzy"
MODULE_FILES = "Pliki do pobrania"
MODULE_FAQ = "Najczęstsze pytania"

#: Szerokość obrazu w treści – odpowiednik renditionu ``width-900`` z ``cms/blocks/image.html``.
IMAGE_SIZE = (900, 0)
#: Znak partnera – odpowiednik ``max-600x240`` z ``partners_page.html``.
PARTNER_LOGO_SIZE = (600, 240)


def thumbnail(image, size: tuple[int, int]) -> dict | None:
    """Miniatura obrazu z filera (``easy-thumbnails``) jako ``src``/``width``/``height``.

    Bez powiększania (jak renditiony Wagtaila). Plik, którego nie da się przetworzyć (skasowany
    z wolumenu, uszkodzony), to brak obrazu, a nie błąd 500 całej strony.
    """
    if image is None:
        return None
    try:
        from easy_thumbnails.files import get_thumbnailer

        thumb = get_thumbnailer(image.file).get_thumbnail({"size": size})
    except Exception:  # noqa: BLE001 - zepsuty plik w bibliotece nie może położyć strony
        logger.warning("Nie udało się przygotować miniatury obrazu filera #%s.", image.pk, exc_info=True)
        return None
    return {"src": thumb.url, "width": thumb.width, "height": thumb.height}


def parent_instance(instance):
    """Rodzic wtyczki-dziecka jako model wtyczki (a nie goły ``CMSPlugin``).

    Przy renderze drzewa django CMS podstawia rodzica już zrzutowanego (``downcast_plugins``), więc
    zwykle nie ma tu żadnego zapytania; zapytanie zostaje na render pojedynczej wtyczki.
    """
    if not instance.parent_id:
        return None
    parent = instance.parent
    if type(parent) is CMSPlugin:
        parent = parent.get_plugin_instance()[0]
    return parent


class BlockPlugin(CMSPluginBase):
    module = MODULE_CONTENT


# --- ART ------------------------------------------------------------------------------------------


@plugin_pool.register_plugin
class ImageWithCaptionPlugin(BlockPlugin):
    model = models.ImageWithCaption
    name = "Obraz z podpisem"
    render_template = "dj/blocks/image.html"

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        image = instance.image if instance.image_id else None
        context["thumb"] = thumbnail(image, IMAGE_SIZE)
        context["alt"] = (getattr(image, "default_alt_text", "") or "") if image is not None else ""
        return context


@plugin_pool.register_plugin
class DocumentLinkPlugin(BlockPlugin):
    model = models.DocumentLink
    name = "Dokument do pobrania"
    render_template = "dj/blocks/document.html"
    fieldsets = (
        (None, {"fields": ("label",)}),
        ("Plik", {"fields": ("file", "url", "title", "extension", "size_bytes")}),
    )


@plugin_pool.register_plugin
class EmbedPlugin(BlockPlugin):
    model = models.Embed
    name = "Film (YouTube, Vimeo)"
    render_template = "dj/blocks/embed.html"


# --- DOC ------------------------------------------------------------------------------------------


@plugin_pool.register_plugin
class HeadingPlugin(BlockPlugin):
    model = models.Heading
    name = "Śródtytuł"
    render_template = "dj/blocks/heading.html"


@plugin_pool.register_plugin
class NoticePlugin(BlockPlugin):
    model = models.Notice
    name = "Ramka"
    render_template = "dj/blocks/notice.html"


@plugin_pool.register_plugin
class DefinitionListPlugin(BlockPlugin):
    model = models.DefinitionList
    name = "Tabela dwukolumnowa (etykieta – wartość)"
    render_template = "dj/blocks/definitions.html"
    allow_children = True
    child_classes = ["DefinitionItemPlugin"]


@plugin_pool.register_plugin
class DefinitionItemPlugin(BlockPlugin):
    model = models.DefinitionItem
    name = "Para etykieta – wartość"
    render_template = "dj/blocks/definition_item.html"
    require_parent = True
    parent_classes = ["DefinitionListPlugin"]

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        # Nagłówki kolumn powtarzane przy każdej parze należą do rodzica – para rysowana osobno
        # (tryb edycji, podwójne kliknięcie) też ma je pokazać.
        parent = parent_instance(instance)
        context["term_label"] = getattr(parent, "term_label", "")
        context["description_label"] = getattr(parent, "description_label", "")
        return context


@plugin_pool.register_plugin
class SchedulePlugin(BlockPlugin):
    model = models.Schedule
    name = "Harmonogram (tabela)"
    render_template = "dj/blocks/schedule.html"
    allow_children = True
    child_classes = ["ScheduleRowPlugin"]

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        context["has_time"] = instance.has_time
        context["has_lecturer"] = instance.has_lecturer
        return context


@plugin_pool.register_plugin
class ScheduleRowPlugin(BlockPlugin):
    model = models.ScheduleRow
    name = "Wiersz harmonogramu"
    render_template = "dj/blocks/schedule_row.html"
    require_parent = True
    parent_classes = ["SchedulePlugin"]

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        # Kolumny godzin i prowadzącego znikają w całym harmonogramie naraz – o tym decyduje
        # rodzic (``Schedule.has_time``), nie pojedynczy wiersz.
        parent = parent_instance(instance)
        context["has_time"] = bool(getattr(parent, "has_time", True))
        context["has_lecturer"] = bool(getattr(parent, "has_lecturer", True))
        return context


# --- strona główna --------------------------------------------------------------------------------


@plugin_pool.register_plugin
class HeroPlugin(BlockPlugin):
    """Tylko ``<h1>`` i ``.hero__text`` – resztę sekcji ``.hero`` (nadtytuł z edycją, przyciski,
    etap bieżący) rysuje szablon strony głównej z danych API (DJ-01f)."""

    model = models.Hero
    name = "Hasło strony głównej"
    module = MODULE_HOME
    render_template = "dj/blocks/hero.html"


@plugin_pool.register_plugin
class StepsSectionPlugin(BlockPlugin):
    model = models.StepsSection
    name = "Sekcja „Jak zacząć”"
    module = MODULE_HOME
    render_template = "dj/blocks/steps_section.html"
    allow_children = True
    child_classes = ["StepPlugin"]
    #: Przycisk rejestracji pod krokami zależy od okna rejestracji (API ``chrome``), nie od pól wtyczki.
    cache = False


@plugin_pool.register_plugin
class StepPlugin(BlockPlugin):
    model = models.Step
    name = "Krok"
    module = MODULE_HOME
    render_template = "dj/blocks/step.html"
    require_parent = True
    parent_classes = ["StepsSectionPlugin"]


@plugin_pool.register_plugin
class AboutSectionPlugin(BlockPlugin):
    model = models.AboutSection
    name = "Sekcja „O Olimpiadzie”"
    module = MODULE_HOME
    render_template = "dj/blocks/about_section.html"
    allow_children = True
    child_classes = DOC_PLUGINS


# --- partnerzy ------------------------------------------------------------------------------------


@plugin_pool.register_plugin
class PartnerPlugin(BlockPlugin):
    model = models.Partner
    name = "Partner"
    module = MODULE_PARTNERS
    render_template = "dj/blocks/partner.html"

    def render(self, context, instance, placeholder):
        context = super().render(context, instance, placeholder)
        context["logo"] = thumbnail(instance.logo if instance.logo_id else None, PARTNER_LOGO_SIZE)
        return context


@plugin_pool.register_plugin
class BecomePartnerPlugin(BlockPlugin):
    model = models.BecomePartner
    name = "Zaproszenie „Zostań partnerem”"
    module = MODULE_PARTNERS
    render_template = "dj/blocks/become_partner.html"


# --- pliki ----------------------------------------------------------------------------------------


@plugin_pool.register_plugin
class AttachmentPlugin(BlockPlugin):
    """Wiersz karty „Do pobrania” – kartę wokół slotu rysuje ``dj/partials/_attachments.html``."""

    model = models.Attachment
    name = "Plik do pobrania (karta „Do pobrania”)"
    module = MODULE_FILES
    render_template = "dj/blocks/attachment.html"
    fieldsets = (
        (None, {"fields": ("label",)}),
        ("Plik", {"fields": ("file", "url", "title", "extension", "size_bytes")}),
    )


@plugin_pool.register_plugin
class ArchiveDocumentPlugin(BlockPlugin):
    """Wiersz listy „Materiały” strony edycji archiwalnej (``<li>`` w ``<ul class="doc-list">``)."""

    model = models.ArchiveDocument
    name = "Materiał edycji (archiwum)"
    module = MODULE_FILES
    render_template = "dj/blocks/archive_document.html"
    fieldsets = (
        (None, {"fields": ("kind", "title")}),
        ("Plik", {"fields": ("file", "url", "extension", "size_bytes")}),
    )


# --- najczęstsze pytania --------------------------------------------------------------------------


@plugin_pool.register_plugin
class FAQEntryPlugin(BlockPlugin):
    model = models.FAQEntry
    name = "Pytanie i odpowiedź"
    module = MODULE_FAQ
    render_template = "dj/blocks/faq_entry.html"
