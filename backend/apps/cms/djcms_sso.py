"""Przejście redaktora z ``/cms/`` do django CMS jednorazowym tokenem (SSO, DJ-02 § 1.2 D5, D6, S11).

**Kto redaguje który konkurs w djcms, rozstrzyga ta aplikacja** – tym samym pytaniem, które
zadaje ``/cms/``: czy konto może edytować korzeń drzewa stron witryny konkursu
(``competition.site.root_page.permissions_for_user(user)``). djcms nie ma własnych kont redaktorów
ani haseł (decyzja użytkownika z 26.09.2026): dostaje wynik tego pytania w podpisanym tokenie
i **zastępuje** nim uprawnienia konta przy każdym logowaniu.

**Przebieg.**

1. Pozycja menu Wagtaila „Edytuj w django CMS” prowadzi do :func:`handoff` (``GET``) – strona
   z przyciskiem. Sam token powstaje dopiero po ``POST`` z tokenem CSRF: przejście nie da się
   wywołać z obcej strony, a token nie trafia do adresu, historii ani dzienników dostępu,
2. odpowiedź na ``POST`` to formularz wysyłany automatycznie (``POST``) na ``/djcms/sso/`` **tego
   samego** hosta i prefiksu konkursu (adres względny – nie ma tu żadnego celu do podstawienia),
3. djcms (``djcms/apps/sites/sso.py``) sprawdza podpis, odbiorcę, czas, host, nagłówek ``Origin``
   i jednorazowość, zakłada/aktualizuje konto ``web:<id>`` bez hasła i ustawia jego grupy
   ``redakcja:*`` dokładnie na te z tokenu.

**Format tokenu** (wektor wspólny obu projektów: ``backend/djcms_contract/sso_token_cases.json``)::

    v1.<B>.<base64url(HMAC-SHA256(DJCMS_SSO_KEY, "olimpiada/djcms-sso/v1." + <B>))>,  B = base64url(JSON)

JSON (klucze posortowane, bez odstępów, ASCII): ``v`` (1), ``aud`` (``"djcms"``), ``iss``
(``"web"``), ``sub`` (id konta), ``email``, ``first_name``, ``last_name``, ``host`` (host żądania,
małymi literami, bez portu), ``platform`` (``true`` = wszystkie konkursy – grupa
``redakcja:platforma``), ``competitions`` (``[{"slug", "abilities": ["edit"[, "publish"]]}]``),
``nonce`` (jednorazowy), ``iat``/``exp`` (sekundy epoki, ``exp - iat`` = :data:`TOKEN_TTL_SECONDS`).

**Mapowanie uprawnień** (:func:`editor_grants`):

- ``edit`` – ``can_edit()`` na korzeniu witryny konkursu; ``publish`` – dodatkowo ``can_publish()``.
  Liczone z pominięciem zamrożenia edycji (``apps.cms.freeze.ignoring``): zamrożenie Wagtaila jest
  właśnie powodem, dla którego redaktor idzie do djcms,
- ``platform`` – konto bez ograniczeń w ``/cms/`` (``apps.cms.scope.is_unrestricted``:
  superużytkownik, superkoordynator, grupa z prawami do korzenia drzewa) **i** z edycją i publikacją
  w każdym aktywnym konkursie. W djcms: grupa ``redakcja:platforma`` (wszystkie witryny),
- **nigdy** superużytkownik djcms: tej roli token nie niesie w ogóle. Konta i uprawnienia djcms
  zmienia wyłącznie techniczny superużytkownik (``bootstrap_djcms_admin``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver
from django.template.response import TemplateResponse
from django.urls import get_script_prefix
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

TOKEN_VERSION = 1
TOKEN_PREFIX = "v1"
AUDIENCE = "djcms"
ISSUER = "web"
#: Kontekst podpisu – ten sam klucz nie podpisuje w tej aplikacji niczego innego, ale gdyby kiedyś
#: miał, podpis tokenu SSO nie da się pomylić z żadnym innym.
SIGNING_CONTEXT = b"olimpiada/djcms-sso/v1."
#: Ważność tokenu: tyle, ile trwa jedno automatyczne przesłanie formularza, z zapasem na wolne łącze.
TOKEN_TTL_SECONDS = 60
KEY_MIN_LENGTH = 32

ABILITY_EDIT = "edit"
ABILITY_PUBLISH = "publish"

#: Adres logowania SSO w djcms – względem korzenia hosta i prefiksu konkursu (DJ-02 D2).
DJCMS_SSO_PATH = "djcms/sso/"

#: Hosty lokalne, które w ``DEBUG`` rozstrzygają konkurs domyślny – tak samo jak w djcms
#: (``djcms/apps/sites/resolution.py::LOCAL_HOSTS``).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


# --- klucz --------------------------------------------------------------------------------------


def sso_key() -> str:
    return getattr(settings, "DJCMS_SSO_KEY", "") or ""


def sso_enabled() -> bool:
    """Klucz ustawiony i dość długi. Krótszy działa jak pusty – token nie powstaje wcale."""
    return len(sso_key()) >= KEY_MIN_LENGTH


def key_problems() -> list[str]:
    """Powody ostrzeżenia ``cms.W013`` (pusty klucz to nie problem – SSO jest wtedy wyłączone)."""
    key = sso_key()
    if not key:
        return []
    problems = []
    if len(key) < KEY_MIN_LENGTH:
        problems.append(
            f"DJCMS_SSO_KEY ma {len(key)} znaków – przejście do django CMS wymaga co najmniej "
            f"{KEY_MIN_LENGTH} i do tego czasu jest wyłączone."
        )
    others = {
        "DJCMS_INTERNAL_TOKEN": getattr(settings, "DJCMS_INTERNAL_TOKEN", "") or "",
        "SECRET_KEY": getattr(settings, "SECRET_KEY", "") or "",
    }
    for name, value in others.items():
        if value and hmac.compare_digest(value.encode(), key.encode()):
            problems.append(f"DJCMS_SSO_KEY jest równy {name} – każdy sekret ma być osobny.")
    return problems


# --- kto redaguje co ----------------------------------------------------------------------------


def normalise_host(value: str) -> str:
    """Host do porównań: bez portu, bez kropki końcowej, małymi literami (jak djcms)."""
    return (value or "").strip().lower().partition(":")[0].rstrip(".")


@dataclass(frozen=True)
class EditorGrants:
    """Wynik pytania „co to konto może redagować” – treść tokenu."""

    platform: bool
    #: ``(slug, (umiejętności…))`` posortowane po slugu; wyłącznie konkursy z ``edit``.
    competitions: tuple[tuple[str, tuple[str, ...]], ...]

    def abilities_for(self, slug: str) -> tuple[str, ...]:
        if self.platform:
            return (ABILITY_EDIT, ABILITY_PUBLISH)
        return next((abilities for item, abilities in self.competitions if item == slug), ())

    def allows(self, slug: str) -> bool:
        return ABILITY_EDIT in self.abilities_for(slug)

    @property
    def empty(self) -> bool:
        return not self.platform and not self.competitions


def competition_abilities(user, competition) -> tuple[str, ...]:
    """Co konto może zrobić z witryną konkursu w ``/cms/`` – ``()``, ``("edit",)`` albo z ``publish``."""
    from . import freeze

    with freeze.ignoring():
        tester = competition.site.root_page.permissions_for_user(user)
        if not tester.can_edit():
            return ()
        return (ABILITY_EDIT, ABILITY_PUBLISH) if tester.can_publish() else (ABILITY_EDIT,)


def _active_competitions():
    from apps.tenancy.models import Competition

    return Competition.objects.filter(is_active=True).select_related("site__root_page").order_by("slug")


def editor_grants(user) -> EditorGrants:
    """Konkursy, które konto redaguje w ``/cms/`` (patrz docstring modułu – mapowanie)."""
    from .scope import is_unrestricted

    if user is None or not user.is_authenticated or not user.is_active:
        return EditorGrants(platform=False, competitions=())
    competitions = list(_active_competitions())
    rows = []
    for competition in competitions:
        abilities = competition_abilities(user, competition)
        if abilities:
            rows.append((competition.slug, abilities))
    platform = (
        bool(competitions)
        and is_unrestricted(user)
        and len(rows) == len(competitions)
        and all(ABILITY_PUBLISH in abilities for _slug, abilities in rows)
    )
    return EditorGrants(platform=platform, competitions=tuple(rows))


def competition_hosts(competition) -> set[str]:
    """Hosty, pod którymi stoi konkurs – te same, które dostaje djcms (``djcms_api.competitions``)."""
    hosts = {normalise_host(competition.site.hostname), normalise_host(competition.primary_domain or "")}
    if competition.site.is_default_site:
        hosts.add(normalise_host(getattr(settings, "SITE_DOMAIN", "") or ""))
    hosts.discard("")
    return hosts


def known_host(host: str) -> bool:
    """Czy host należy do któregoś aktywnego konkursu – tylko pod takim djcms ma witrynę."""
    if settings.DEBUG and host in LOCAL_HOSTS:
        return True
    return any(host in competition_hosts(competition) for competition in _active_competitions())


# --- token --------------------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign(body: str, key: str) -> str:
    digest = hmac.new(key.encode("utf-8"), SIGNING_CONTEXT + body.encode("ascii"), hashlib.sha256).digest()
    return _b64(digest)


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def token_payload(*, user, host: str, grants: EditorGrants, now: int, nonce: str) -> dict:
    return {
        "v": TOKEN_VERSION,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "sub": user.pk,
        "email": user.email,
        "first_name": (user.first_name or "")[:150],
        "last_name": (user.last_name or "")[:150],
        "host": host,
        "platform": grants.platform,
        "competitions": [
            {"slug": slug, "abilities": list(abilities)} for slug, abilities in grants.competitions
        ],
        "nonce": nonce,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }


def encode_token(payload: dict, key: str) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii"))
    return f"{TOKEN_PREFIX}.{body}.{sign(body, key)}"


def issue_token(
    *, user, host: str, grants: EditorGrants, now: int | None = None, nonce: str | None = None
) -> str:
    """Podpisany token dla konta i hosta. ``now``/``nonce`` – wyłącznie dla testów i wektora kontraktu."""
    if not sso_enabled():
        raise RuntimeError("DJCMS_SSO_KEY nie jest ustawiony – token nie powstaje.")
    payload = token_payload(
        user=user,
        host=host,
        grants=grants,
        now=int(time.time()) if now is None else now,
        nonce=nonce or new_nonce(),
    )
    return encode_token(payload, sso_key())


# --- widok --------------------------------------------------------------------------------------


@never_cache
@require_http_methods(["GET", "POST"])
def handoff(request):
    """„Edytuj w django CMS”: ``GET`` – przycisk, ``POST`` (CSRF) – token i automatyczny formularz.

    Widok stoi w ``/cms/`` (``register_admin_urls``), więc Wagtail wpuszcza tu wyłącznie konta
    z ``wagtailadmin.access_admin``. Odmowa (brak klucza, konto bez tego konkursu, host spoza
    listy konkursów) to strona z wyjaśnieniem, a nie token.
    """
    competition = getattr(request, "competition", None)
    host = normalise_host(request.get_host())
    context = {
        "competition": competition,
        "sso_url": f"{get_script_prefix()}{DJCMS_SSO_PATH}",
    }
    if not sso_enabled():
        return TemplateResponse(
            request, "cms/admin/djcms_handoff.html", {**context, "state": "disabled"}, status=503
        )
    grants = editor_grants(request.user)
    if competition is None or not known_host(host) or not grants.allows(competition.slug):
        return TemplateResponse(
            request, "cms/admin/djcms_handoff.html", {**context, "state": "forbidden"}, status=403
        )
    if request.method == "GET":
        return TemplateResponse(request, "cms/admin/djcms_handoff.html", {**context, "state": "confirm"})

    token = issue_token(user=request.user, host=host, grants=grants)
    # Bez tokenu w logu: identyfikator konta i lista konkursów wystarczą do audytu.
    logger.info(
        "SSO do django CMS: konto #%s, host %s, konkursy: %s",
        request.user.pk,
        host,
        "wszystkie" if grants.platform else ", ".join(slug for slug, _abilities in grants.competitions),
    )
    # Bez ``Referrer-Policy: no-referrer``: przy niej przeglądarka wysłałaby ``Origin: null``,
    # a djcms przyjmuje token wyłącznie z ``Origin`` równym własnemu hostowi.
    return TemplateResponse(
        request, "cms/admin/djcms_handoff.html", {**context, "state": "submit", "token": token}
    )


# --- wylogowanie: także z django CMS ----------------------------------------------------------------

#: Ciasteczko sesji djcms – ``SESSION_COOKIE_NAME`` w ``djcms/config/settings/base.py``: host-only
#: (bez ``SESSION_COOKIE_DOMAIN``), ``Path=/`` (domyślne ``SESSION_COOKIE_PATH``), ``SameSite=Lax``.
DJCMS_SESSION_COOKIE = "djcms_sessionid"
_EXPIRE_DJCMS_SESSION = "_expire_djcms_session"


@receiver(user_logged_out, dispatch_uid="cms_djcms_sso_logout")
def _mark_djcms_session_for_expiry(sender, request=None, **kwargs) -> None:
    if request is not None and DJCMS_SESSION_COOKIE in request.COOKIES:
        setattr(request, _EXPIRE_DJCMS_SESSION, True)


class DjcmsLogoutMiddleware:
    """Wylogowanie z aplikacji głównej zamyka też sesję w django CMS na tym samym hoście.

    Sesja redaktora w djcms powstaje z ``/cms/`` (SSO) **zawsze pod tym samym hostem** (djcms
    przyjmuje token tylko od własnego hosta) i ma własne ciasteczko host-only ``djcms_sessionid``.
    Bez tej warstwy przeżyłaby wylogowanie o ``DJCMS_SSO_SESSION_SECONDS`` – na wspólnym komputerze
    następna osoba weszłaby do panelu djcms bez logowania. Każda droga wylogowania (strona, API,
    ``/cms/logout/``, usunięcie konta) kończy się ``django.contrib.auth.logout`` i sygnałem
    ``user_logged_out``; odbiornik zaznacza żądanie, a ta warstwa dokłada do odpowiedzi wygaszenie
    ciasteczka (``Max-Age=0``, ta sama ścieżka i ``SameSite``). Sama sesja w bazie djcms wygasa
    swoim terminem – bez ciasteczka nikt się do niej nie dostanie.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, _EXPIRE_DJCMS_SESSION, False):
            response.delete_cookie(DJCMS_SESSION_COOKIE, path="/", samesite="Lax")
        return response
