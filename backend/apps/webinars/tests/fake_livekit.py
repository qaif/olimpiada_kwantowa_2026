"""Fałszywy serwer LiveKit (API Twirp) i magazyn nagrań w pamięci – bez sieci.

Podmienia ``apps.webinars.livekit._http_post``. Każde wywołanie **sprawdza token serwerowy**
niezależną implementacją (PyJWT): podpis sekretem, ``iss`` = klucz API i uprawnienie wymagane przez
metodę (``roomAdmin`` dla pokoju, ``roomCreate`` dla ``DeleteRoom``, ``roomRecord`` dla Egress) –
tak jak prawdziwy serwer. Stan: osoby w pokojach (z uprawnieniem ``can_publish``) i egressy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field

import jwt

URL = "wss://live.example.test"
API_KEY = "APItestKey"
API_SECRET = "s3cr3t-0123456789abcdef0123456789abcdef"

REQUIRED_GRANT = {
    "UpdateParticipant": "roomAdmin",
    "RemoveParticipant": "roomAdmin",
    "DeleteRoom": "roomCreate",
    "CreateRoom": "roomCreate",
    "ListEgress": "roomRecord",
    "StartRoomCompositeEgress": "roomRecord",
    "StopEgress": "roomRecord",
}


@dataclass
class FakeLiveKit:
    rooms: dict[str, dict[str, dict]] = field(default_factory=dict)
    egresses: dict[str, dict] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    fail_with: Exception | None = None

    def names(self) -> list[str]:
        return [name for name, _payload in self.calls]

    def payload(self, name: str) -> dict:
        return next(payload for call, payload in reversed(self.calls) if call == name)

    def join(self, room: str, identity: str, *, can_publish: bool = False) -> None:
        self.rooms.setdefault(room, {})[identity] = {"can_publish": can_publish}

    def __call__(self, url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        assert timeout and timeout <= 10, "klient LiveKit musi mieć limit czasu"
        if self.fail_with is not None:
            raise self.fail_with
        assert url.startswith("https://live.example.test/twirp/livekit."), url
        method = url.rsplit("/", 1)[-1]
        payload = json.loads(body)
        token = headers["Authorization"].removeprefix("Bearer ")
        try:
            claims = jwt.decode(token, API_SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return 401, json.dumps({"code": "unauthenticated", "msg": "bad token"}).encode()
        assert claims["iss"] == API_KEY
        grant = REQUIRED_GRANT[method]
        if not claims.get("video", {}).get(grant):
            return 403, json.dumps({"code": "permission_denied", "msg": grant}).encode()
        if grant == "roomAdmin":
            assert claims["video"]["room"] == payload["room"], "token admina na inny pokój"
        self.calls.append((method, payload))
        return getattr(self, f"_{method}")(payload)

    @staticmethod
    def _ok(data: dict) -> tuple[int, bytes]:
        return 200, json.dumps(data).encode()

    @staticmethod
    def _not_found() -> tuple[int, bytes]:
        return 404, json.dumps({"code": "not_found", "msg": "participant not found"}).encode()

    def _UpdateParticipant(self, payload):  # noqa: N802 - nazwa metody Twirp
        person = self.rooms.get(payload["room"], {}).get(payload["identity"])
        if person is None:
            return self._not_found()
        person["can_publish"] = payload["permission"]["can_publish"]
        return self._ok({"identity": payload["identity"]})

    def _RemoveParticipant(self, payload):  # noqa: N802
        if self.rooms.get(payload["room"], {}).pop(payload["identity"], None) is None:
            return self._not_found()
        return self._ok({})

    def _CreateRoom(self, payload):  # noqa: N802
        self.rooms.setdefault(payload["name"], {})
        return self._ok({"name": payload["name"], "empty_timeout": payload.get("empty_timeout")})

    def _ListEgress(self, payload):  # noqa: N802
        egress = self.egresses.get(payload.get("egress_id"))
        if egress is None:
            return self._ok({"items": []})
        status = egress.get("status") or ("EGRESS_ACTIVE" if egress["active"] else "EGRESS_COMPLETE")
        item = {
            "egress_id": payload["egress_id"],
            "status": status,
            "file_results": egress.get("file_results", []),
        }
        return self._ok({"items": [item]})

    def _DeleteRoom(self, payload):  # noqa: N802
        self.rooms.pop(payload["room"], None)
        return self._ok({})

    def _StartRoomCompositeEgress(self, payload):  # noqa: N802
        egress_id = f"EG_{len(self.egresses) + 1}"
        self.egresses[egress_id] = {"payload": payload, "active": True}
        return self._ok(
            {"egress_id": egress_id, "room_name": payload["room_name"], "status": "EGRESS_STARTING"}
        )

    def _StopEgress(self, payload):  # noqa: N802
        egress = self.egresses.get(payload["egress_id"])
        if egress is None:
            return self._not_found()
        if not egress["active"]:
            return 412, json.dumps({"code": "failed_precondition", "msg": "egress already ended"}).encode()
        egress["active"] = False
        return self._ok({"egress_id": payload["egress_id"], "status": "EGRESS_ENDING"})


def sign_webhook(
    body: bytes, *, secret: str = API_SECRET, key: str = API_KEY, ttl: int = 300, **extra
) -> str:
    """Nagłówek ``Authorization`` webhooka – jak serwer LiveKit (PyJWT, niezależnie od kodu platformy)."""
    now = int(time.time())
    claims = {
        "iss": key,
        "nbf": now - 5,
        "exp": now + ttl,
        "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
        **extra,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


class MemoryRecordingStorage:
    """Magazyn nagrań w pamięci: zapamiętuje podpisane odczyty i skasowane klucze."""

    deleted: list[str] = []
    signed: list[str] = []

    @classmethod
    def reset(cls):
        cls.deleted = []
        cls.signed = []

    def presigned_get(self, key, *, ttl, content_type, content_disposition):
        type(self).signed.append(key)
        return f"https://s3.example.test/submissions/{key}?X-Amz-Expires={ttl}&sig=abc"

    def delete(self, key):
        type(self).deleted.append(key)


STORAGE_PATH = "apps.webinars.tests.fake_livekit.MemoryRecordingStorage"
