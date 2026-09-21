"""Reguły forum: kto może pisać, w jakim stanie rodzi się wpis i co zostawia moderacja.

Ten plik pilnuje **serwisu**, a nie ekranu: widoki forum mają własną suitę
(``apps/web/tests/test_forum.py``, ``test_coordinator_forum.py``). Podział jest taki sam, jak przy
zgłoszeniach do organizatora, i z tego samego powodu: reguła „wpis czeka na zatwierdzenie” ma być
prawdą także wtedy, gdy kiedyś powstanie API albo import – a test przez ``client.post`` sprawdzałby
przy okazji formularz, szablon i adres, czyli trzy rzeczy naraz i żadnej dokładnie.

Trzy rzeczy, o które chodzi tu najbardziej, bo są regułami bezpieczeństwa, a nie funkcjami:

- **wymuszona moderacja wstępna w czasie otwartego etapu** – regulamin § 10 ust. 2 i § 17,
- **izolacja konkursów** – rozmowa jednej olimpiady nie istnieje dla drugiej,
- **podpis autora** – nigdy adres e-mail, szkoła ani kod ``OLM-…`` (klucz anonimowego oceniania).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import CompetitionRole
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.competitions.models import TRAINING_DEADLINE, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.forum import services
from apps.forum.models import (
    EDIT_WINDOW_MINUTES,
    MAX_POST_LENGTH,
    ForumCategory,
    ForumPost,
    ModerationMode,
    ModerationStatus,
    display_author,
)
from apps.forum.tests.factories import (
    ForumCategoryFactory,
    ForumPostFactory,
    ForumThreadFactory,
)
from apps.tenancy.tests.factories import grant_membership

pytestmark = pytest.mark.django_db


def participant_of(competition, **kwargs):
    """Aktywny uczestnik **tego** konkursu – konto razem z rolą, bo forum wymaga obu."""
    profile = ParticipantFactory(competition=competition, **kwargs)
    grant_membership(profile.user, competition, CompetitionRole.PARTICIPANT)
    return profile


def coordinator_of(competition):
    """Koordynator **tego** konkursu. Fabryka nadaje grupę, ``grant_membership`` – rolę w konkursie.

    Oba naraz, bo ``has_role`` czyta jedno albo drugie zależnie od tego, czy model członkostw już
    istnieje – i tak samo robią pozostałe suity panelu.
    """
    user = CoordinatorFactory()
    grant_membership(user, competition, CompetitionRole.COORDINATOR)
    return user


def with_forum(competition):
    """Konkurs z **włączonym** forum – tak, jak zrobi to organizator w panelu.

    W pamięci, bo ``has_feature`` czyta pole wiersza, który wołający już trzyma.
    """
    competition.feature_flags = {**(competition.feature_flags or {}), "participant_forum": True}
    return competition


def closed_edition(competition):
    """Bieżąca edycja z etapem **zamkniętym** – stan spoza okna przyjmowania rozwiązań.

    Etap przesuwamy w przeszłość zamiast go nie tworzyć, bo edycja bez ani jednego etapu jest
    stanem, którego w zawodach nie ma, a test ma opisywać forum po deadline'cie, a nie przed
    założeniem etapów.
    """
    edition = CurrentEditionFactory(competition=competition)
    past = timezone.now() - timedelta(days=30)
    StageFactory(
        competition=competition,
        edition=edition,
        opens_at=past,
        deadline_at=past + timedelta(days=1),
    )
    return edition


def open_edition(competition):
    """Bieżąca edycja z etapem **przyjmującym rozwiązania teraz** (fabryka tak właśnie ustawia)."""
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(competition=competition, edition=edition)
    return edition


def training_stage_for(edition):
    """Etap treningowy edycji, taki, jaki zostawia ``seed_training_problems``: otwarty bez końca.

    Ta sama fabryka, co w ``apps.web.tests.test_training`` – piaskownica ma sentynel
    ``TRAINING_DEADLINE`` (rok 2099) zamiast realnego terminu, więc ``is_open_for_submissions``
    zwraca dla niej ``True`` na zawsze, dopóki jej ktoś nie zamknie ręcznie.
    """
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        opens_at=timezone.now() - timedelta(hours=1),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
    )
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


# --- podpis autora -------------------------------------------------------------------------------


def test_the_signature_is_the_first_name_and_the_initial_of_the_surname():
    user = UserFactory(first_name="Anna", last_name="Nowakowska")

    assert display_author(user) == "Anna N."


def test_the_signature_of_a_deleted_account_says_so_instead_of_being_empty():
    """Puste miejsce pod wpisem wyglądałoby jak wpis organizatora, a nie jak brak autora."""
    assert display_author(None) == "Użytkownik usunięty"


def test_an_anonymised_account_gets_the_same_signature_as_a_deleted_one(competition):
    """Anonimizacja wyciera imię i nazwisko, a wiersz konta zostawia – podpis ma zniknąć tak samo."""
    profile = participant_of(competition, user__first_name="", user__last_name="")

    assert display_author(profile.user) == "Użytkownik usunięty"


def test_the_signature_never_carries_the_e_mail_the_school_or_the_public_code(competition):
    """Trzy rzeczy, których na forum nie ma, i jedna, przez którą by się przedostały.

    Kod publiczny jest kluczem anonimowego oceniania: jeden wątek „cześć, jestem Ania OLM-XXXXXX”
    wystarczyłby, żeby powiązanie kod → osoba stało się publiczne dla wszystkich naraz.
    """
    profile = participant_of(
        competition,
        user__first_name="Ania",
        user__last_name="Testowa",
        user__email="ania@example.invalid",
        school="LO im. Testowego",
    )

    signature = display_author(profile.user)

    assert signature == "Ania T."
    assert profile.user.email not in signature
    assert profile.school not in signature
    assert profile.public_code not in signature


# --- dostęp ---------------------------------------------------------------------------------------


def test_a_participant_of_this_competition_may_read(competition):
    profile = participant_of(competition)

    assert services.can_read(profile.user, competition) is True


def test_a_participant_of_another_competition_may_not_read(competition, other_competition):
    """Rola jest zawsze rolą **w konkursie** – forum sąsiada nie jest niczyim forum."""
    stranger = participant_of(other_competition)

    assert services.can_read(stranger.user, competition) is False


def test_an_account_without_a_role_may_not_read(competition):
    assert services.can_read(UserFactory(), competition) is False


def test_an_inactive_account_may_not_read(competition):
    """Konto bez potwierdzonego adresu jest kontem, o którym nie wiadomo, czyje jest."""
    profile = participant_of(competition)
    profile.user.is_active = False

    assert services.can_read(profile.user, competition) is False


def test_the_coordinator_may_read_without_being_a_participant(competition):
    assert services.can_read(coordinator_of(competition), competition) is True


def test_the_coordinator_post_carries_a_badge(competition):
    assert services.author_badge(coordinator_of(competition), competition) == "Organizator"


def test_a_participant_post_carries_no_badge(competition):
    """Odznaka odróżnia zdanie kolegi od zdania osoby rozstrzygającej o zawodach – i tylko to."""
    profile = participant_of(competition)

    assert services.author_badge(profile.user, competition) == ""


# --- tryb moderacji ------------------------------------------------------------------------------


def test_a_competition_without_settings_is_pre_moderated(competition):
    """Brak wiersza ustawień znaczy „domyślne”, a domyślne jest ciche: forum bez moderatora
    przy klawiaturze ma być zamknięte, a nie otwarte."""
    closed_edition(competition)

    assert services.effective_mode(competition) == ModerationMode.PRE


def test_the_post_moderation_setting_publishes_at_once(competition):
    closed_edition(competition)
    services.save_settings(
        competition=competition,
        actor=coordinator_of(competition),
        mode=ModerationMode.POST,
        is_read_only=False,
    )

    assert services.effective_mode(competition) == ModerationMode.POST
    assert services.initial_status(competition) == ModerationStatus.PUBLISHED


def test_an_open_stage_forces_pre_moderation_over_the_setting(competition):
    """**Najważniejszy test tego pliku.**

    Konkurs ustawił „po publikacji”, ale etap przyjmuje rozwiązania – i wtedy ustawienie nie
    obowiązuje. Odwrócenie tej reguły kosztowałoby unieważnienie etapu (§ 10 ust. 2, § 17), a nie
    jeden nieprzyjemny wątek.
    """
    open_edition(competition)
    services.save_settings(
        competition=competition,
        actor=coordinator_of(competition),
        mode=ModerationMode.POST,
        is_read_only=False,
    )

    assert services.stage_forcing_pre_moderation(competition) is not None
    assert services.effective_mode(competition) == ModerationMode.PRE
    assert services.initial_status(competition) == ModerationStatus.PENDING


def test_the_forced_mode_names_the_stage_so_the_warning_can_quote_it(competition):
    """Zwracamy etap, a nie ``True``: „trwa etap” bez nazwy nie mówi, czego nie wolno omawiać."""
    edition = open_edition(competition)

    stage = services.stage_forcing_pre_moderation(competition)

    assert stage is not None
    assert stage.edition_id == edition.pk


def test_a_stage_of_another_competition_does_not_force_our_mode(competition, other_competition):
    closed_edition(competition)
    open_edition(other_competition)

    assert services.stage_forcing_pre_moderation(competition) is None


def test_a_training_stage_does_not_force_pre_moderation_forever(competition):
    """Trening jest otwarty bez końca (sentynel 2099) – ta sama reguła i ten sam powód, co przy
    pomijaniu treningu w ``apps.competitions.services.current_stage``: gdyby liczył się tutaj,
    forum zamykałoby się w ``PRE`` na stałe, niezależnie od ustawienia koordynatora."""
    edition = CurrentEditionFactory(competition=competition)
    training_stage_for(edition)
    services.save_settings(
        competition=competition,
        actor=coordinator_of(competition),
        mode=ModerationMode.POST,
        is_read_only=False,
    )

    assert services.stage_forcing_pre_moderation(competition) is None
    assert services.effective_mode(competition) == ModerationMode.POST


def test_a_training_stage_alongside_a_real_open_stage_names_the_real_stage(competition):
    """Trening obok prawdziwych zawodów nie przesłania ich: wymuszenie ``PRE`` ma wskazać etap,
    którego rozwiązań regulamin faktycznie broni (§ 10 ust. 2, § 17), a nie piaskownicę."""
    edition = open_edition(competition)
    training_stage_for(edition)

    forcing = services.stage_forcing_pre_moderation(competition)

    assert forcing is not None
    assert forcing.kind != StageKind.TRAINING
    assert services.effective_mode(competition) == ModerationMode.PRE


# --- pisanie ---------------------------------------------------------------------------------------


def test_a_new_thread_under_pre_moderation_waits_together_with_its_first_post(competition):
    closed_edition(competition)
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition)

    thread = services.create_thread(
        user=profile.user,
        competition=competition,
        category=category,
        title="Pytanie o terminy",
        body="Kiedy ogłaszacie wyniki?",
    )

    assert thread.status == ModerationStatus.PENDING
    # Wątek bez wpisu nie jest wątkiem: moderator nie ma czego ocenić, a czytelnik zastaje pustą
    # stronę. Oba wiersze powstają w jednej transakcji i dostają ten sam stan.
    assert thread.posts.get().status == ModerationStatus.PENDING


def test_a_thread_in_a_category_of_another_competition_is_not_found(competition, other_competition):
    """Kategoria sąsiada nie jest „nieprawidłowym wyborem”, tylko adresem, którego tu nie ma."""
    profile = participant_of(competition)
    stranger_category = ForumCategoryFactory(competition=other_competition)

    with pytest.raises(DomainError) as error:
        services.create_thread(
            user=profile.user,
            competition=competition,
            category=stranger_category,
            title="Temat",
            body="Treść",
        )

    assert error.value.status_code == 404


def test_a_closed_category_takes_no_new_threads(competition):
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition, is_open=False)

    with pytest.raises(DomainError):
        services.create_thread(
            user=profile.user, competition=competition, category=category, title="T", body="B"
        )


def test_a_read_only_forum_takes_no_new_posts(competition):
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    services.save_settings(
        competition=competition,
        actor=coordinator_of(competition),
        mode=ModerationMode.PRE,
        is_read_only=True,
    )

    with pytest.raises(DomainError) as error:
        services.reply(user=profile.user, competition=competition, thread=thread, body="Cześć")

    assert error.value.machine_code == "FORUM_READ_ONLY"


def test_a_locked_thread_takes_no_replies(competition):
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition, is_locked=True)

    with pytest.raises(DomainError):
        services.reply(user=profile.user, competition=competition, thread=thread, body="Cześć")


def test_a_post_longer_than_the_limit_is_rejected_not_truncated(competition):
    """Obcięty wpis kończyłby się w połowie zdania – i nikt by nie wiedział, że go obcięto."""
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)

    with pytest.raises(DomainError) as error:
        services.reply(
            user=profile.user, competition=competition, thread=thread, body="x" * (MAX_POST_LENGTH + 1)
        )

    assert error.value.machine_code == "BODY_TOO_LONG"


def test_a_pending_reply_does_not_raise_the_thread_in_the_list(competition):
    """Kolejność działu nie może zdradzać, gdzie ktoś właśnie coś napisał, zanim to przeczytano."""
    closed_edition(competition)
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    before = thread.last_activity_at

    services.reply(user=profile.user, competition=competition, thread=thread, body="Nowy wpis")

    thread.refresh_from_db()
    assert thread.last_activity_at == before


# --- poprawka i usunięcie własnego wpisu ---------------------------------------------------------


def test_the_author_may_fix_a_post_inside_the_window(competition):
    closed_edition(competition)
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, body="Pierwotna treść")

    services.edit_post(post=post, user=profile.user, body="Poprawiona treść")

    post.refresh_from_db()
    assert post.body == "Poprawiona treść"
    assert post.edited_at is not None


def test_the_edit_window_closes_after_a_quarter_of_an_hour(competition):
    """Wpis sprzed godziny, który brzmi inaczej niż odpowiedź pod nim, jest gorszy niż literówka."""
    profile = participant_of(competition)
    old = timezone.now() - timedelta(minutes=EDIT_WINDOW_MINUTES + 1)
    post = ForumPostFactory(competition=competition, author=profile.user, created_at=old)

    assert services.can_edit(post, profile.user) is False
    with pytest.raises(DomainError) as error:
        services.edit_post(post=post, user=profile.user, body="Podmiana")

    assert error.value.machine_code == "FORUM_EDIT_WINDOW_CLOSED"


def test_nobody_may_fix_a_post_that_is_not_theirs(competition):
    profile = participant_of(competition)
    stranger = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user)

    assert services.can_edit(post, stranger.user) is False


def test_fixing_a_published_post_under_pre_moderation_sends_it_back_to_the_queue(competition):
    """Bez tego wystarczyłoby napisać zdanie nijakie, doczekać zatwierdzenia i podmienić treść."""
    closed_edition(competition)
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, status=ModerationStatus.PUBLISHED)

    services.edit_post(post=post, user=profile.user, body="Zupełnie inna treść")

    post.refresh_from_db()
    assert post.status == ModerationStatus.PENDING
    assert post.moderated_at is None


def test_fixing_a_post_under_post_moderation_leaves_it_visible(competition):
    closed_edition(competition)
    profile = participant_of(competition)
    services.save_settings(
        competition=competition,
        actor=coordinator_of(competition),
        mode=ModerationMode.POST,
        is_read_only=False,
    )
    post = ForumPostFactory(competition=competition, author=profile.user, status=ModerationStatus.PUBLISHED)

    services.edit_post(post=post, user=profile.user, body="Poprawka")

    post.refresh_from_db()
    assert post.status == ModerationStatus.PUBLISHED


def test_deleting_an_own_post_hides_it_instead_of_dropping_the_row(competition):
    """Miękko z dwóch powodów: odpowiedzi pod wpisem i zgłoszenie, które nie może zniknąć
    na żądanie autora."""
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user)

    services.delete_own_post(post=post, user=profile.user)

    post.refresh_from_db()
    assert post.status == ModerationStatus.HIDDEN
    assert ForumPost.objects.filter(pk=post.pk).exists()


def test_deleting_somebody_elses_post_is_refused(competition):
    profile = participant_of(competition)
    stranger = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user)

    with pytest.raises(DomainError) as error:
        services.delete_own_post(post=post, user=stranger.user)

    assert error.value.status_code == 403


# --- widoczność ----------------------------------------------------------------------------------


def test_the_author_sees_their_own_pending_post_and_nobody_else_does(competition):
    """Bez tego uczestnik wracałby na stronę, na której nie ma śladu po tym, co przed chwilą napisał."""
    profile = participant_of(competition)
    other = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    pending = ForumPostFactory(
        competition=competition, thread=thread, author=profile.user, status=ModerationStatus.PENDING
    )

    assert pending in services.visible_posts(thread, profile.user)
    assert pending not in services.visible_posts(thread, other.user)


def test_a_rejected_post_is_invisible_even_to_its_author_in_the_thread(competition):
    """Jego miejsce jest na ekranie „Twoje wpisy”, razem z notatką – a nie w udawanej rozmowie."""
    profile = participant_of(competition)
    thread = ForumThreadFactory(competition=competition)
    rejected = ForumPostFactory(
        competition=competition, thread=thread, author=profile.user, status=ModerationStatus.REJECTED
    )

    assert rejected not in services.visible_posts(thread, profile.user)
    assert rejected in services.own_posts(profile.user, competition)


def test_the_category_counter_counts_what_a_reader_will_actually_see(competition):
    """Licznik obiecujący dziesięć wątków przy dziale, w którym widać zero, jest licznikiem,
    którego strona działu nie dotrzyma."""
    reader = participant_of(competition)
    category = ForumCategoryFactory(competition=competition)
    ForumThreadFactory(competition=competition, category=category)
    ForumThreadFactory(competition=competition, category=category, status=ModerationStatus.PENDING)

    counted = services.categories_with_counts(competition, reader.user).get(pk=category.pk)

    assert counted.thread_count == 1


# --- moderacja i audyt ----------------------------------------------------------------------------


def test_approving_a_thread_publishes_its_first_post_too(competition):
    """To ten wpis moderator właśnie przeczytał – zatwierdza więc treść, a nie sam nagłówek."""
    closed_edition(competition)
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition)
    thread = services.create_thread(
        user=profile.user, competition=competition, category=category, title="T", body="B"
    )

    services.moderate_thread(
        thread=thread, actor=coordinator_of(competition), status=ModerationStatus.PUBLISHED
    )

    thread.refresh_from_db()
    assert thread.status == ModerationStatus.PUBLISHED
    assert thread.posts.get().status == ModerationStatus.PUBLISHED


def test_rejecting_without_a_note_is_refused(competition):
    """Ekran „Twoje wpisy” istnieje po to, żeby pod odmową stał dalszy ciąg."""
    post = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)

    with pytest.raises(DomainError) as error:
        services.moderate_post(post=post, actor=coordinator_of(competition), status=ModerationStatus.REJECTED)

    assert error.value.machine_code == "FORUM_NOTE_REQUIRED"


def test_the_rejection_note_reaches_the_author(competition):
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, status=ModerationStatus.PENDING)

    services.moderate_post(
        post=post,
        actor=coordinator_of(competition),
        status=ModerationStatus.REJECTED,
        note="Wpis zdradza rozwiązanie zadania 2.",
    )

    mine = services.own_posts(profile.user, competition).get(pk=post.pk)
    assert mine.moderation_note == "Wpis zdradza rozwiązanie zadania 2."


@pytest.mark.parametrize(
    ("status", "action"),
    [
        (ModerationStatus.PUBLISHED, "forum.post_approved"),
        (ModerationStatus.HIDDEN, "forum.post_hidden"),
    ],
)
def test_every_moderation_decision_leaves_an_audit_entry(competition, status, action):
    actor = coordinator_of(competition)
    post = ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)

    services.moderate_post(post=post, actor=actor, status=status)

    entry = AuditLog.objects.filter(action=action).get()
    assert entry.actor_id == actor.pk
    assert entry.target_id == str(post.pk)


def test_moderating_a_thread_back_to_pending_writes_its_own_audit_action(competition):
    """Powrót do kolejki nie jest zatwierdzeniem i nie ma dzielić z nim nazwy zdarzenia.

    ``_THREAD_ACTIONS[PENDING]`` wskazywał wcześniej na ``AUDIT_THREAD_APPROVED`` – ekran audytu
    pokazywałby więc wątek cofnięty do kolejki jako zatwierdzony, czyli dokładną odwrotność decyzji
    moderatora. ``AUDIT_THREAD_RESTORED`` mirroruje ``AUDIT_POST_RESTORED`` z tego samego powodu.
    """
    actor = coordinator_of(competition)
    thread = ForumThreadFactory(competition=competition, status=ModerationStatus.PUBLISHED)

    services.moderate_thread(thread=thread, actor=actor, status=ModerationStatus.PENDING)

    entry = AuditLog.objects.filter(action=services.AUDIT_THREAD_RESTORED).get()
    assert entry.actor_id == actor.pk
    assert entry.target_id == str(thread.pk)
    assert not AuditLog.objects.filter(action=services.AUDIT_THREAD_APPROVED).exists()


def test_the_audit_entry_never_carries_the_body_of_the_post(competition):
    """Audyt zostaje w bazie na stałe i nie podlega ani retencji, ani anonimizacji konta – więc
    skopiowana tam treść byłaby danymi, których nie zdejmie już żadne żądanie z art. 17."""
    post = ForumPostFactory(
        competition=competition, body="TAJNA-TRESC-WPISU", status=ModerationStatus.PENDING
    )

    services.moderate_post(
        post=post,
        actor=coordinator_of(competition),
        status=ModerationStatus.REJECTED,
        note="TAJNE-UZASADNIENIE",
    )

    entry = AuditLog.objects.filter(action="forum.post_rejected").get()
    assert "TAJNA-TRESC-WPISU" not in str(entry.diff)
    assert "TAJNE-UZASADNIENIE" not in str(entry.diff)
    # W ``diff`` stoi **sama obecność** notatki – tyle, ile trzeba, żeby odpowiedzieć na pytanie
    # „czy autor dostał uzasadnienie”, i nic ponadto.
    assert entry.diff["has_note"] is True


def test_pinning_and_locking_leave_their_own_audit_entries(competition):
    actor = coordinator_of(competition)
    thread = ForumThreadFactory(competition=competition)

    services.set_thread_flag(thread=thread, actor=actor, field="is_pinned", value=True)
    services.set_thread_flag(thread=thread, actor=actor, field="is_locked", value=True)

    assert AuditLog.objects.filter(action="forum.thread_pinned").count() == 1
    assert AuditLog.objects.filter(action="forum.thread_locked").count() == 1


def test_bulk_approval_leaves_one_audit_entry_per_item(competition):
    """Kolejka, która po jednym kliknięciu zostawia jeden ślad na sto wpisów, przestaje
    odpowiadać na pytanie „kto to przepuścił”."""
    actor = coordinator_of(competition)
    posts = [ForumPostFactory(competition=competition, status=ModerationStatus.PENDING) for _ in range(3)]

    approved = services.bulk_approve(
        competition=competition, actor=actor, post_ids=[post.pk for post in posts]
    )

    assert approved == 3
    assert AuditLog.objects.filter(action="forum.post_approved").count() == 3


def test_resolving_a_report_does_not_touch_the_post(competition):
    """Moderator bywa innego zdania niż zgłaszający i to też jest rozpatrzeniem sprawy."""
    from apps.forum.tests.factories import ForumReportFactory

    report = ForumReportFactory(competition=competition)

    services.resolve_report(report=report, actor=coordinator_of(competition))

    report.refresh_from_db()
    report.post.refresh_from_db()
    assert report.resolved_at is not None
    assert report.post.status == ModerationStatus.PUBLISHED


def test_a_report_does_not_hide_the_post_on_its_own(competition):
    """Automatyczne zdejmowanie dałoby każdemu uczestnikowi przycisk „usuń cudzy wpis”."""
    reporter = participant_of(competition)
    post = ForumPostFactory(competition=competition)

    services.report_post(post=post, user=reporter.user, reason="Nie podoba mi się.")

    post.refresh_from_db()
    assert post.status == ModerationStatus.PUBLISHED


# --- kolejka -----------------------------------------------------------------------------------


def test_the_queue_shows_a_new_thread_once_not_twice(competition):
    """Wątek i jego pierwszy wpis to dla moderatora **jedna** pozycja: zatwierdza je razem."""
    closed_edition(competition)
    with_forum(competition)
    profile = participant_of(competition)
    category = ForumCategoryFactory(competition=competition)
    services.create_thread(user=profile.user, competition=competition, category=category, title="T", body="B")

    assert services.pending_threads(competition).count() == 1
    assert services.pending_posts(competition).count() == 0
    assert services.moderation_count(competition) == 1


def test_a_competition_without_the_flag_counts_nothing(competition):
    """Konkurs bez forum oddaje zero **bez ani jednego zapytania** – flaga jest polem wiersza."""
    ForumPostFactory(competition=competition, status=ModerationStatus.PENDING)

    assert competition.has_feature("participant_forum") is False
    assert services.moderation_count(competition) == 0


# --- anonimizacja konta ---------------------------------------------------------------------------


def test_anonymising_an_account_takes_the_signature_off_the_posts_and_leaves_the_conversation(
    competition,
):
    """Art. 17 spełniony bez wycinania rozmowy – i bez ani jednej linijki w ``apps.forum``.

    Obie połowy reguły mieszkają w tym module: ``author`` jest ``SET_NULL``, a podpis powstaje
    w jednym miejscu, które wytarte imię czyta tak samo jak skasowane konto. Treść zostaje, bo pod
    wpisem stoją cudze odpowiedzi, a wycięcie akapitu, do którego ktoś się odniósł, zamienia je
    w bełkot – sama treść przestaje być jednak powiązana z osobą.
    """
    from apps.accounts.profile import anonymise_account

    profile = participant_of(competition, user__first_name="Ania", user__last_name="Testowa")
    post = ForumPostFactory(competition=competition, author=profile.user, body="TRESC-ROZMOWY")

    anonymise_account(profile.user)

    post.refresh_from_db()
    post.author.refresh_from_db()
    assert post.body == "TRESC-ROZMOWY"
    assert display_author(post.author) == "Użytkownik usunięty"


def test_deleting_the_account_row_leaves_the_post_without_an_author(competition):
    """Ścieżka twardego usunięcia konta: ``SET_NULL`` zostawia wpis, a nie wywraca kasowania."""
    profile = participant_of(competition)
    post = ForumPostFactory(competition=competition, author=profile.user, body="TRESC-ROZMOWY")

    profile.user.delete()

    post.refresh_from_db()
    assert post.author_id is None
    assert display_author(post.author) == "Użytkownik usunięty"


# --- izolacja konkursów ---------------------------------------------------------------------------


def test_threads_of_another_competition_are_invisible(competition, other_competition):
    reader = participant_of(competition)
    mine = ForumThreadFactory(competition=competition, title="WATEK-TUTEJSZY")
    stranger = ForumThreadFactory(competition=other_competition, title="WATEK-OBCY")

    visible = services.visible_threads(competition, reader.user)

    assert mine in visible
    assert stranger not in visible


def test_categories_of_another_competition_are_invisible(competition, other_competition):
    reader = participant_of(competition)
    ForumCategoryFactory(competition=other_competition, name="DZIAL-OBCY")

    names = [row.name for row in services.categories_with_counts(competition, reader.user)]

    assert "DZIAL-OBCY" not in names


def test_own_posts_do_not_reach_across_competitions(competition, other_competition):
    """Ta sama osoba bywa uczestnikiem dwóch olimpiad – „Twoje wpisy” pokazują wpisy **tej**."""
    profile = participant_of(competition)
    here = ForumPostFactory(competition=competition, author=profile.user)
    there = ForumPostFactory(competition=other_competition, author=profile.user)

    mine = services.own_posts(profile.user, competition)

    assert here in mine
    assert there not in mine


def test_bulk_approval_cannot_reach_a_post_of_another_competition(competition, other_competition):
    stranger = ForumPostFactory(competition=other_competition, status=ModerationStatus.PENDING)

    approved = services.bulk_approve(
        competition=competition, actor=coordinator_of(competition), post_ids=[stranger.pk]
    )

    stranger.refresh_from_db()
    assert approved == 0
    assert stranger.status == ModerationStatus.PENDING


def test_a_category_slug_may_repeat_in_another_competition(competition, other_competition):
    """Dwie olimpiady mają prawo mieć dział „zadania” – adres rozstrzyga się już po domenie.

    Więz jest na parze ``(konkurs, slug)``, a nie na samym slugu: unikalność w całej instalacji
    znaczyłaby, że pierwsza olimpiada, która założy dział „zadania”, odbiera tę nazwę wszystkim
    pozostałym konkursom na serwerze.
    """
    here = ForumCategoryFactory(competition=competition, slug="zadania")
    there = ForumCategoryFactory(competition=other_competition, slug="zadania")

    assert here.pk != there.pk
    assert ForumCategory.objects.for_competition(competition).filter(slug="zadania").count() == 1
