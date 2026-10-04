"""Piaskownica wykonująca kod notatników uczniów – **bez Django** (QC-01 § 6).

Warstwy, od najmocniejszej:

1. **kontener** ``notebook-runner`` (docker-compose.yml): ``network_mode: none``, system plików
   tylko do odczytu, ``/tmp`` jako tmpfs z ``noexec``, żadnych sekretów w środowisku, ``cap_drop:
   ALL`` z trzema wyjątkami (SETUID/SETGID/KILL – żeby proces-nadzorca mógł uruchamiać dzieci jako
   osobny, nieuprzywilejowany użytkownik i je zabijać), ``no-new-privileges``, limity pamięci i PID,
2. **proces dziecka** (``child.py``) jako użytkownik ``nobody``-podobny (losowy UID z puli, inny
   dla każdego zadania; po zadaniu nadzorca zabija procesy tego UID i kasuje jego pliki), bez
   grup dodatkowych, z limitami ``setrlimit`` (CPU, pamięć, rozmiar pliku, deskryptory, procesy)
   i limitem czasu ściennego pilnowanym przez rodzica (``killpg``),
3. **hak audytowy** (``sys.addaudithook``) w dziecku: odmawia gniazd, podprocesów, ``fork``,
   ``ctypes``, wątków, odczytu plików spoza bibliotek i zapisu poza katalogiem roboczym. To jest
   obrona w głąb, a nie granica bezpieczeństwa – granicą jest kontener (1).

Komunikacja z workerem Celery idzie przez wspólny katalog (``spool``), nie przez sieć: kontener
piaskownicy nie ma dostępu ani do Redisa, ani do bazy, więc kod ucznia nie ma czego zaatakować.
"""
