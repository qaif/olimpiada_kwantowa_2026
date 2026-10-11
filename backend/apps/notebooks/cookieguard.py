"""Podrzucone ciasteczka z hosta laboratorium (QC-02 § 5, przegląd M1).

Kod z notatnika na ``lab.<domena>`` może ustawić ciasteczko z ``Domain=<domena>`` – przeglądarka
wyśle je potem **każdemu** hostowi serwisu obok prawdziwego (host-only), z terminem do ~400 dni.
Django przy dwóch ciasteczkach o tej samej nazwie bierze **ostatnie**, czyli zwykle podrzucone:
podrzucony ``csrftoken`` to 403 na każdym formularzu (DoS), podrzucony ``sessionid`` – wylogowanie
albo cudza sesja (fiksacja).

W produkcji ciasteczka sesji i CSRF mają wtedy prefiks ``__Host-`` (``config/settings/production.py``):
takiego ciasteczka przeglądarka nie przyjmie z atrybutem ``Domain`` ani z innego hosta, więc ich
podrzucić się nie da. Ta warstwa domyka resztę (dev bez TLS, ciasteczko języka, djcms ma kopię
w ``djcms/apps/pages/labguard.py``): gdy w nagłówku ``Cookie`` pilnowana nazwa występuje **dwa razy**,
a host żądania dzieli z hostem laboratorium domenę nadrzędną,

1. pierwsze takie żądanie dostaje przekierowanie pod ten sam adres (302 dla GET/HEAD, 307 dla reszty
   – przeglądarka powtórzy metodę i treść) z wygaszeniem (``Max-Age=0``) ciasteczek o tej nazwie dla
   każdej domeny nadrzędnej wspólnej z laboratorium i każdej ścieżki-przodka adresu (podrzucone
   ciasteczko przyszło z tym żądaniem, więc jego ścieżka jest przodkiem adresu). Ciasteczka host-only
   serwisu to inne ciasteczka (bez ``Domain``) – zostają,
2. gdyby wygaszenie nie zadziałało (znacznik ``olimpiada_cookie_wipe`` z kroku 1 jeszcze żyje), żądanie
   idzie dalej **bez** zdublowanych ciasteczek: żadna z dwóch wartości nie jest wiarygodna, a brak
   sesji jest bezpieczniejszy niż cudza.

Pojedynczego podrzuconego ciasteczka (bez prawdziwego obok) nie da się odróżnić po nagłówku –
przed tym chroni prefiks ``__Host-`` albo wariant z osobną domeną (QC-02 § 2).
"""

from __future__ import annotations

import logging

from django.http import HttpResponseRedirect

logger = logging.getLogger(__name__)

MARKER = "olimpiada_cookie_wipe"
#: Ścieżki-przodkowie adresu: tyle segmentów wystarcza każdemu adresowi serwisu, a liczba nagłówków
#: ``Set-Cookie`` zostaje ograniczona (nazwy × domeny × ścieżki).
MAX_SEGMENTS = 8
EXPIRED = "Thu, 01 Jan 1970 00:00:00 GMT"


def duplicated_names(raw_cookie: str, names) -> list[str]:
    """Pilnowane nazwy, które w surowym nagłówku ``Cookie`` występują więcej niż raz."""
    wanted = set(names)
    counts: dict[str, int] = {}
    for chunk in raw_cookie.split(";"):
        name = chunk.split("=", 1)[0].strip()
        if name in wanted:
            counts[name] = counts.get(name, 0) + 1
    return sorted(name for name, count in counts.items() if count > 1)


def shared_parent_domains(request_host: str, lab_host: str) -> list[str]:
    """Domeny, które laboratorium może wpisać w ``Domain`` i które dostaje host żądania.

    Ciasteczko z ``Domain=X`` ustawione na hoście laboratorium wymaga, żeby X był przyrostkiem hosta
    laboratorium (innym niż on sam), a do hosta żądania dojdzie, gdy X jest i jego przyrostkiem.
    Osobna domena rejestrowalna (wariant B) – lista pusta: podrzucenie jest niemożliwe.
    """
    host = request_host.split(":")[0].lower().rstrip(".")
    lab = lab_host.split(":")[0].lower().rstrip(".")
    labels = host.split(".")
    found = []
    for start in range(len(labels) - 1):  # co najmniej dwie etykiety – nie sam TLD
        candidate = ".".join(labels[start:])
        if lab.endswith(f".{candidate}"):
            found.append(candidate)
    return found


def ancestor_paths(path: str) -> list[str]:
    paths = ["/"]
    current = ""
    for segment in [part for part in path.split("/") if part][:MAX_SEGMENTS]:
        current = f"{current}/{segment}"
        paths += [current, f"{current}/"]
    return paths


def wipe_headers(names, domains, paths, *, secure: bool) -> list[str]:
    suffix = "; Secure" if secure else ""
    return [
        f"{name}=; Domain={domain}; Path={path}; Max-Age=0; Expires={EXPIRED}; SameSite=Lax{suffix}"
        for name in names
        for domain in domains
        for path in paths
    ]


class CookieWipeRedirect(HttpResponseRedirect):
    """Przekierowanie z **wieloma** nagłówkami ``Set-Cookie`` o tej samej nazwie.

    ``response.cookies`` Django trzyma jedno ciasteczko na nazwę, a wygaszenie musi trafić w każdą
    parę (domena, ścieżka). Handler WSGI składa nagłówki z ``response.items()`` – tu je dokładamy.
    """

    def __init__(self, url: str, wipes: list[str], *, status: int):
        super().__init__(url)
        self.status_code = status
        self.cookie_wipes = wipes

    def items(self):
        return [*super().items(), *(("Set-Cookie", header) for header in self.cookie_wipes)]


def guard(request, lab_host: str, names):
    """Odpowiedź z wygaszeniem podrzuconych ciasteczek albo ``None`` (żądanie idzie dalej)."""
    duplicates = duplicated_names(request.META.get("HTTP_COOKIE", ""), names)
    if not duplicates:
        return None
    domains = shared_parent_domains(request.get_host(), lab_host)
    if not domains:
        return None
    if MARKER not in request.COOKIES:
        logger.warning(
            "Zdublowane ciasteczka %s na %s (podrzucone z domeny nadrzędnej?) – wygaszam, powtarzam żądanie.",
            ", ".join(duplicates),
            request.get_host(),
        )
        wipes = wipe_headers(duplicates, domains, ancestor_paths(request.path), secure=request.is_secure())
        status = 302 if request.method in ("GET", "HEAD") else 307
        response = CookieWipeRedirect(request.get_full_path(), wipes, status=status)
        response.set_cookie(
            MARKER, "1", max_age=60, httponly=True, secure=request.is_secure(), samesite="Lax"
        )
        return response
    logger.warning(
        "Zdublowane ciasteczka %s na %s mimo wygaszenia – żądanie bez nich.",
        ", ".join(duplicates),
        request.get_host(),
    )
    request.COOKIES = {name: value for name, value in request.COOKIES.items() if name not in duplicates}
    return None
