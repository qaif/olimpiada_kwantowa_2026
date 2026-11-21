"""Mapa uczestników (zadanie MAP-01, ``docs/tasks/MAP-01.md``): publiczna strona w sekcji „Dokumenty”.

``/dokumenty/mapa-uczestnikow/`` pokazuje, z jakich miejscowości pochodzą uczestnicy bieżącej
edycji: kafle podsumowania, mapę Polski z kółkami miejscowości (pole kółka ∝ liczbie uczniów)
i pełną listę z wyszukiwarką. Wszystko to są **agregaty** – ani jednej osoby, ani jednej nazwy
szkoły – a miejscowość z liczbą uczestników poniżej progu ochrony (domyślnie 3) nie pojawia się
z nazwą, tylko w zbiorczej pozycji „pozostałe miejscowości” swojego województwa
(``services.compute``). Uczestnicy są w większości małoletni, a strona jest publiczna.

Strona stoi za flagą konkursu ``participant_map`` (domyślnie wyłączona – mapa Polski nie ma sensu
w olimpiadzie międzynarodowej). Koordynator włącza ją, ustawia próg i wymusza przeliczenie na
ekranie ``/coordinator/participant-map/``; bez tego agregat odświeża się sam co godzinę
(pamięć podręczna).

Własna tabela jest jedna i trzyma wyłącznie ustawienia konkursu (``MapSettings``); punkty
miejscowości są danymi referencyjnymi przy wykazie szkół (``schools.TownLocation``).
"""
