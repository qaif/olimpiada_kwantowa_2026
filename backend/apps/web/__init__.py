"""Interfejs WWW (Django templates + HTMX + Alpine.js).

Warstwa prezentacji. Nie ma tu logiki domenowej: każdy widok woła serwis z aplikacji dziedzinowej
(``apps.accounts``, ``apps.competitions``, ``apps.submissions``, ``apps.grading``, ``apps.appeals``,
``apps.results``) i renderuje wynik. Reguły dostępu też pochodzą stąd – mixiny ról w ``mixins.py``
opierają się na tych samych helperach, co klasy uprawnień DRF.
"""
