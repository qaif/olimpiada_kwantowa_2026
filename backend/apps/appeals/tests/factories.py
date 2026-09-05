"""Fabryki factory_boy dla reklamacji. Używane wyłącznie w testach."""

import factory
from django.utils import timezone

from apps.accounts.models import GROUP_APPEALS, GROUP_REVIEWER, CommitteeStatus
from apps.accounts.tests.factories import CommitteeMemberFactory, ParticipantFactory, UserFactory
from apps.appeals.models import Appeal, AppealStatus
from apps.submissions.tests.factories import SubmissionFactory

#: Uzasadnienie dłuższe niż wymagane 50 znaków – domyślne wejście testów.
VALID_ARGUMENT = (
    "Rozwiązanie zawiera pełne uzasadnienie nierówności w punkcie 3, "
    "a recenzenci pominęli krok z lematem o średnich."
)
#: Uzasadnienie krótsze niż 50 znaków – ścieżka błędu (kryterium 5).
SHORT_ARGUMENT = "Za mało punktów."


class AppealsCommitteeMemberFactory(CommitteeMemberFactory):
    """Aktywny członek komisji odwoławczej: grupy ``reviewer`` + ``appeals`` i ``is_appeals_committee``.

    Obie grupy, bo dokładnie tak wygląda profil po zatwierdzeniu (``_grant_reviewer_groups``):
    członek komisji odwoławczej jest jednocześnie recenzentem. Fabryka z samą grupą ``appeals``
    opisywała stan, którego rejestracja nie potrafi wyprodukować.
    """

    user = factory.SubFactory(UserFactory, groups=[GROUP_REVIEWER, GROUP_APPEALS])
    status = CommitteeStatus.ACTIVE
    district_verified = True
    is_appeals_committee = True


class AppealFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Appeal

    submission = factory.SubFactory(SubmissionFactory)
    filed_by = factory.SubFactory(ParticipantFactory)
    filed_at = factory.LazyFunction(timezone.now)
    argument = VALID_ARGUMENT
    status = AppealStatus.OPEN
