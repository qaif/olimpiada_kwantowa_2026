"""Serializery API kont.

Hasła i kody zaproszeń są wyłącznie ``write_only`` – nigdy nie pojawiają się w odpowiedzi.
"""

from rest_framework import serializers

from .models import GRADE_CHOICES, CommitteeMember, Participant, User, Voivodeship


class ParticipantRegisterSerializer(serializers.Serializer):
    """Wejście ``POST /api/auth/register/participant/``.

    Szkoła ma dwie postacie i **żadna nie jest wymagana osobno**: ``school_id`` wskazuje wiersz
    słownika (``GET /api/schools/``), ``school`` jest wolnym tekstem dla szkoły spoza wykazu.
    Wymagana jest co najmniej jedna – dzięki temu klient sprzed wprowadzenia słownika, który zna
    tylko ``school``, działa bez zmian. Podanie obu nie jest błędem: wygrywa ``school_id``, a
    nazwa i tak zostanie przepisana z rejestru (patrz ``accounts.services._resolve_school``).
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    school = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    school_id = serializers.IntegerField(min_value=1, required=False, allow_null=True, default=None)
    grade = serializers.ChoiceField(choices=GRADE_CHOICES)
    district = serializers.ChoiceField(choices=Voivodeship.choices)
    birth_year = serializers.IntegerField(min_value=1900, max_value=2200)
    gdpr_consent = serializers.BooleanField()
    guardian_consent = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs.get("school_id") is None and not (attrs.get("school") or "").strip():
            raise serializers.ValidationError(
                {"school": "Podaj identyfikator szkoły z rejestru (school_id) albo jej nazwę (school)."}
            )
        return attrs


class ParticipantRegisteredSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = Participant
        fields = ("id", "email", "public_code")


class CommitteeRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    invitation_code = serializers.CharField(write_only=True, max_length=128)
    district = serializers.ChoiceField(
        choices=Voivodeship.choices, required=False, allow_blank=True, allow_null=True
    )


class CommitteeRegisteredSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = CommitteeMember
        fields = ("id", "email", "status")


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(read_only=True)


class ParticipantProfileSerializer(serializers.ModelSerializer):
    # Wartość ``district`` jest slugiem ASCII (stabilnym dla klientów), etykieta z diakrytykami
    # jedzie obok – żeby front nie musiał utrzymywać własnej kopii słownika województw.
    district_label = serializers.CharField(source="get_district_display", read_only=True)

    class Meta:
        model = Participant
        fields = (
            "public_code",
            "school",
            # Identyfikator wiersza słownika albo ``null`` dla szkoły wpisanej ręcznie. ``school``
            # jest wypełnione zawsze, więc klient, którego słownik nie interesuje, może ten klucz
            # zignorować i nadal ma nazwę do pokazania.
            "school_ref",
            "grade",
            "district",
            "district_label",
            "birth_year",
            "guardian_consent",
            "publish_full_name",
            "gdpr_consent_at",
        )


class CommitteeProfileSerializer(serializers.ModelSerializer):
    district_label = serializers.CharField(source="get_district_display", read_only=True)

    class Meta:
        model = CommitteeMember
        fields = (
            "id",
            "district",
            "district_label",
            "district_verified",
            "status",
            "is_appeals_committee",
        )


class VerifyDistrictSerializer(serializers.Serializer):
    """Wejście potwierdzenia okręgu przez koordynatora."""

    district = serializers.ChoiceField(choices=Voivodeship.choices)


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    participant = serializers.SerializerMethodField()
    committee = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "email", "first_name", "last_name", "roles", "participant", "committee")

    def get_roles(self, obj: User) -> list[str]:
        return obj.role_names

    def get_participant(self, obj: User) -> dict | None:
        profile = getattr(obj, "participant", None)
        return ParticipantProfileSerializer(profile).data if profile else None

    def get_committee(self, obj: User) -> dict | None:
        profile = getattr(obj, "committee_member", None)
        return CommitteeProfileSerializer(profile).data if profile else None


class PendingCommitteeMemberSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    district_label = serializers.CharField(source="get_district_display", read_only=True)

    class Meta:
        model = CommitteeMember
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "district",
            "district_label",
            "district_verified",
            "status",
            "is_appeals_committee",
            "created_at",
        )
