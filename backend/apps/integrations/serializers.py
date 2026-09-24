"""Kształt odpowiedzi publicznego API ``/api/v1/``.

Nazwy pól są **angielskie**, tak jak w dotychczasowym API platformy: po drugiej stronie stoi
program, a nie człowiek, a mieszanka języków w jednym schemacie jest gorsza niż dowolny z nich
konsekwentnie. Opisy (``help_text``) zostają po polsku – czyta je koordynator w ``/api/docs/``.

Serializery mają przedrostek ``V1``, bo nazwa klasy staje się nazwą komponentu w OpenAPI:
``StageSerializer`` istnieje już w ``apps.competitions`` i dwa komponenty „Stage” zderzyłyby się
w jednym schemacie. Przedrostek jest przy okazji obietnicą: kształt ``V1…`` nie zmieni się bez
zmiany numeru wersji w adresie.

Dane osobowe mają **osobny** serializer (``V1ParticipantPiiSerializer``), a nie pola dokładane
warunkowo do jednego. Tak widać w schemacie, że to dwie różne odpowiedzi, a widok nie ma miejsca,
w którym „zapomniałby” sprawdzić zakres: wybór klasy **jest** sprawdzeniem.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.competitions.models import Edition, Stage
from apps.core.points_api import PointsDictField, PointsField

from .models import SCOPES, WEBHOOK_EVENTS


class V1EditionSerializer(serializers.ModelSerializer):
    """Edycja olimpiady: oznaczenie roku i okno rejestracji."""

    class Meta:
        model = Edition
        fields = [
            "id",
            "year_label",
            "is_current",
            "created_at",
            "registration_enabled",
            "registration_opens_at",
            "registration_closes_at",
        ]


class V1StageSerializer(serializers.ModelSerializer):
    """Etap edycji razem z całym kalendarzem, którego serwer pilnuje."""

    edition_id = serializers.IntegerField(read_only=True)
    display_name = serializers.CharField(read_only=True)
    problem_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Stage
        fields = [
            "id",
            "edition_id",
            "kind",
            "name",
            "display_name",
            "format",
            "location",
            "opens_at",
            "deadline_at",
            "grace_seconds",
            "review_deadline_at",
            "appeal_window_opens_at",
            "appeal_window_closes_at",
            "results_published_at",
            "closed_at",
            "problem_count",
        ]


class V1ParticipantSerializer(serializers.Serializer):
    """Uczestnik etapu bez danych osobowych: kod publiczny, szkoła, województwo, klasa, stan.

    Kod publiczny jest tu jedynym identyfikatorem osoby i taki jest zamysł – to ten sam
    pseudonim, którym posługują się ogłoszone wyniki.
    """

    public_code = serializers.CharField(source="participant.public_code")
    school = serializers.CharField(source="participant.school")
    school_city = serializers.SerializerMethodField()
    voivodeship = serializers.CharField(source="participant.district")
    grade = serializers.IntegerField(source="participant.grade", allow_null=True)
    status = serializers.CharField()
    registered_at = serializers.DateTimeField(source="created_at")

    def get_school_city(self, entry) -> str:
        """Miejscowość szkoły – wyłącznie ze słownika SIO.

        Szkoła wpisana ręcznie nie ma miejscowości jako osobnej danej (jest częścią napisu),
        a wyciąganie jej z nazwy zgadywaniem dawałoby pole, któremu nie wolno ufać.
        """
        school = getattr(entry.participant, "school_ref", None)
        return school.city if school is not None else ""


class V1ParticipantPiiSerializer(V1ParticipantSerializer):
    """To samo plus dane osobowe – wyłącznie dla klucza z ``read:participants_pii`` i zgodą PII."""

    first_name = serializers.CharField(source="participant.user.first_name")
    last_name = serializers.CharField(source="participant.user.last_name")
    email = serializers.EmailField(source="participant.user.email")


class V1ResultRowSerializer(serializers.Serializer):
    """Wiersz ogłoszonej tabeli – przepisany ze **zamrożonego** snapshotu publikacji.

    ``display`` jest gotowym napisem (kod, inicjały ze szkołą albo pełne nazwisko – zależnie od
    trybu anonimizacji wybranego przy publikacji) i nie da się go rozłożyć na części. Tak samo
    widzi go publiczna tabela wyników; API nie ma prawa wiedzieć więcej niż strona.
    """

    rank = serializers.IntegerField()
    display = serializers.CharField()
    points = PointsDictField(help_text="Numer zadania → punkty (liczba, najwyżej 2 miejsca po przecinku).")
    total = PointsField()
    qualified = serializers.BooleanField()
    manual = serializers.BooleanField(help_text="Czy o wierszu rozstrzygnęła decyzja komitetu.")
    voivodeship = serializers.CharField(
        required=False, help_text="Wyłącznie w tabelach anonimizowanych kodem."
    )


class V1ResultsSerializer(serializers.Serializer):
    """Nagłówek ogłoszonych wyników etapu. Same wiersze idą stronicowaną listą obok."""

    stage_id = serializers.IntegerField()
    edition_id = serializers.IntegerField()
    published_at = serializers.DateTimeField()
    anonymization = serializers.CharField()
    count = serializers.IntegerField()


class V1ResultsPageSerializer(serializers.Serializer):
    """Strona tabeli wyników: standardowa koperta stronicowania plus nagłówek publikacji.

    Nagłówek jest przy **każdej** stronie, a nie tylko przy pierwszej: klient, który pobiera
    trzecią stronę po godzinie, musi wiedzieć, z której publikacji pochodzą wiersze, które
    właśnie dostał – inaczej sklejałby tabelę sprzed i po decyzji komisji.
    """

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    stage = V1ResultsSerializer()
    results = V1ResultRowSerializer(many=True)


class V1SubmissionSerializer(serializers.Serializer):
    """Metryczka oddanej pracy. Bez pliku, bez nazwy pliku, bez skrótu i bez punktów."""

    id = serializers.IntegerField()
    participant_code = serializers.CharField(source="entry.participant.public_code")
    problem_number = serializers.IntegerField(source="problem.number")
    version = serializers.IntegerField()
    status = serializers.CharField()
    is_late = serializers.BooleanField()
    submitted_at = serializers.DateTimeField()


class V1ProblemDistributionSerializer(serializers.Serializer):
    """Histogram punktów jednego zadania w ogłoszonym etapie."""

    number = serializers.CharField()
    total = serializers.IntegerField()
    bars = serializers.ListField(child=serializers.DictField())


class V1StatsSerializer(serializers.Serializer):
    """Statystyki jednego ogłoszonego etapu – te same liczby, co na stronie ``/statystyki/``."""

    stage_id = serializers.IntegerField()
    stage_name = serializers.CharField()
    edition = serializers.CharField()
    is_training = serializers.BooleanField()
    published_at = serializers.DateTimeField()
    participants = serializers.IntegerField()
    problems = V1ProblemDistributionSerializer(many=True)
    mean = serializers.FloatField(allow_null=True)
    median = serializers.FloatField(allow_null=True)
    max_total = PointsField(allow_null=True)
    qualified = serializers.IntegerField()
    threshold = PointsField(allow_null=True)
    districts = serializers.ListField(child=serializers.DictField())


class V1EventCreateSerializer(serializers.Serializer):
    """Wydarzenie dopisywane do linii czasu edycji (zakres ``write:events``).

    Walidacja jest tu **wstępna** (typy i obecność pól) – regułę domenową („koniec nie przed
    początkiem”, dozwolone schematy odnośnika) rozstrzyga ``apps.competitions.events``, czyli
    dokładnie ten sam serwis, który obsługuje formularz w panelu. Dwie kopie tych reguł
    rozjechałyby się przy pierwszej zmianie.
    """

    edition_id = serializers.IntegerField(
        required=False, help_text="Pomijalne dla klucza przypisanego do edycji."
    )
    title = serializers.CharField(max_length=80)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    url = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    show_on_timeline = serializers.BooleanField(required=False, default=True)


class V1EventSerializer(serializers.Serializer):
    """Zapisane wydarzenie – odpowiedź na ``POST /api/v1/events/``."""

    id = serializers.IntegerField()
    edition_id = serializers.IntegerField()
    title = serializers.CharField()
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(allow_null=True)
    note = serializers.CharField()
    url = serializers.CharField()
    show_on_timeline = serializers.BooleanField()


class V1CapabilitiesSerializer(serializers.Serializer):
    """Odpowiedź ``GET /api/v1/`` – co ten klucz może i w której edycji.

    Endpoint istnieje po to, żeby integrator nie musiał zgadywać, czy dostał klucz, o który
    prosił: jedno żądanie mówi mu, jakie ma zakresy i czy jest zawężony do jednej edycji.
    Odpowiedź niesie też spis zakresów i zdarzeń, żeby dokumentacja i system nie mogły się
    rozjechać bez konsekwencji.
    """

    key_prefix = serializers.CharField()
    name = serializers.CharField()
    edition_id = serializers.IntegerField(allow_null=True)
    scopes = serializers.ListField(child=serializers.CharField())
    pii_allowed = serializers.BooleanField()
    rate_limit_per_minute = serializers.IntegerField()
    available_scopes = serializers.DictField(child=serializers.CharField(), default=dict(SCOPES))
    webhook_events = serializers.DictField(child=serializers.CharField(), default=dict(WEBHOOK_EVENTS))
