"""Jednorazowy pomiar czasów testów dla pytest-split (format pliku .test_durations)."""

import json
from collections import defaultdict

_d = defaultdict(float)


def pytest_runtest_logreport(report):
    _d[report.nodeid] += report.duration


def pytest_sessionfinish(session):
    with open(".test_durations", "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(_d.items())), fh, indent=0, ensure_ascii=False)
