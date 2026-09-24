"""Zapowiedź materiałów na stronie „Warsztaty” – ``{% workshop_materials_teaser %}``.

Strona ``/warsztaty/`` jest treścią Wagtaila i stoi na allow-liście pamięci stron publicznych
(``apps.web.page_cache``), czyli wersja dla gościa bywa odtwarzana z Redisa. Stąd dwie reguły tej
zapowiedzi:

- gość dostaje **wyłącznie** liczbę materiałów i odnośnik do logowania – żadnego adresu pliku,
  nawet listy tytułów. Wszystko, co ma podpis, powstaje w widokach dla zalogowanych,
- treść zależy od tego, czy są opublikowane materiały, więc zapis materiału unieważnia pamięć
  witryny konkursu (odbiornik w ``apps.web.page_cache``).

Zalogowany (którego ta pamięć nie obsługuje) dostaje od razu odnośnik „Zobacz materiały”. Konto
spoza konkursu też go dostanie – i zobaczy 403 z wyjaśnieniem; zapowiedź nie pyta o rolę, bo to
kosztowałoby zapytania na stronie, która jest treścią, a nie panelem.

Przy wyłączonym przełączniku znacznik nie oddaje niczego i nie zadaje ani jednego zapytania.
"""

from __future__ import annotations

from django import template

from ..access import feature_enabled
from ..services import visible_materials

register = template.Library()


@register.inclusion_tag("cms/_workshop_materials_teaser.html", takes_context=True)
def workshop_materials_teaser(context) -> dict:
    request = context.get("request")
    competition = getattr(request, "competition", None)
    if not feature_enabled(competition):
        return {"show": False}
    count = visible_materials(competition).count()
    user = getattr(request, "user", None)
    return {
        "show": count > 0,
        "count": count,
        "authenticated": bool(user and user.is_authenticated),
    }
