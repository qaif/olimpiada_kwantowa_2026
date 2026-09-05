"""Serializery API kont.

Hasła i kody zaproszeń są wyłącznie ``write_only`` – nigdy nie pojawiają się w odpowiedzi.
"""

from rest_framework import serializers

from .models import CommitteeMember, Participant, User


class ParticipantRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    school = serializers.CharField(max_length=200)
    district = serializers.CharField(max_length=100)
    birth_year = serializers.IntegerField(min_value=1900, max_value=2200)
    gdpr_consent = serializers.BooleanField()
    guardian_consent = serializers.BooleanField(required=False, default=False)


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
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)


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
    class Meta:
        model = Participant
        fields = (
            "public_code",
            "school",
            "district",
            "birth_year",
            "guardian_consent",
            "publish_full_name",
            "gdpr_consent_at",
        )


class CommitteeProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommitteeMember
        fields = ("id", "district", "district_verified", "status", "is_appeals_committee")


class VerifyDistrictSerializer(serializers.Serializer):
    """Wejście potwierdzenia okręgu przez koordynatora."""

    district = serializers.CharField(max_length=100)


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

    class Meta:
        model = CommitteeMember
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "district",
            "district_verified",
            "status",
            "is_appeals_committee",
            "created_at",
        )
