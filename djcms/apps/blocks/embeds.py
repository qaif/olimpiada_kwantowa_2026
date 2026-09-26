"""Osadzenia filmów budowane z **wzorca**, a nie z oEmbed (§ 6.2 docs/tasks/DJ-01.md, reguła 4).

Wagtail pyta dostawcę oEmbed i wstawia do strony HTML, który ten odeśle. Na ``dj.`` nie robimy tego
wcale: z adresu podanego przez redaktora wyciągamy wyłącznie **identyfikator filmu** i sami składamy
adres ramki z zaszytego wzorca. Skutki:

- do strony nie trafia ani jeden znak HTML z zewnątrz – nie ma czego sanityzować,
- djcms nie wychodzi do internetu (usługa i tak siedzi w sieci ``internal`` bez wyjścia),
- ramka może wskazywać wyłącznie hosty z ``frame-src`` polityki CSP
  (``apps.pages.middleware.EMBED_FRAME_SOURCES``) – YouTube przez ``youtube-nocookie.com``
  (bez ciasteczek śledzących do chwili odtworzenia) i Vimeo przez ``player.vimeo.com``.

Walidacja stoi w ``Embed.clean`` (formularz redaktora) i jeszcze raz przy renderze: wiersz mógł
powstać z pominięciem formularza (import, powłoka), a zły adres ma dać pustą wtyczkę, a nie ramkę.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

YOUTUBE_EMBED = "https://www.youtube-nocookie.com/embed/{id}"
VIMEO_EMBED = "https://player.vimeo.com/video/{id}"

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIMEO_ID = re.compile(r"^[0-9]{1,12}$")

_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
_YOUTUBE_EMBED_HOSTS = frozenset({"www.youtube-nocookie.com", "youtube-nocookie.com"})
_YOUTU_BE_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_VIMEO_HOSTS = frozenset({"vimeo.com", "www.vimeo.com"})
_VIMEO_PLAYER_HOSTS = frozenset({"player.vimeo.com"})


def _youtube_id(host: str, path: str, query: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if host in _YOUTU_BE_HOSTS:
        candidate = parts[0] if len(parts) == 1 else None
    elif host in _YOUTUBE_HOSTS and parts == ["watch"]:
        candidate = (parse_qs(query).get("v") or [None])[0]
    elif (
        host in _YOUTUBE_HOSTS | _YOUTUBE_EMBED_HOSTS
        and len(parts) == 2
        and parts[0] in {"embed", "shorts", "live"}
    ):
        candidate = parts[1]
    else:
        return None
    return candidate if candidate and _YOUTUBE_ID.match(candidate) else None


def _vimeo_id(host: str, path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if host in _VIMEO_HOSTS and len(parts) == 1:
        candidate = parts[0]
    elif host in _VIMEO_PLAYER_HOSTS and len(parts) == 2 and parts[0] == "video":
        candidate = parts[1]
    else:
        return None
    return candidate if _VIMEO_ID.match(candidate) else None


def embed_src(url: str | None) -> str | None:
    """Adres ramki dla adresu filmu z YouTube albo Vimeo; ``None`` dla wszystkiego innego.

    Tylko ``http(s)``, bez danych logowania i portu w adresie – ``javascript:``, ``data:``
    i „``https://youtube.com@evil.example/``” kończą się ``None``.
    """
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or parts.username or parts.password or port is not None:
        return None
    host = (parts.hostname or "").lower()
    video = _youtube_id(host, parts.path, parts.query)
    if video:
        return YOUTUBE_EMBED.format(id=video)
    video = _vimeo_id(host, parts.path)
    if video:
        return VIMEO_EMBED.format(id=video)
    return None
