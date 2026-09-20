"""Komunikat o oficjalnym starcie rejestracji (``cms.SiteSettings.registration_note``).

Po co osobny tekst, skoro serwis i tak wie, czy rejestracja jest otwarta: **to dwie różne rzeczy**.
O przyjmowaniu zgłoszeń rozstrzyga okno rejestracji edycji (panel koordynatora), a organizator
trzyma formularz otwarty do testów **zanim** ogłoszona data nadejdzie. Bez tej linii strona
mówiłaby „rejestracja jest otwarta” w dniu, w którym oficjalnie jeszcze się nie zaczęła.

Komunikat pokazuje się w dwóch miejscach – na stronie głównej pod przyciskami i nad formularzem
rejestracji – i w żadnym nie wpływa na to, czy formularz da się wysłać.
"""

import pytest
from wagtail.models import Site

from apps.cms.models import SiteSettings

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
NOTE = "Zakończenie rejestracji: 28.02.2027"


def set_note(text: str) -> None:
    settings_row = SiteSettings.for_site(Site.objects.get(is_default_site=True))
    settings_row.registration_note = text
    settings_row.save()


def test_default_note_comes_from_the_migration():
    """Produkcja ma pokazać tekst zaraz po wdrożeniu, bez wchodzenia redaktora do /cms/."""
    assert SiteSettings.for_site(Site.objects.get(is_default_site=True)).registration_note == NOTE


def test_registration_page_shows_the_note(web_client, edition):
    set_note(NOTE)

    assert NOTE in web_client.get(REGISTER_URL).content.decode()


def test_home_page_shows_the_note(web_client, edition):
    set_note(NOTE)

    assert NOTE in web_client.get("/").content.decode()


def test_a_blank_note_shows_nothing(web_client, edition):
    """Puste pole = brak linii, a nie pusty akapit pod przyciskami."""
    set_note("")

    assert "hero__note" not in web_client.get("/").content.decode()
    assert NOTE not in web_client.get(REGISTER_URL).content.decode()


def test_the_note_does_not_decide_whether_registration_is_open(web_client, edition):
    """Tekst jest informacją redakcyjną – formularz zostaje otwarty tak samo z nim, jak i bez niego."""
    set_note(NOTE)
    with_note = web_client.get(REGISTER_URL).content.decode()
    set_note("")
    without_note = web_client.get(REGISTER_URL).content.decode()

    for body in (with_note, without_note):
        # Formularz jest jedynym świadectwem otwartej rejestracji – zdanie o tym, że jest otwarta,
        # zdjął organizator 15.09 (patrz ``templates/web/register.html``).
        assert 'name="password"' in body
