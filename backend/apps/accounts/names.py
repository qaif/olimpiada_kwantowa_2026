"""Imię i nazwisko wpisywane przez użytkownika: znaki, których w nich nie przyjmujemy.

Jedna reguła (pakiet 5 po audycie): **żadnej kropki środkowej** ani jej sobowtórów. Podpis
wiadomości zespołu organizatora w czacie miał postać „Organizator · Imię N.”, więc uczestnik
o imieniu „Organizator · Anna” wyglądał w rozmowie dokładnie jak organizator. Właściwa naprawa
siedzi w szablonie – rola nadawcy jest osobną odznaką sterowaną ``sender_role`` wiadomości
(``templates/web/chat/_messages.html``) – a ten walidator jest drugą warstwą: separatora, którym
interfejs oddziela rolę od imienia, nie da się wpisać w imię. W polskich (i nie tylko) imionach
i nazwiskach ten znak nie występuje, więc reguła nikomu nie odbiera prawdziwego zapisu.

Walidator jest funkcją Django (``ValidationError``), więc ten sam obiekt wchodzi do pól
formularzy (``apps.web.forms``, ``apps.web.supervisor_forms``) i serializerów DRF
(``apps.accounts.serializers``) – DRF zamienia błąd Django na błąd pola sam.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

#: Kropka środkowa i znaki, które na ekranie wyglądają tak samo (punktor, operator kropki,
#: kropka katakany, kropka środkowa pełnej szerokości).
FORBIDDEN_NAME_CHARACTERS = frozenset("·•∙⋅・･·")

NAME_CHARACTER_MESSAGE = "Imię i nazwisko nie mogą zawierać znaku „·” ani podobnych kropek środkowych."


def validate_person_name(value: str) -> None:
    """Odmowa dla imienia albo nazwiska z kropką środkową (patrz docstring modułu)."""
    if any(char in FORBIDDEN_NAME_CHARACTERS for char in value or ""):
        raise ValidationError(NAME_CHARACTER_MESSAGE, code="name_character")
