"""Nakładka zatwierdzonych poprawek w gettext i jej unieważnianie (L10N-01 § 6)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from django.core.cache import cache
from django.template import Context, Template
from django.utils import translation
from django.utils.translation import trans_real

from apps.translation_review import catalogs, runtime, services
from apps.translation_review.models import OverrideKind, TranslationOverride
from apps.translation_review.validation import placeholders

from .conftest import placeholder_row, simple_row

pytestmark = pytest.mark.django_db


def override(row, text, language="es"):
    TranslationOverride.objects.update_or_create(
        language=language,
        key=row.key,
        defaults={
            "msgctxt": row.msgctxt or "",
            "msgid": row.msgid,
            "plural_index": row.plural_index,
            "text": text,
            "base_text": row.translation,
        },
    )
    runtime.publish(language)


def simulate_release(monkeypatch, row, new_text, language="es"):
    """Wydanie zmienia ``msgstr`` napisu: nowy tekst w indeksie ``.po`` **i** w katalogu gettext."""
    original = catalogs.index

    def index(lang):
        built = original(lang)
        if lang != language:
            return built
        changed = replace(built.by_key[row.key], translation=new_text)
        return catalogs.Index(
            rows=[changed if item.key == row.key else item for item in built.rows],
            by_key={**built.by_key, row.key: changed},
        )

    monkeypatch.setattr(catalogs, "index", index)
    with translation.override(language):
        catalog = trans_real.translation(language)._catalog
    layer = getattr(trans_real.translation(language), runtime.LAYER_ATTR, None)
    for item in catalog._catalogs:
        if (layer is None or item is not layer[1]) and row.msgid in item:
            monkeypatch.setitem(item, row.msgid, new_text)
            break


def test_override_wins_over_catalog_in_gettext_and_templates(overrides_on):
    row = simple_row()
    override(row, "Texto corregido")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Texto corregido"
        rendered = Template('{% load i18n %}{% translate "' + row.msgid + '" %}').render(Context())
        assert rendered == "Texto corregido"
    with translation.override("en"):
        assert translation.gettext(row.msgid) != "Texto corregido"
    with translation.override("pl"):
        assert translation.gettext(row.msgid) == row.msgid


def test_override_with_placeholder_formats(overrides_on):
    row = placeholder_row()
    text = "PRUEBA " + " ".join(placeholders(row.msgid))
    override(row, text)
    with translation.override("es"):
        assert translation.gettext(row.msgid) == text


def test_context_and_plural_keys(overrides_on):
    """``pgettext`` skleja kontekst separatorem, ``ngettext`` szuka ``(msgid, forma)``."""
    rows = catalogs.index("es").rows
    context_row = next(row for row in rows if row.msgctxt and row.translation and "%" not in row.msgid)
    form = catalogs.plural_function("es")(5)
    plural_row = next(row for row in rows if row.msgid_plural and row.plural_index == form)
    override(context_row, "¡Guardar!")
    override(plural_row, plural_row.translation + " ¡plural!")
    with translation.override("es"):
        assert translation.pgettext(context_row.msgctxt, context_row.msgid) == "¡Guardar!"
        assert translation.ngettext(plural_row.msgid, plural_row.msgid_plural, 5).endswith(" ¡plural!")


def test_approval_and_revert_invalidate_immediately(
    overrides_on, reviewer, django_capture_on_commit_callbacks
):
    """Przebudowa nakładki idzie w ``on_commit`` – test wykonuje te wywołania tak, jak zatwierdzenie."""
    row = simple_row()
    with translation.override("es"):
        before = translation.gettext(row.msgid)
    with django_capture_on_commit_callbacks(execute=True):
        suggestion = services.suggest(user=reviewer, language="es", key=row.key, text="Nuevo texto")
        services.approve(user=reviewer, suggestion=suggestion)
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Nuevo texto"
    with django_capture_on_commit_callbacks(execute=True):
        services.revert(user=reviewer, language="es", key=row.key)
    with translation.override("es"):
        assert translation.gettext(row.msgid) == before


def test_publish_only_bumps_the_version_and_each_process_rebuilds(overrides_on, settings):
    """W cache'u jest sama wersja; proces po zmianie wersji buduje nakładkę z bazy sam."""
    settings.TRANSLATION_OVERRIDES_CHECK_SECONDS = 0
    row = simple_row()
    override(row, "Primera")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Primera"
    assert cache.get(runtime.VERSION_KEY.format(language="es"))
    # „Inny proces” zmienia nakładkę: nowa treść w bazie i nowa wersja w cache'u, pamięć tego procesu
    # zostaje stara – a mimo to po sprawdzeniu wersji widzi nowy tekst.
    TranslationOverride.objects.filter(key=row.key).update(text="Segunda")
    memo = runtime._memo["es"]
    runtime.publish("es")
    runtime._memo["es"] = memo
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Segunda"


def test_publish_purges_the_anonymous_page_cache(overrides_on):
    from apps.web import page_cache

    before = page_cache._version(f"{page_cache.VERSION_PREFIX}:global")
    runtime.publish("es")
    assert page_cache._version(f"{page_cache.VERSION_PREFIX}:global") != before


def test_lost_cache_is_rebuilt_from_database(overrides_on):
    row = simple_row()
    override(row, "Desde la base")
    cache.clear()
    runtime.forget()
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Desde la base"


def test_disabled_flag_strips_the_layer(overrides_on, settings):
    row = simple_row()
    override(row, "Capa")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Capa"
    settings.TRANSLATION_OVERRIDES_ENABLED = False
    with translation.override("es"):
        assert translation.gettext(row.msgid) == row.translation
    catalog = trans_real.translation("es")._catalog
    assert len(catalog._catalogs) == len(catalog._plurals)


def test_confirmation_never_enters_gettext(overrides_on, reviewer):
    row = simple_row()
    services.confirm(user=reviewer, language="es", key=row.key)
    assert TranslationOverride.objects.get(key=row.key).kind == OverrideKind.CONFIRMATION
    runtime.publish("es")
    with translation.override("es"):
        translation.gettext(row.msgid)
    assert runtime.gettext_key(None, row.msgid, None) not in runtime._memo["es"].entries


def test_confirmation_then_catalog_change_shows_new_catalog_text(overrides_on, reviewer, monkeypatch):
    """Potwierdzenie → wydanie zmienia ``msgstr`` → serwis pokazuje **nowy** tekst katalogu."""
    row = simple_row()
    services.confirm(user=reviewer, language="es", key=row.key)
    runtime.publish("es")
    simulate_release(monkeypatch, row, "Texto nuevo del catálogo")
    runtime.publish("es")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Texto nuevo del catálogo"
    view = services.string_view("es", row.key)
    assert view.stale and view.current == "Texto nuevo del catálogo"


def test_change_decided_on_old_catalog_text_steps_aside(overrides_on, reviewer, monkeypatch):
    """Poprawka zatwierdzona wobec starego ``msgstr`` nie przykrywa nowszego tekstu z wydania."""
    row = simple_row()
    suggestion = services.suggest(user=reviewer, language="es", key=row.key, text="Decisión vieja")
    services.approve(user=reviewer, suggestion=suggestion)
    runtime.publish("es")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Decisión vieja"
    simulate_release(monkeypatch, row, "Texto del nuevo lanzamiento")
    runtime.publish("es")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Texto del nuevo lanzamiento"
    assert services.string_view("es", row.key).stale
