"""Fabryki factory_boy dla kont. Używane wyłącznie w testach.

Profile ról (``Participant``, ``CommitteeMember``, ``InvitationCode``) dostają od zadania T7
argument ``competition`` z wartością domyślną z kontekstu – uzasadnienie i mechanika:
``apps/tenancy/tests/factories.py``. Samo konto (``User``) zakresu **nie** ma i mieć nie będzie:
jedna osoba ma jedno hasło i jeden reset hasła na całą platformę, a rolą w konkursie jest dopiero
członkostwo (``docs/UNIWERSALNY-ETAP-1.md`` § 3.8).
"""

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
from apps.tenancy.tests.factories import CompetitionScopedFactory

DEFAULT_PASSWORD = "Poprawne-Haslo-2026"

#: Numer w postaci, jaką zapisuje ``apps.accounts.phones.normalize_phone`` – testy, które numeru
#: nie badają, mają go podać raz i o nim zapomnieć.
DEFAULT_PHONE = "+48600100200"


def activate(user):
    """Przechodzi aktywację konta e-mailem **za** uczestnika.

    Do użytku w testach, których przedmiotem jest cokolwiek **po** rejestracji (logowanie, panel,
    zgłoszenie do etapu). Konto zakładane serwisem rejestracji jest od tej zmiany nieaktywne
    i logowanie go nie wpuszcza – helper zdejmuje tę przeszkodę tą samą drogą, którą przechodzi
    kliknięcie linku z listu, więc test nie osłabia reguły, tylko ją przechodzi.
    """
    from apps.accounts.activation import mark_activated

    return mark_activated(user)


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
    # Konto z fabryki jest kontem **działającym**: potwierdzony adres e-mail i ``is_active=True``.
    # Inaczej każdy test logowania zaczynałby się od aktywacji, a kosiarka kont nieaktywowanych
    # (``apps.accounts.tasks``) traktowałaby te konta jak porzucone rejestracje. Test, którego
    # przedmiotem jest **aktywacja**, podaje ``email_verified_at=None, is_active=False`` jawnie.
    email_verified_at = factory.LazyFunction(timezone.now)

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        if not create:
            return
        obj.set_password(extracted or DEFAULT_PASSWORD)
        obj.save(update_fields=["password"])

    @factory.post_generation
    def groups(obj, create, extracted, **kwargs):
        _assign_groups(obj, create, extracted)


class ParticipantFactory(CompetitionScopedFactory):
    """Profil uczestnika **jednego** konkursu (§ 3.3): szkoła, klasa, ``public_code`` i zgody.

    Konto (``user``) zostaje wspólne dla platformy, więc podfabryka konta konkursu nie dostaje.
    """

    class Meta:
        model = Participant

    user = factory.SubFactory(UserFactory, groups=[GROUP_PARTICIPANT])
    # Prefiks kodu bierze się z konkursu profilu, dokładnie tak jak w serwisie rejestracji: test
    # konkursu z własnym prefiksem ma dostać kod z tym prefiksem bez podawania go ręcznie.
    public_code = factory.LazyAttribute(lambda obj: generate_public_code(obj.competition))
    school = "LO nr 1"
    # Szkoła wpisana ręcznie (``school_ref`` puste) – wariant, który działa bez słownika.
    grade = 3
    # Wartość z ``Voivodeship`` – po zamknięciu listy każdy inny zapis odpada na walidacji.
    # Stała, a nie losowana: testy konfliktu interesów porównują województwa między obiektami
    # i muszą mieć powtarzalny punkt wyjścia (własne województwo podają jawnie).
    district = Voivodeship.MAZOWIECKIE
    birth_year = 2008
    # Numer z zakresu testowego w postaci już znormalizowanej – dokładnie takiej, jaką zapisuje
    # ``apps.accounts.phones.normalize_phone``.
    phone = "+48600000000"
    gdpr_consent_at = factory.LazyFunction(timezone.now)
    terms_accepted_at = factory.LazyFunction(timezone.now)
    guardian_consent = True


class CommitteeMemberFactory(CompetitionScopedFactory):
    """Członek komitetu **tego** konkursu – razem z województwem i przynależnością do komisji."""

    class Meta:
        model = CommitteeMember

    user = factory.SubFactory(UserFactory)
    district = Voivodeship.MAZOWIECKIE
    district_verified = False
    status = CommitteeStatus.PENDING
    is_appeals_committee = False


class ActiveReviewerFactory(CommitteeMemberFactory):
    """Recenzent aktywny: status ACTIVE + grupa ``reviewer`` (tak jak po zatwierdzeniu).

    Województwo jest oznaczone jako pochodzące od organizatora (``district_verified=True``), bo to
    przypadek domyślny: kod zaproszenia z województwem. Flaga nie wpływa na przydział – reguła
    konfliktu interesów patrzy wyłącznie na samo ``district`` – więc test, który bada województwo
    samodeklarowane albo puste, podaje wartości jawnie.
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


class InvitationCodeFactory(CompetitionScopedFactory):
    """Fabryka kodu zaproszenia. Kod jawny wstrzykuje się parametrem ``plain_code``.

    Kod nadaje status w komitecie **konkursu**, więc od T2 należy do konkursu (§ 3.2).
    """

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
