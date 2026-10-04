"""Statystyki szkół i opiekunów szkolnych (zadanie STAT-01, ``docs/tasks/STAT-01.md``).

Osobna aplikacja, a nie moduł w ``apps.results`` albo ``apps.accounts``, bo funkcja **niczego
nie przechowuje**: nie ma modeli ani migracji, tylko liczy agregaty z danych, które już są
(wpisy do etapów, zgłoszenia, zamrożone sumy publikacji, szkoła z profilu uczestnika). Z obiema
aplikacjami łączy ją wyłącznie odczyt.
"""
