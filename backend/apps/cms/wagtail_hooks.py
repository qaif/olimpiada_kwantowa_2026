"""Rejestracja modeli ``apps.cms`` w edytorze Wagtaila.

Dziś jest tu jedna rzecz: komunikaty organizatora jako **snippet**. Ekranem podstawowym jest
``/coordinator/announcements/`` – komunikat pisze się wtedy, gdy coś już nie działa, a wtedy
organizator jest w swoim panelu, nie w edytorze treści. Wpis w ``/cms/`` jest drugą drogą i ma
konkretnego adresata: redakcja bywa kimś innym niż organizator zawodów i pracuje w Wagtailu –
dla niej pasek z komunikatem jest elementem serwisu obok stron i dokumentów.

Obie drogi zapisują ten sam model, więc żadna reguła się nie dubluje: okna czasowego pilnuje
``Announcement.clean()`` i constraint w bazie, a pamięć podręczną baneru czyści sygnał
``post_save`` (``apps.cms.announcements``) niezależnie od tego, kto zapisał.
"""

from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Announcement


class AnnouncementViewSet(SnippetViewSet):
    """Komunikaty w ``/cms/`` → Fragmenty. Kolumny listy odpowiadają na „co i czy wisi”."""

    model = Announcement
    icon = "warning"
    menu_label = "Komunikaty"
    list_display = ("text", "level", "starts_at", "ends_at", "is_active")
    list_filter = ("level", "is_active")
    search_fields = ("text",)

    panels = [
        MultiFieldPanel([FieldPanel("text"), FieldPanel("level")], heading="Komunikat"),
        MultiFieldPanel(
            [FieldPanel("link_url"), FieldPanel("link_label")],
            heading="Odnośnik (opcjonalny)",
        ),
        MultiFieldPanel(
            [FieldPanel("starts_at"), FieldPanel("ends_at"), FieldPanel("is_active")],
            heading="Kiedy pokazywać",
        ),
        FieldPanel("dismissible"),
    ]


register_snippet(AnnouncementViewSet)
