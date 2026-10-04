"""Fałszywy serwer LiveKit dla nadzoru (Twirp) i magazyn w pamięci – bez sieci.

Podmienia ``apps.webinars.livekit._http_post`` (to samo jedyne wyjście do sieci, co webinary).
Każde wywołanie sprawdza token serwerowy **niezależną** implementacją (PyJWT): podpis, ``iss`` i
uprawnienie wymagane przez metodę (``roomAdmin`` dla pokoju, ``roomRecord`` dla Egress).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import jwt

URL = "wss://live.example.test"
API_KEY = "APItestKey"
API_SECRET = "s3cr3t-0123456789abcdef0123456789abcdef"

REQUIRED_GRANT = {
    "SendData": "roomAdmin",
    "GetParticipant": "roomAdmin",
    "RemoveParticipant": "roomAdmin",
    "StartTrackEgress": "roomRecord",
    "StopEgress": "roomRecord",
}


@dataclass
class FakeLiveKit:
    #: pokój → identity → lista ścieżek (``{"sid", "source"}``)
    rooms: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    egresses: dict[str, dict] = field(default_factory=dict)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    fail_with: Exception | None = None

    def names(self) -> list[str]:
        return [name for name, _payload in self.calls]

    def payload(self, name: str) -> dict:
        return next(payload for call, payload in reversed(self.calls) if call == name)

    def publish(self, room: str, identity: str, *sources: str) -> None:
        tracks = [{"sid": f"TR_{source}", "source": source} for source in sources]
        self.rooms.setdefault(room, {})[identity] = tracks

    def __call__(self, url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
        if self.fail_with is not None:
            raise self.fail_with
        assert url.startswith("https://live.example.test/twirp/livekit."), url
        method = url.rsplit("/", 1)[-1]
        payload = json.loads(body)
        token = headers["Authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, API_SECRET, algorithms=["HS256"])
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

    def _SendData(self, payload):  # noqa: N802 - nazwa metody Twirp
        payload["decoded"] = json.loads(base64.b64decode(payload["data"]))
        return self._ok({})

    def _GetParticipant(self, payload):  # noqa: N802
        tracks = self.rooms.get(payload["room"], {}).get(payload["identity"])
        if tracks is None:
            return self._not_found()
        return self._ok({"identity": payload["identity"], "tracks": tracks})

    def _RemoveParticipant(self, payload):  # noqa: N802
        if self.rooms.get(payload["room"], {}).pop(payload["identity"], None) is None:
            return self._not_found()
        return self._ok({})

    def _StartTrackEgress(self, payload):  # noqa: N802
        egress_id = f"EG_{len(self.egresses) + 1}"
        self.egresses[egress_id] = payload
        return self._ok({"egressId": egress_id, "status": "EGRESS_STARTING"})

    def _StopEgress(self, payload):  # noqa: N802
        return self._ok({"egressId": payload["egress_id"]})


class MemoryStorage:
    objects: dict[str, bytes] = {}
    deleted: list[str] = []
    signed: list[str] = []

    @classmethod
    def reset(cls):
        cls.objects = {}
        cls.deleted = []
        cls.signed = []

    def put(self, key, data, content_type):
        type(self).objects[key] = data

    def presigned_get(self, key, *, ttl, content_type, content_disposition):
        type(self).signed.append(key)
        return f"https://s3.example.test/{key}?X-Amz-Expires={ttl}"

    def delete(self, key):
        type(self).deleted.append(key)
        type(self).objects.pop(key, None)


STORAGE_PATH = "apps.proctoring.tests.fake_livekit.MemoryStorage"
