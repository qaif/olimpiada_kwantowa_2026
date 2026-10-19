"""Edytor LaTeX (webTEX) do pisania rozwiązań – strona platformy (``docs/tasks/TEX-01.md``).

Platforma wystawia jednorazowy token SSO (nowa karta, ``{WEBTEX_URL}/sso#token=…``), a przy
złożeniu sama pobiera świeżo skompilowany PDF z webTEX (serwer–serwer) i oddaje go zwykłej ścieżce
uploadu (``apps.submissions.services.create_submission``). Edytorowi nie ufamy w niczym, co dotyczy
zawodów – bramki sprawdza Django przy otwarciu i przy złożeniu.
"""
