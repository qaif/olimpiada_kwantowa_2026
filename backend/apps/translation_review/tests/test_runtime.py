"""Nakładka zatwierdzonych poprawek w gettext i jej unieważnianie (L10N-01 § 6)."""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.template import Context, Template
from django.utils import translation
from django.utils.translation import trans_real

from apps.translation_review import catalogs, runtime, services
from apps.translation_review.models import TranslationOverride
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
        },
    )
    runtime.publish(language)


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
    TranslationOverride.objects.create(
        language="es", key="a" * 64, msgctxt="ctx", msgid="Zapisz", text="Guardar!"
    )
    form = catalogs.plural_function("es")(5)
    TranslationOverride.objects.create(
        language="es", key="b" * 64, msgid="%(n)s punkt", plural_index=form, text="%(n)s puntitos"
    )
    runtime.publish("es")
    with translation.override("es"):
        assert translation.pgettext("ctx", "Zapisz") == "Guardar!"
        assert translation.ngettext("%(n)s punkt", "%(n)s punktów", 5) == "%(n)s puntitos"


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


def test_other_processes_see_the_new_version_after_the_check_interval(overrides_on, settings):
    """Proces, który zmiany nie robił, sprawdza wersję w cache'u co ``CHECK_SECONDS``."""
    settings.TRANSLATION_OVERRIDES_CHECK_SECONDS = 0
    row = simple_row()
    override(row, "Primera")
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Primera"
    # „Inny proces” zmienia nakładkę: nowa treść i wersja wprost w cache'u, pamięć tego procesu stara.
    TranslationOverride.objects.filter(key=row.key).update(text="Segunda")
    memo = runtime._memo["es"]
    runtime.publish("es")
    runtime._memo["es"] = memo
    with translation.override("es"):
        assert translation.gettext(row.msgid) == "Segunda"


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


def test_override_identical_to_catalog_stays_out_of_gettext(overrides_on, reviewer):
    """Potwierdzenie nie może przykryć przyszłej zmiany ``msgstr`` w repozytorium."""
    row = simple_row()
    services.confirm(user=reviewer, language="es", key=row.key)
    runtime.publish("es")
    assert runtime.gettext_key(None, row.msgid, None) not in runtime._memo["es"].entries
