"""Serializery API zawodów – deklaratywne, bez logiki domenowej.

Publiczne zasoby (``editions/current/``) nie zawierają żadnych danych uczestników: wyłącznie
terminy, rodzaje etapów i metadane zadań. Treść zadania jest ujawniana dopiero po otwarciu etapu.
"""

from django.urls import reverse
from rest_framework import serializers

from .models import Edition, Problem, Stage, StageEntry
from .services import entry_owner


def _statements_visible(stage, context) -> bool:
    """Jawność treści zadań – ``has_opened`` z oknami czasowymi (TZ-01).

    W etapie z oknami zalogowany uczeń widzi zadania od startu swojego okna, a anonim po końcu
    ostatniego. Bez flagi konkursu to dokładnie ``stage.has_opened(now)``, bez zapytania.
    """
    from apps.time_windows.access import statements_visible

    request = context.get("request")
    return statements_visible(
        stage,
        user=getattr(request, "user", None),
        competition=getattr(request, "competition", None),
        now=context.get("now"),
    )


class PublicStageSerializer(serializers.ModelSerializer):
    """Etap w widoku publicznym: tylko oś czasu, bez danych o uczestnikach i wpisach.

    ``kind`` zostaje na miejscu (klienci filtrują po nim etapy), a obok dochodzą ``name``
    (własna nazwa albo pusty tekst) i ``display_name`` – podpis gotowy do wyświetlenia. Klient,
    który dotąd sam mapował ``kind`` na etykietę, działa bez zmian; nowy bierze ``display_name``
    i widzi nazwę nadaną przez organizatora.
    """

    display_name = serializers.CharField(read_only=True)

    class Meta:
        model = Stage
        fields = (
            "id",
            "kind",
            "name",
            "display_name",
            "format",
            "opens_at",
            "deadline_at",
            "grace_seconds",
            # Dni wydarzenia są osobnymi polami, a nie gotowym napisem z zakresem: klient API
            # składa własne zdanie (i własny język), a formatowanie „4–7 czerwca 2027” należy do
            # warstwy widoku. ``null`` dla etapów zdalnych – tam po prostu nie ma zjazdu.
            "event_starts_on",
            "event_ends_on",
            "results_published_at",
        )
        read_only_fields = fields


class PublicProblemSerializer(serializers.ModelSerializer):
    """Zadanie w widoku publicznym. ``statement_pdf`` jest ``None``, dopóki etap się nie otworzy."""

    statement_pdf = serializers.SerializerMethodField()

    class Meta:
        model = Problem
        fields = ("id", "number", "title", "allowed_formats", "max_file_mb", "statement_pdf")
        read_only_fields = fields

    def get_statement_pdf(self, obj: Problem) -> str | None:
        stage = self.context.get("stage") or obj.stage
        if not _statements_visible(stage, self.context):
            return None
        if not obj.statement_pdf:
            return None
        request = self.context.get("request")
        url = reverse("competitions:problem-statement", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(url) if request is not None else url


class RegistrationStatusSerializer(serializers.Serializer):
    """Stan rejestracji uczestników – kształt odpowiedzi dla ``RegistrationStatus``.

    Wyłącznie odczyt: rejestrację otwiera i zamyka koordynator w panelu, nie klient API. ``reason``
    jest kodem maszynowym (``open``/``disabled``/``not_yet``/``closed``/``delegations``), a nie
    zdaniem po polsku – tłumaczenie na komunikat należy do warstwy, która go pokazuje.
    """

    is_open = serializers.BooleanField(read_only=True)
    reason = serializers.CharField(read_only=True)
    opens_at = serializers.DateTimeField(read_only=True, allow_null=True)
    closes_at = serializers.DateTimeField(read_only=True, allow_null=True)


class CurrentEditionSerializer(serializers.ModelSerializer):
    """Bieżąca edycja: wszystkie etapy + zadania etapu bieżącego."""

    stages = PublicStageSerializer(many=True, read_only=True)
    current_stage = serializers.SerializerMethodField()
    problems = serializers.SerializerMethodField()
    # Klient (strona główna, aplikacja mobilna) musi wiedzieć, czy w ogóle pokazywać przycisk
    # rejestracji – i od kiedy. Bez tego pola jedyną drogą byłaby próba założenia konta i odczyt
    # kodu 409, czyli zapytanie o stan przez wywołanie skutku ubocznego.
    registration = serializers.SerializerMethodField()

    class Meta:
        model = Edition
        fields = (
            "id",
            "year_label",
            "is_current",
            "registration",
            "stages",
            "current_stage",
            "problems",
        )
        read_only_fields = fields

    def get_registration(self, obj: Edition) -> dict:
        # Tryb delegacji konkursu (DEL-01) zamyka rejestrację publiczną także w tej odpowiedzi.
        from .models import public_registration_status

        return RegistrationStatusSerializer(public_registration_status(obj, self.context.get("now"))).data

    def get_current_stage(self, obj: Edition) -> dict | None:
        stage = self.context.get("stage")
        return PublicStageSerializer(stage, context=self.context).data if stage else None

    def get_problems(self, obj: Edition) -> list[dict]:
        """Zadania etapu bieżącego – **dopiero po jego otwarciu** (pakiet 5 po audycie).

        Etap bieżący bywa jeszcze przed ``opens_at`` (najbliższy zapowiedziany). Do tej zmiany
        anonimowy ``GET`` oddawał wtedy listę zadań z tytułami – ``statement_pdf`` był pusty, ale sam
        tytuł („Nierówność ze średnimi”) potrafi zdradzić temat zadania przed startem zawodów.
        Reguła jest ta sama, co w części informacyjnej (``apps.cms.live_data.problems_state``):
        przed otwarciem lista jest pusta, a klient ma ``current_stage.opens_at``, żeby powiedzieć,
        kiedy się pojawi.
        """
        stage = self.context.get("stage")
        if stage is None or not _statements_visible(stage, self.context):
            return []
        problems = stage.problems.all().order_by("number")
        return PublicProblemSerializer(problems, many=True, context=self.context).data


class StageEntrySerializer(serializers.ModelSerializer):
    """Wpis uczestnika **albo drużyny** do etapu – zwracany wyłącznie właścicielowi.

    Zakres rozstrzyga queryset (``StageEntryQuerySet.for_user``), nie ten serializer.

    ``public_code`` idzie przez ``entry_owner`` (§ 1.2.3), a nie przez ``participant.public_code``:
    od etapu 2 właścicielem wpisu bywa drużyna, a wtedy kolumna ``participant`` jest pusta i pole
    oddawałoby ``null`` – czyli wpis bez identyfikatora w tabeli wyników. Dla wpisu uczestnika
    funkcja oddaje dokładnie ten sam napis, co dotąd, i nie dokłada ani jednego zapytania
    (``participant`` jest w ``select_related`` querysetu).

    ``total_points`` jest ``None`` do ogłoszenia wyników etapu (v0.38.7, decyzja właściciela:
    uczeń widzi oceny dopiero po ostatecznym zatwierdzeniu). Kolumnę zapisuje już **podgląd**
    wyników koordynatora (``compute_stage_results`` bez ``preview=True``), więc bez tej bramki API
    oddawało sumę w trakcie procedury – także w teście z ``show_results_after=NEVER``. Sygnał jest
    ten sam, co w panelu i w ``GET /api/me/results/``: ``stage.results_published_at``; etap jest
    w ``select_related`` querysetu (``entries_for_user``), więc bramka nie kosztuje zapytania.
    ``status`` zostaje bez zmian, jak w panelu: ``QUALIFIED``/``NOT_QUALIFIED`` nadaje dopiero
    publikacja (``apply_qualification`` woła tylko ``publish_results``).
    """

    stage = PublicStageSerializer(read_only=True)
    edition = serializers.CharField(source="stage.edition.year_label", read_only=True)
    public_code = serializers.SerializerMethodField()

    class Meta:
        model = StageEntry
        fields = ("id", "edition", "stage", "public_code", "status", "total_points", "created_at")
        read_only_fields = fields

    def get_public_code(self, obj: StageEntry) -> str:
        return entry_owner(obj).public_code

    def to_representation(self, instance) -> dict:
        data = super().to_representation(instance)
        if instance.stage.results_published_at is None:
            data["total_points"] = None
        window = _entry_time_window(instance, self.context)
        if window is not None:
            # Okno czasowe ucznia (TZ-01) – klucz **tylko** w etapie z oknami: odpowiedź każdego
            # innego konkursu zostaje co do klucza ta sama. ``stage.opens_at``/``deadline_at`` to
            # rama etapu; uczeń zaczyna i kończy w swoim oknie.
            data["time_window"] = {
                "label": window.window.label,
                "opens_at": window.opens_at.isoformat(),
                "deadline_at": window.deadline_at.isoformat(),
                "submission_deadline": window.submission_deadline.isoformat(),
                "extra_minutes": window.extra_minutes,
            }
        return data


def _entry_time_window(entry: StageEntry, context):
    """Okno ucznia wpisu albo ``None`` (bez flagi konkursu – bez zapytania)."""
    from apps.time_windows.access import effective_window
    from apps.time_windows.access import enabled as time_windows_enabled

    request = context.get("request")
    competition = getattr(request, "competition", None)
    if entry.participant_id is None or not time_windows_enabled(competition):
        return None
    return effective_window(entry.stage, entry.participant, competition)
