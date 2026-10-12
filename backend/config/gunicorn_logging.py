"""Access log gunicorna bez tokenów z adresów (audyt bezpieczeństwa 10.10.2026).

Kilka adresów aplikacji niesie w ścieżce jednorazowy sekret, który sam jest uprawnieniem: link
aktywacji konta, reset hasła, zaproszenie ucznia, zgoda opiekuna, przepustka do pokoju wideo,
przyjęcie delegacji… Domyślny access log (``--access-logfile -``) wypisuje pełny adres żądania
i nagłówek ``Referer`` – a ten log ląduje w ``docker logs`` (250 MB na usługę, docker-compose.yml),
w każdym wklejonym do zgłoszenia fragmencie i u każdego, kto ma dostęp do Dockera na hoście. Token
z logu to gotowy link do cudzego konta albo cudzej zgody.

Klasa podmienia wyłącznie to, co trafia do linijki logu: sekretne segmenty ścieżki i wartości
parametrów o „sekretnych” nazwach zamienia na ``***``. Żądanie, routing i aplikacja widzą adres
bez zmian. Wpinana w docker-compose.yml: ``--logger-class config.gunicorn_logging.TokenMaskingLogger``.

Lista prefiksów odpowiada wzorcom z ``<token>``/``<key>``/``<uidb64>`` w ``apps/web/urls*.py``.
Prefiks konkursu na domenie głównej (``/<prefiks>/activate/…``) jest obsłużony – wzorzec szuka
segmentu w dowolnym miejscu ścieżki. Nowy adres z sekretem w ścieżce = nowa pozycja na liście.
"""

from __future__ import annotations

import re

from gunicorn.glogging import Logger

MASK = "***"

#: Segmenty ścieżki, po których następuje sekret (jeden albo – dla resetu hasła – dwa segmenty).
#: Słowa w nawiasie po lewej to adresy BEZ sekretu o tym samym prefiksie (``activate/resend/``,
#: ``reset/done/``, ``zgoda/dziekujemy/``…) – zostają czytelne, bo pomagają przy diagnozie.
_SECRET_PATHS = re.compile(
    r"(?P<prefix>/(?:"
    r"activate|zgoda|opiekun/zgoda|zaproszenie/wideo|zaproszenie|account/email/confirm"
    r"|forum/unsubscribe|delegation/accept|me/messages/new"
    r")/)(?!(?:resend|dziekujemy|wideo|done)/)(?P<secret>[^/?#\s\"]+)"
)
#: Reset hasła: ``reset/<uidb64>/<token>/`` – oba segmenty (uid sam w sobie wskazuje konto).
_RESET = re.compile(r"(?P<prefix>/reset/)(?!done/)[^/?#\s\"]+/[^/?#\s\"]+")
#: Parametry zapytania z sekretem (``?token=``, ``?key=``, podpisy adresów, JWT pokoju).
_SECRET_QUERY = re.compile(
    r"(?P<name>(?:^|[?&;])(?:token|key|code|signature|sig|jwt|password|secret|uid|"
    r"X-Amz-Signature|X-Amz-Credential)=)[^&;#\s\"]*",
    re.IGNORECASE,
)


def mask_secrets(value: str) -> str:
    """Adres (albo linijka żądania, albo Referer) z sekretami zamienionymi na ``***``."""
    if not value or value == "-":
        return value
    value = _RESET.sub(lambda m: f"{m.group('prefix')}{MASK}/{MASK}", value)
    value = _SECRET_PATHS.sub(lambda m: f"{m.group('prefix')}{MASK}", value)
    return _SECRET_QUERY.sub(lambda m: f"{m.group('name')}{MASK}", value)


class TokenMaskingLogger(Logger):
    """``gunicorn.glogging.Logger`` z maskowaniem sekretów w polach adresu access logu."""

    #: Pola formatu access logu z adresem: linijka żądania (``%(r)s``), ścieżka (``%(U)s``),
    #: zapytanie (``%(q)s``) i Referer (``%(f)s``, także jako ``%({referer}i)s``).
    _URL_ATOMS = ("r", "U", "q", "f", "{referer}i")

    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        for key in self._URL_ATOMS:
            if isinstance(atoms.get(key), str):
                atoms[key] = mask_secrets(atoms[key])
        return atoms
