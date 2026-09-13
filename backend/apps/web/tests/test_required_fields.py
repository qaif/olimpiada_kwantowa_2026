"""Oznaczenie pól obowiązkowych w formularzach rejestracji i edycji profilu.

Zgłoszenie organizatora: z formularza nie dało się odczytać, co trzeba wypełnić, a co można
pominąć. Django umie to powiedzieć samo – ``required_css_class`` dokleja klasę do etykiety
każdego pola wymaganego, a gwiazdkę dorysowuje arkusz (``label.required::after``). Testujemy
**klasę w HTML-u**, a nie gwiazdkę: gwiazdki w znaczniku nie ma i być nie ma.
"""

import re

import pytest

from apps.web.forms import (
    CommitteeRegisterForm,
    ParticipantProfileForm,
    ParticipantRegisterForm,
    SocialParticipantSignupForm,
)

pytestmark = pytest.mark.django_db

REGISTER_URL = "/register/"
COMMITTEE_URL = "/register/committee/"
LEGEND = "Pola oznaczone * są obowiązkowe."


def label_for(markup: str, field_id: str) -> str:
    """Znacznik ``<label>`` wskazujący na pole o tym ``id``. Kolejność atrybutów nieistotna."""
    match = re.search(rf"<label[^>]*\bfor=\"{re.escape(field_id)}\"[^>]*>", markup)
    assert match, f"brak etykiety dla {field_id}"
    return match.group(0)


@pytest.mark.parametrize(
    "form_class",
    [ParticipantRegisterForm, SocialParticipantSignupForm, CommitteeRegisterForm, ParticipantProfileForm],
)
def test_every_registration_form_marks_required_fields(form_class):
    assert form_class.required_css_class == "required"


def test_required_label_carries_the_class_and_an_optional_one_does_not():
    """E-mail jest wymagany, zgoda na publikację nazwiska – nie. Marker ma je rozróżniać."""
    form = ParticipantRegisterForm()

    assert "required" in label_for(str(form["email"].label_tag()), "id_email")
    # Zgody są w Django ``required=False`` (rozstrzyga je serwis), a wymagane oznacza osobny
    # napis „wymagane” przy oświadczeniu – gwiazdka przy nich byłaby drugim, innym markerem.
    assert "required" not in str(form["publish_name_consent"].label_tag())


def test_the_school_query_label_is_marked_by_hand():
    """``school_query`` jest ``required=False``, a szkoła obowiązkowa – stąd klasa wpisana w szablonie."""
    assert ParticipantRegisterForm().fields["school_query"].required is False


def test_registration_page_shows_the_legend_and_the_marked_labels(web_client, edition):
    body = web_client.get(REGISTER_URL).content.decode()

    assert LEGEND in body
    assert "required" in label_for(body, "id_email")
    # Etykieta bloku „szkoła” jest budowana ręcznie (web/_school_picker.html), więc marker
    # trzeba było dołożyć osobno – bez tego jedyne obowiązkowe pole bez gwiazdki.
    assert "required" in label_for(body, "id_school_query")
    # Wolny tekst wymagany nie jest (szkoła może przyjść ze słownika) i gwiazdki mieć nie ma.
    assert "required" not in label_for(body, "id_school")


def test_committee_registration_page_shows_the_legend(web_client, edition):
    body = web_client.get(COMMITTEE_URL).content.decode()

    assert LEGEND in body
    assert "required" in label_for(body, "id_email")
