"""Znaczniki motywu w ``base.html`` i szablonach CMS.

Każdy znacznik przy **braku motywu** oddaje dokładnie to, co stało w szablonie przed motywami:
``theme_head``/``theme_html_attrs``/``theme_preview_banner`` – pusty napis, ``theme_slot`` – treść
domyślnego szablonu ``theme/<slot>.html`` wyrenderowaną w bieżącym kontekście (jak ``{% include %}``),
``theme_default``/``theme_wrap`` – własną zawartość. Stąd test „Konkurs #1 co do bajtu”.

Znaczniki leżą w aplikacji, a nie w liście bibliotek dozwolonych dla motywów: szablon **paczki**
nie może wołać slotów ani wstrzykiwać arkuszy – robi to wyłącznie rama aplikacji.
"""

from __future__ import annotations

from django import template
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from ..rendering import render_theme_template
from ..runtime import active_theme, package_slots_allowed, preview_active
from ..slots import SLOTS

register = template.Library()

#: Atrybut żądania, który mówi warstwie CSP, że strona dołącza arkusze/kroje z bucketu motywu.
CSP_FLAG = "_theme_assets_used"


def _theme(context):
    return active_theme(context.get("request"))


def _slot_theme(context):
    """Motyw, którego szablony slotów obowiązują na tej stronie – tylko strona publiczna."""
    request = context.get("request")
    return active_theme(request) if request is not None and package_slots_allowed(request) else None


def _app_slot(context, slot: str) -> str:
    """Domyślny slot aplikacji, renderowany jak ``{% include %}`` (ten sam kontekst, ten sam silnik)."""
    tpl = context.template.engine.get_template(f"theme/{slot}.html")
    with context.push():
        return tpl.render(context)


@register.simple_tag(takes_context=True)
def theme_slot(context, slot: str):
    if slot not in SLOTS:
        raise template.TemplateSyntaxError(f"Nieznany slot motywu: {slot!r}.")
    theme = _slot_theme(context)
    if theme is not None:
        rendered = render_theme_template(theme, f"theme/{slot}.html", context)
        if rendered is not None:
            return mark_safe(rendered)  # noqa: S308 - wynik renderu szablonu z autoescape
    return mark_safe(_app_slot(context, slot))  # noqa: S308 - j.w.


class ThemeWrapNode(template.Node):
    """``{% theme_wrap "slot" %}…{% endtheme_wrap %}`` – opakowanie treści slotem motywu.

    Bez motywu (albo bez nadpisania slotu) oddaje własną zawartość bez zmian. Z nadpisaniem –
    renderuje zawartość, a wynik podaje szablonowi motywu jako ``slot_content`` (już bezpieczny
    HTML, więc motyw nie potrzebuje ``|safe``).
    """

    child_nodelists = ("nodelist",)

    def __init__(self, slot: str, nodelist):
        self.slot = slot
        self.nodelist = nodelist

    def render(self, context):
        inner = self.nodelist.render(context)
        theme = _slot_theme(context)
        if theme is not None:
            rendered = render_theme_template(
                theme,
                f"theme/{self.slot}.html",
                context,
                {"slot_content": mark_safe(inner)},  # noqa: S308
            )
            if rendered is not None:
                return rendered
        return inner


@register.tag
def theme_wrap(parser, token):
    bits = token.split_contents()
    if len(bits) != 2 or bits[1][:1] not in "'\"":
        raise template.TemplateSyntaxError('Użycie: {% theme_wrap "slot" %}…{% endtheme_wrap %}')
    slot = bits[1][1:-1]
    if slot not in SLOTS:
        raise template.TemplateSyntaxError(f"Nieznany slot motywu: {slot!r}.")
    nodelist = parser.parse(("endtheme_wrap",))
    parser.delete_first_token()
    return ThemeWrapNode(slot, nodelist)


class ThemeDefaultNode(template.Node):
    """Zawartość wyłącznie **bez** motywu (np. ``<meta name="theme-color">`` aplikacji)."""

    child_nodelists = ("nodelist",)

    def __init__(self, nodelist):
        self.nodelist = nodelist

    def render(self, context):
        return "" if _theme(context) is not None else self.nodelist.render(context)


@register.tag
def theme_default(parser, token):
    nodelist = parser.parse(("endtheme_default",))
    parser.delete_first_token()
    return ThemeDefaultNode(nodelist)


@register.simple_tag(takes_context=True)
def theme_head(context):
    """``<link>`` arkuszy motywu (po ``app.css``) i kolor paska przeglądarki. Bez stylu inline."""
    theme = _theme(context)
    if theme is None:
        return ""
    request = context.get("request")
    if request is not None:
        setattr(request, CSP_FLAG, True)
    rt = theme.runtime
    links = [rt.tokens_url, rt.css_url]
    parts = [format_html_join("", '\n  <link rel="stylesheet" href="{}">', ((url,) for url in links))]
    if theme.brand_accent:
        from django.urls import reverse

        from ..tokens import HEX

        if HEX.match(theme.brand_accent):
            href = reverse("web:theme-overrides") + f"?v={rt.pk}-{theme.brand_accent.lstrip('#').lower()}"
            parts.append(format_html('\n  <link rel="stylesheet" href="{}">', href))
    if rt.meta_color:
        parts.append(format_html('\n  <meta name="theme-color" content="{}">', rt.meta_color))
    return mark_safe("".join(str(p) for p in parts))  # noqa: S308 - części z format_html


@register.simple_tag(takes_context=True)
def theme_html_attrs(context):
    """Atrybuty ``<html>``: motyw, schemat kolorów i warianty układów (selektory ``theme.css``)."""
    theme = _theme(context)
    if theme is None:
        return ""
    attrs = [("data-theme", theme.runtime.slug), ("data-color-scheme", theme.runtime.color_scheme)]
    attrs += [(f"data-layout-{key.replace('_', '-')}", value) for key, value in sorted(theme.layouts.items())]
    return format_html_join("", ' {}="{}"', attrs)


@register.simple_tag(takes_context=True)
def theme_preview_banner(context):
    """Pasek „to jest podgląd” – wyłącznie w podglądzie koordynatora."""
    request = context.get("request")
    if request is None or not preview_active(request):
        return ""
    theme = _theme(context)
    from django.urls import reverse

    label = f"{theme.runtime.name} {theme.runtime.version}" if theme is not None else "Klasyczny (wbudowany)"
    return format_html(
        '\n<div class="theme-preview-bar" role="status">Podgląd motywu <strong>{}</strong> – '
        "widzisz go tylko Ty. "
        '<a href="{}">Wróć do wyboru motywu</a> · <a href="{}">Zakończ podgląd</a></div>',
        label,
        reverse("web:coordinator-theme"),
        request.path,
    )
