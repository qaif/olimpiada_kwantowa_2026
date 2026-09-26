"""``bootstrap_djcms_admin`` i ``setup_djcms_groups`` – idempotentne, wołane przez deploy (§ 8.10)."""

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import CommandError, call_command

from apps.pages.management.commands.setup_djcms_groups import EXCLUDED_MODELS, GROUP_NAME

EMAIL = "Admin@Example.com"
PASSWORD = "bardzo-dlugie-haslo-admina-2026"


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("DJCMS_ADMIN_EMAIL", EMAIL)
    monkeypatch.setenv("DJCMS_ADMIN_PASSWORD", PASSWORD)


@pytest.mark.django_db
def test_bootstrap_creates_superuser(admin_env, django_user_model):
    call_command("bootstrap_djcms_admin")
    user = django_user_model.objects.get(username="admin@example.com")
    assert user.is_superuser and user.is_staff and user.is_active
    assert user.email == "admin@example.com"
    assert user.check_password(PASSWORD)


@pytest.mark.django_db
def test_bootstrap_is_idempotent_and_keeps_password(admin_env, monkeypatch, django_user_model):
    call_command("bootstrap_djcms_admin")
    user = django_user_model.objects.get(username="admin@example.com")
    user.set_password("haslo-zmienione-w-panelu-123")
    user.is_staff = False
    user.save()
    monkeypatch.setenv("DJCMS_ADMIN_PASSWORD", "inne-haslo-z-deployu-456789")
    call_command("bootstrap_djcms_admin")
    user.refresh_from_db()
    assert django_user_model.objects.count() == 1
    assert user.is_staff  # uprawnienia uzupełnione
    assert user.check_password("haslo-zmienione-w-panelu-123")  # hasło nietknięte


@pytest.mark.django_db
def test_bootstrap_reset_password(admin_env, monkeypatch, django_user_model):
    call_command("bootstrap_djcms_admin")
    monkeypatch.setenv("DJCMS_ADMIN_PASSWORD", "nowe-haslo-po-resecie-2026")
    call_command("bootstrap_djcms_admin", "--reset-password")
    assert django_user_model.objects.get().check_password("nowe-haslo-po-resecie-2026")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("email", "password"),
    [("", PASSWORD), ("admin@example.com", ""), ("to-nie-adres", PASSWORD), ("admin@example.com", "krotkie")],
)
def test_bootstrap_rejects_bad_input(monkeypatch, django_user_model, email, password):
    monkeypatch.setenv("DJCMS_ADMIN_EMAIL", email)
    monkeypatch.setenv("DJCMS_ADMIN_PASSWORD", password)
    with pytest.raises(CommandError):
        call_command("bootstrap_djcms_admin")
    assert not django_user_model.objects.exists()


@pytest.mark.django_db
def test_setup_groups_creates_editor_group_without_access_management():
    call_command("setup_djcms_groups")
    group = Group.objects.get(name=GROUP_NAME)
    codenames = set(group.permissions.values_list("content_type__app_label", "codename"))
    # Treść: strony, ich zawartość, wtyczki, tekst, pliki, wersje.
    for expected in [
        ("cms", "change_page"),
        ("cms", "add_page"),
        ("cms", "add_cmsplugin"),
        ("cms", "use_structure"),
        ("cms", "publish_page"),
        ("djangocms_text", "change_text"),
        ("filer", "add_image"),
        ("filer", "change_folder"),
        ("djangocms_versioning", "change_version"),
        ("djangocms_versioning", "change_pagecontentversion"),
    ]:
        assert expected in codenames, expected
    # Zarządzanie dostępem – poza grupą.
    models = set(group.permissions.values_list("content_type__app_label", "content_type__model"))
    assert models.isdisjoint(EXCLUDED_MODELS)
    assert not group.permissions.filter(content_type__app_label="auth").exists()


@pytest.mark.django_db
def test_setup_groups_is_idempotent_and_resets_manual_changes():
    call_command("setup_djcms_groups")
    group = Group.objects.get(name=GROUP_NAME)
    count = group.permissions.count()
    group.permissions.add(Permission.objects.get(content_type__app_label="auth", codename="add_user"))
    call_command("setup_djcms_groups")
    assert Group.objects.filter(name=GROUP_NAME).count() == 1
    assert group.permissions.count() == count
