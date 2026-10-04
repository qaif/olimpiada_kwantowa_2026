"""Formularz webinaru w panelu koordynatora (ekrany koordynatora są po polsku – zakres I18N-01)."""

from __future__ import annotations

from django import forms
from django.utils import timezone

from apps.web.forms import LocalDateTimeField

from .models import DURATION_MAX, DURATION_MIN, Audience, Webinar
from .services import co_moderator_choices, competition_zone


class ZonedDateTimeField(LocalDateTimeField):
    """``datetime-local`` w strefie **konkursu**, a nie instalacji.

    Uczestnik widzi godzinę w strefie konkursu (``Competition.time_zone``), więc koordynator musi
    ją wpisywać w tej samej strefie – inaczej konkurs prowadzony w innej strefie niż serwer
    ogłaszałby webinar o innej godzinie, niż ta wpisana. Konwersję robi Django
    (``from_current_timezone``/``to_current_timezone``) w strefie podstawionej na czas wywołania.
    """

    def __init__(self, *, zone, **kwargs):
        self.zone = zone
        super().__init__(**kwargs)

    def to_python(self, value):
        with timezone.override(self.zone):
            return super().to_python(value)

    def prepare_value(self, value):
        with timezone.override(self.zone):
            return super().prepare_value(value)


class WebinarForm(forms.ModelForm):
    class Meta:
        model = Webinar
        fields = (
            "title",
            "description",
            "starts_at",
            "duration_minutes",
            "audience",
            "stage",
            "include_committee",
            "co_moderators",
            "record",
            "public_link",
            "email_reminder",
        )
        labels = {
            "title": "Tytuł",
            "description": "Opis (widzą go odbiorcy i stoi w zaproszeniu)",
            "duration_minutes": "Czas trwania (minuty)",
            "audience": "Odbiorcy",
            "stage": "Etap (dla odbiorców „uczestnicy wybranego etapu”)",
            "include_committee": "Pokaż także komisji (recenzentom i komisji odwoławczej)",
            "co_moderators": "Współprowadzący",
            "record": "Nagrywanie (prowadzący włącza je przyciskiem w pokoju; MP4 w prywatnym magazynie)",
            "public_link": "Link dla gości bez konta",
            "email_reminder": "Przypomnienie e-mailem przed startem",
        }
        help_texts = {
            "co_moderators": (
                "Koordynatorzy i aktywni członkowie komisji. Wchodzą jako prowadzący "
                "(nadają obraz i dźwięk, dają głos)."
            ),
            "public_link": "Domyślnie wyłączony. Gość wchodzi jako widz z nazwą, którą sam wpisze.",
            "email_reminder": "Raz, do odbiorców, którzy nie wyłączyli listów o webinarach.",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "co_moderators": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, competition, extra_stage_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.competitions.models import Stage

        self.competition = competition
        zone = competition_zone(competition)
        self.fields["starts_at"] = ZonedDateTimeField(
            zone=zone, label=f"Początek (strefa {zone.key})", required=True
        )
        if self.instance.pk:
            self.initial["starts_at"] = self.instance.starts_at
        self.fields["duration_minutes"].min_value = DURATION_MIN
        self.fields["duration_minutes"].max_value = DURATION_MAX
        self.fields["duration_minutes"].widget.attrs.update({"min": DURATION_MIN, "max": DURATION_MAX})
        # Etapy **bieżącej** edycji (jak listy odbiorców komunikatów); przy edycji starego webinaru
        # zostaje też jego etap, żeby zapis bez zmian nie gubił odbiorców.
        stages = Stage.objects.for_competition(competition).filter(edition__is_current=True)
        keep = extra_stage_id or self.instance.stage_id
        if keep:
            stages = stages | Stage.objects.for_competition(competition).filter(pk=keep)
        self.fields["stage"].queryset = stages.select_related("edition").order_by("opens_at", "id")
        self.fields["stage"].required = False
        self.fields["co_moderators"].queryset = co_moderator_choices(competition)
        self.fields["co_moderators"].required = False
        if not competition.has_feature("team_entries"):
            self.fields["audience"].choices = [
                choice for choice in self.fields["audience"].choices if choice[0] != Audience.CAPTAINS
            ]

    def clean_duration_minutes(self):
        value = self.cleaned_data["duration_minutes"]
        if value is None or not DURATION_MIN <= value <= DURATION_MAX:
            raise forms.ValidationError(f"Czas trwania: od {DURATION_MIN} do {DURATION_MAX} minut.")
        return value

    def clean(self):
        data = super().clean()
        if data.get("audience") == Audience.STAGE and not data.get("stage"):
            self.add_error("stage", "Wybierz etap.")
        return data

    def service_data(self) -> dict:
        """Pola do serwisu (bez współprowadzących – te idą osobnym argumentem)."""
        return {name: value for name, value in self.cleaned_data.items() if name != "co_moderators"}
