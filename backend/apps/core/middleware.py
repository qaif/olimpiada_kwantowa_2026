"""Telemetria odpowiedzi serwera: ile razy w ostatnim kwadransie coś się wywróciło.

Jedna warstwa i jedna liczba. Nie zbieramy ścieżek, nagłówków ani identyfikatorów użytkowników:
alert ma powiedzieć „serwis oddaje błędy”, a szczegóły stoją w logu, do którego dyżurny i tak
musi zajrzeć. Licznik trafia do cache'u pod kluczem bez żadnych danych osobowych, więc podgląd
Redisa nie jest listą tego, komu się nie udało.

Dlaczego to nie wystarcza zrobić logami: log trzeba czytać, żeby się dowiedzieć. Ten licznik
czyta co pięć minut ``apps.core.alerts`` i sam pisze list – różnica jest między „wiedzieliśmy,
gdyby ktoś spojrzał” a „dowiedzieliśmy się”.
"""

from __future__ import annotations

from apps.core.alerts import SERVER_ERRORS_KEY, bump


class ServerErrorCounterMiddleware:
    """Zlicza odpowiedzi 5xx do okna alertowego.

    Miejsce w łańcuchu: możliwie na zewnątrz, ale **za** ``SecurityMiddleware``. Chodzi o to, żeby
    zobaczyć także odpowiedzi 500 wygenerowane przez wewnętrzne warstwy z wyjątku widoku – te
    powstają głębiej i wracają tędy jak każda inna odpowiedź. Wyjątku nie przechwytujemy sami
    (``process_exception``), bo Django i tak zamienia go na odpowiedź 500, a druga obsługa
    liczyłaby to samo zdarzenie dwa razy.

    Kod 4xx nie jest liczony i nie będzie: 404 od robota i 403 od kogoś, kto wszedł nie tam,
    są normalnym ruchem, a nie awarią serwisu.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code >= 500:
            bump(SERVER_ERRORS_KEY)
        return response
