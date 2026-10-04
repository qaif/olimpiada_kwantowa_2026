"""``export_translations`` – nakładki z bazy do ``.po`` i z powrotem (L10N-01 § 7)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.translation_review import catalogs
from apps.translation_review.models import TranslationOverride

from .conftest import simple_row

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog_copy(tmp_path, monkeypatch):
    """Kopia hiszpańskiego katalogu projektu – komenda pisze do niej, a nie do repozytorium."""
    source = Path(settings.BASE_DIR) / "locale" / "es" / "LC_MESSAGES" / "django.po"
    target = tmp_path / "es" / "LC_MESSAGES" / "django.po"
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    original = catalogs.catalog_paths
    monkeypatch.setattr(
        catalogs, "catalog_paths", lambda language: [target] if language == "es" else original(language)
    )
    catalogs.forget()
    yield target
    catalogs.forget()


def make_override(row, text, kind="change", base_text=None):
    return TranslationOverride.objects.create(
        kind=kind,
        base_text=row.translation if base_text is None else base_text,
        language="es",
        key=row.key,
        msgctxt=row.msgctxt or "",
        msgid=row.msgid,
        plural_index=row.plural_index,
        text=text,
    )


def test_export_writes_po_and_roundtrips(catalog_copy, tmp_path):
    row = simple_row()
    before = catalog_copy.read_text(encoding="utf-8")
    make_override(row, "Traducción revisada")
    call_command("export_translations", "--language", "es", "--force", stdout=io.StringIO())
    after = catalog_copy.read_text(encoding="utf-8")
    entry = next(
        entry for entry in catalogs.parse(after) if entry.msgid == row.msgid and entry.msgctxt is None
    )
    assert entry.msgstr[0] == "Traducción revisada"
    assert entry.reviewed
    # Diff to dokładnie: podmieniona linia ``msgstr`` i dopisany znacznik przeglądu.
    removed = set(before.splitlines()) - set(after.splitlines())
    added = set(after.splitlines()) - set(before.splitlines())
    assert removed
    assert all(line.startswith(("msgstr", '"')) for line in removed)
    assert added <= {'msgstr "Traducción revisada"', f"# {catalogs.REVIEWED_MARKER}"}
    msgfmt = shutil.which("msgfmt")
    if msgfmt:
        result = subprocess.run(  # noqa: S603 - ścieżka z which
            [msgfmt, "--check", "-o", str(tmp_path / "x.mo"), str(catalog_copy)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    # ``--prune`` przed kompilacją: ``.po`` ma już tekst, ale ``.mo`` (to, co widzi gettext) – nie.
    other = next(item for item in catalogs.index("es").rows if item.key != row.key and item.translation)
    make_override(other, "Otra cosa")
    call_command("export_translations", "--prune", "--language", "es", stdout=io.StringIO())
    assert TranslationOverride.objects.count() == 2
    if not msgfmt:
        pytest.skip("Brak msgfmt – drugiej połowy (po kompilacji) nie da się sprawdzić.")
    subprocess.run([msgfmt, "-o", str(catalog_copy.with_suffix(".mo")), str(catalog_copy)], check=True)  # noqa: S603
    # Po „wdrożeniu” (skompilowany katalog oddaje ten tekst) ``--prune`` sprząta nakładkę, inne zostawia.
    call_command("export_translations", "--prune", "--language", "es", stdout=io.StringIO())
    assert list(TranslationOverride.objects.values_list("key", flat=True)) == [other.key]


def test_dry_run_changes_nothing(catalog_copy):
    before = catalog_copy.read_bytes()
    make_override(simple_row(), "Algo")
    out = io.StringIO()
    call_command("export_translations", "--dry-run", "--language", "es", stdout=out)
    assert catalog_copy.read_bytes() == before
    assert "[próba] es: wpisy 1" in out.getvalue()


def test_json_roundtrip_validates_again(catalog_copy, tmp_path):
    row = simple_row()
    make_override(row, "Desde JSON")
    dump = tmp_path / "overrides.json"
    call_command("export_translations", "--to-json", str(dump), stdout=io.StringIO())
    payload = json.loads(dump.read_text(encoding="utf-8"))
    assert payload["overrides"][0]["text"] == "Desde JSON"
    # Bez danych osób: kto zatwierdził i kiedy – nie wyjeżdża z produkcji.
    assert set(payload["overrides"][0]) == {
        "language",
        "msgctxt",
        "msgid",
        "plural_index",
        "kind",
        "text",
        "base_text",
    }
    # Plik przyjechał spoza repozytorium: wpis z HTML-em nie przechodzi walidacji i nie trafia do .po.
    payload["overrides"].append({**payload["overrides"][0], "msgid": row.msgid, "text": "<script>x</script>"})
    payload["overrides"].append({**payload["overrides"][0], "msgid": "Nie ma takiego napisu", "text": "x"})
    dump.write_text(json.dumps(payload), encoding="utf-8")
    TranslationOverride.objects.all().delete()
    out = io.StringIO()
    call_command("export_translations", "--from-json", str(dump), "--force", stdout=out)
    content = catalog_copy.read_text(encoding="utf-8")
    assert "Desde JSON" in content
    assert "<script>" not in content
    assert "odrzucony" in out.getvalue()
    assert "nieaktualny" in out.getvalue()


# --- poprawki po przeglądzie (H1, M3, L5) -------------------------------------------------------


def test_write_mode_refuses_outside_a_checkout(catalog_copy, settings):
    """M3: bez DEBUG i bez ``.git`` obok ``backend`` zapis do .po jest odmawiany (chyba że --force)."""
    from django.core.management.base import CommandError

    settings.DEBUG = False
    make_override(simple_row(), "Algo")
    before = catalog_copy.read_bytes()
    with pytest.raises(CommandError):
        call_command("export_translations", "--language", "es", stdout=io.StringIO())
    assert catalog_copy.read_bytes() == before
    # Próba i zrzut do JSON-a nie piszą do .po – działają wszędzie.
    call_command("export_translations", "--dry-run", "--language", "es", stdout=io.StringIO())


def test_confirmation_exports_only_the_marker(catalog_copy):
    """H1: potwierdzenie dopisuje ``# l10n-reviewed`` i nie rusza ``msgstr``."""
    row = simple_row()
    make_override(row, row.translation, kind="confirmation")
    before = catalog_copy.read_text(encoding="utf-8")
    call_command("export_translations", "--language", "es", "--force", stdout=io.StringIO())
    after = catalog_copy.read_text(encoding="utf-8")
    assert set(after.splitlines()) - set(before.splitlines()) == {f"# {catalogs.REVIEWED_MARKER}"}
    assert not set(before.splitlines()) - set(after.splitlines())
    entry = next(
        entry for entry in catalogs.parse(after) if entry.msgid == row.msgid and entry.msgctxt is None
    )
    assert entry.reviewed and entry.msgstr[0] == row.translation
    # Po wdrożeniu (znacznik w katalogu) ``--prune`` usuwa potwierdzenie z bazy.
    call_command("export_translations", "--prune", "--language", "es", stdout=io.StringIO())
    assert not TranslationOverride.objects.exists()


def test_change_decided_on_another_catalog_text_is_a_conflict(catalog_copy):
    """H1: poprawka podjęta wobec innego ``msgstr`` niż dzisiejszy nie nadpisuje nowszego tekstu."""
    row = simple_row()
    make_override(row, "Decisión vieja", base_text="texto anterior del catálogo")
    before = catalog_copy.read_bytes()
    out = io.StringIO()
    call_command("export_translations", "--language", "es", "--force", stdout=out)
    assert catalog_copy.read_bytes() == before
    assert "konflikt" in out.getvalue()


def test_prune_reports_stale_and_deletes_only_on_request():
    """L5: nakładka napisu, którego nie ma w katalogach – raport; usunięcie dopiero z --prune-stale."""
    TranslationOverride.objects.create(language="es", key="c" * 64, msgid="Napis usunięty z kodu", text="x")
    out = io.StringIO()
    call_command("export_translations", "--prune", "--language", "es", stdout=out)
    assert TranslationOverride.objects.exists()
    assert "Napis usunięty z kodu" in out.getvalue()
    call_command("export_translations", "--prune-stale", "--language", "es", stdout=io.StringIO())
    assert not TranslationOverride.objects.exists()
