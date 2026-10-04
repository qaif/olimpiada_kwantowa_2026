"""``{% error_tracking_loader %}`` – znacznik loadera błędów JavaScriptu (OPS-02 § 5).

Stoi w ``templates/base.html`` w tej samej linii co ``<meta charset>``: wyłączona funkcja daje pusty
napis, więc HTML strony jest co do bajtu taki jak przed OPS-02 (testy złote ``apps/tenancy``).
"""

from __future__ import annotations

import os

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html

from apps.monitoring.browser import browser_config

register = template.Library()


@register.simple_tag(takes_context=True)
def error_tracking_loader(context) -> str:
    config = browser_config()
    if config is None:
        return ""
    request = context.get("request")
    nonce = getattr(request, "csp_nonce", "") if request is not None else ""
    competition = getattr(request, "competition", None) if request is not None else None
    return format_html(
        '<script nonce="{}" src="{}" data-endpoint="{}" data-release="{}" data-environment="{}"'
        ' data-competition="{}"></script>',
        nonce,
        static("monitoring/errors.js"),
        config.endpoint,
        os.environ.get("APP_VERSION", "dev"),
        getattr(settings, "SENTRY_ENVIRONMENT", "production"),
        getattr(competition, "slug", "") or "",
    )
