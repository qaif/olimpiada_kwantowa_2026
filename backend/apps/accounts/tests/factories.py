"""Fabryki factory_boy dla kont. Używane wyłącznie w testach."""

from datetime import timedelta

import factory
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.models import (
    GROUP_COORDINATOR,
    GROUP_PARTICIPANT,
    GROUP_REVIEWER,
    CommitteeMember,
    CommitteeStatus,
    InvitationCode,
    InvitationGrantsStatus,
    Participant,
    User,
    Voivodeship,
    generate_public_code,
    hash_invitation_code,
)

DEFAULT_PASSWORD = "Poprawne-Haslo-2026"


def _assign_groups(user, create, names):
    if not create or not names:
        return
    for name in names:
        group, _ = Group.objects.get_or_create(name=name)
        user.groups.add(group)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@example.test")
    first_name = "Jan"
    last_name = "Kowalski"

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        if not create:
            return
        obj.set_password(extracted or DEFAULT_PASSWORD)
        obj.save(update_fields=["password"])

    @factory.post_generation
    def groups(obj, create, extracted, **kwargs):
        _assign_groups(obj, create, extracted)


class ParticipantFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Participant

    user = factory.SubFactory(UserFactory, groups=[GROUP_PARTICIPANT])
    public_code = factory.LazyFunction(generate_public_code)
    school = "LO nr 1"
    # Szkoła wpisana ręcznie (``school_ref`` puste) – wariant, który działa bez słownika.
    grade = 3
    # Wartość z ``Voivodeship`` – po zamknięciu listy każdy inny zapis odpada na walidacji.
    # Stała, a nie losowana: testy konfliktu okręgu porównują okręgi między obiektami
    # i muszą mieć powtarzalny punkt wyjścia (własny okręg podają jawnie).
    district = Voivodeship.MAZOWIECKIE
    birth_year = 2008
    gdpr_consent_at = factory.LazyFunction(timezone.now)
    guardian_consent = True


class CommitteeMemberFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CommitteeMember

    user = factory.SubFactory(UserFactory)
    district = Voivodeship.MAZOWIECKIE
    district_verified = False
    status = CommitteeStatus.PENDING
    is_appeals_committee = False


class ActiveReviewerFactory(CommitteeMemberFactory):
    """Recenzent aktywny: status ACTIVE + grupa ``reviewer`` (tak jak po zatwierdzeniu).

    Okręg jest zweryfikowany, bo to przypadek domyślny po wdrożeniu długu T-02 (kod zaproszenia
    z okręgiem). Test konfliktu interesów jawnie podaje ``district_verified=False``, gdy bada
    ścieżkę niezweryfikowaną.
    """

    user = factory.SubFactory(UserFactory, groups=[GROUP_REVIEWER])
    status = CommitteeStatus.ACTIVE
    district_verified = True


class PendingReviewerFactory(CommitteeMemberFactory):
    """Recenzent oczekujący: profil PENDING, ale już w grupie ``reviewer`` (przypadek brzegowy)."""

    user = factory.SubFactory(UserFactory, groups=[GROUP_REVIEWER])
    status = CommitteeStatus.PENDING


class CoordinatorFactory(UserFactory):
    """Koordynator. ``groups`` musi zostać nadpisane deklaracją post-generation, nie zwykłą listą."""

    groups = factory.PostGeneration(
        lambda obj, create, extracted, **kwargs: _assign_groups(obj, create, extracted or [GROUP_COORDINATOR])
    )


class InvitationCodeFactory(factory.django.DjangoModelFactory):
    """Fabryka kodu zaproszenia. Kod jawny wstrzykuje się parametrem ``plain_code``."""

    class Meta:
        model = InvitationCode
        exclude = ("plain_code",)

    plain_code = "kod-testowy-0001"
    code_hash = factory.LazyAttribute(lambda obj: hash_invitation_code(obj.plain_code))
    created_by = factory.SubFactory(CoordinatorFactory)
    expires_at = factory.LazyFunction(lambda: timezone.now() + timedelta(days=7))
    max_uses = 1
    used_count = 0
    grants_status = InvitationGrantsStatus.ACTIVE
    is_appeals = False
    district = None
