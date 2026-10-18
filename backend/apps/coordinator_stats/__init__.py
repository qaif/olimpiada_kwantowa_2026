"""Lejek uczestnika i raporty wysyłek komunikatów (zadanie STATS-02, ``docs/tasks/STATS-02.md``).

Osobna aplikacja, bo obie funkcje są **odczytem** cudzych danych (konta, profile, wpisy do etapów,
komunikaty, odbicia) i obie potrzebują tej samej tablicy grup domen (``domains``). Własne tabele są
dwie i obie dotyczą wyłącznie wysyłek: liczniki komunikatu po grupach domen i odbicia przypisane
komunikatowi. Lejek niczego nie przechowuje – liczy agregaty z tego, co już jest.

Z aplikacjami, które czyta, łączy ją możliwie mało: ``accounts.messaging`` woła dwie funkcje przy
porcji wysyłki (nagłówek listu, liczniki), a ``email_delivery`` wysyła sygnał o odbiciu – nie wie,
kto go słucha.
"""
