"""Koszt CPU i liczba zapytań gorących adresów – pomiar w procesie, bez sieci (PERF-01, § 42.3).

Uzupełnienie generatora: ``loadgen.py`` mówi, **gdzie** rośnie kolejka, a ten skrypt – **ile** CPU
procesu ``web`` kosztuje jedno żądanie danego adresu (``time.process_time`` wątku obsługującego
żądanie, czyli Python + szablony, bez czekania na bazę) i ile zapytań do bazy wysyła. Z tej tabeli
liczy się pojemność w § 42.4: ``żądań/s na rdzeń ≈ 1000 / CPU ms``.

Uruchomienie (stos testu obciążenia, po ``run.sh seed``)::

    docker compose -f scripts/loadtest/docker-compose.loadtest.yml exec -T web \\
        python manage.py shell < scripts/loadtest/profile_endpoints.py

Działa wyłącznie na bazie z ``loadtest`` w nazwie (ten sam bezpiecznik, co ``loadtest_seed``).
"""

import re
import statistics
import time

from django.conf import settings
from django.db import connection, reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import User
from apps.chat.models import Conversation
from apps.competitions.models import Problem, Stage
from apps.results.models import ResultsPublication

assert "loadtest" in str(connection.settings_dict.get("NAME")), "tylko baza testu obciążenia"

REPEAT = 15
HOST = {"HTTP_HOST": "web", "HTTP_X_FORWARDED_PROTO": "http"}


def measure(label, client, method, path, **kwargs):
    cpu, wall, queries, sizes, statuses = [], [], [], [], set()
    for _ in range(REPEAT):
        reset_queries()
        with CaptureQueriesContext(connection) as ctx:
            c0, w0 = time.process_time(), time.perf_counter()
            response = getattr(client, method)(path, **HOST, **kwargs)
            body = b"".join(response.streaming_content) if response.streaming else response.content
            c1, w1 = time.process_time(), time.perf_counter()
        cpu.append((c1 - c0) * 1000)
        wall.append((w1 - w0) * 1000)
        queries.append(len(ctx.captured_queries))
        sizes.append(len(body))
        statuses.add(response.status_code)
    print(
        f"| {label} | {statistics.median(cpu):.1f} | {statistics.median(wall):.1f} | {max(queries)} "
        f"| {statistics.median(sizes) / 1024:.0f} | {','.join(map(str, sorted(statuses)))} |"
    )


student = User.objects.filter(email="lt-student-00042@loadtest.local").first()
coordinator = User.objects.filter(email="lt-coordinator-01@loadtest.local").first()
written = Stage.objects.get(name="International stage (loadtest)")
quiz_stage = Stage.objects.get(name="Online test (loadtest)")
problem = Problem.objects.filter(stage=written).order_by("number").first()
publication = ResultsPublication.objects.first()

print(f"PAGE_CACHE_ENABLED={settings.PAGE_CACHE_ENABLED}, powtórzeń {REPEAT}, mediana")
print("| adres | CPU ms | czas ms | zapytań (maks.) | KB | status |")
print("|---|---:|---:|---:|---:|---|")

anon = Client()
measure("GET / (gość, cache)", anon, "get", "/")
measure("GET /?utm_source=x (gość)", anon, "get", "/?utm_source=x")
measure("GET /aktualnosci/ (gość)", anon, "get", "/aktualnosci/")
measure("GET /wyniki/ (gość)", anon, "get", "/wyniki/")
measure(f"GET /results/{publication.stage_id}/ (gość)", anon, "get", f"/results/{publication.stage_id}/")
measure("GET /login/", anon, "get", "/login/")

me = Client()
me.force_login(student)
measure("GET /me/", me, "get", "/me/")
measure("GET statement PDF", me, "get", f"/api/competitions/problems/{problem.pk}/statement/")
conversation = Conversation.objects.filter(participant__user=student).first()
measure("GET chat thread", me, "get", f"/me/messages/{conversation.pk}/")
thread = me.get(f"/me/messages/{conversation.pk}/", **HOST).content.decode()
poll = re.search(r'hx-get="([^"]*fragment=messages[^"]*)"', thread).group(1).replace("&amp;", "&")
measure("GET chat poll (204)", me, "get", poll, HTTP_HX_REQUEST="true")
measure("GET quiz start", me, "get", f"/me/stages/{quiz_stage.pk}/test/")

coord = Client()
coord.force_login(coordinator)
measure("GET /coordinator/", coord, "get", "/coordinator/")
measure("GET stage progress", coord, "get", f"/coordinator/stages/{written.pk}/progress/")
measure("GET quiz results", coord, "get", f"/coordinator/stages/{quiz_stage.pk}/quiz/results/")
measure("GET export participants CSV", coord, "get", "/coordinator/export/participants/csv/")
