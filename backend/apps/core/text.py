"""Wspólna postać porównawcza napisów: bez diakrytyków, małymi literami.

Moduł jest **czysto tekstowy** (sam ``unicodedata``), bez importu Django – dzięki temu może go
wołać zarówno ``apps.accounts.models`` (mapa województw budowana na poziomie modułu), jak i
``apps.schools`` (kolumna ``search_text``) oraz skrypt generujący słownik szkół spoza projektu
Django. Gdyby każde z tych miejsc miało własną kopię składania znaków, „Łódź” wpisana w
wyszukiwarce szkół przestałaby trafiać w to samo, co „lodz” zapisane w bazie – a jest to jedyna
rzecz, którą ta funkcja ma gwarantować.
"""

import unicodedata

# „ł” jest osobną literą Unicode (l ze skreśleniem), a nie „l” z dokładanym znakiem, więc NFKD go
# nie rozkłada i „łódzkie” zostałoby bez tego przepisania nierozpoznane.
_STROKED_L = str.maketrans({"ł": "l", "Ł": "L"})


def fold(text: str) -> str:
    """Napis bez diakrytyków, małymi literami – wspólna postać porównawcza."""
    decomposed = unicodedata.normalize("NFKD", text.translate(_STROKED_L))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()
