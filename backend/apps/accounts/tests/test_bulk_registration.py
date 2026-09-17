"""Import listy uczniów i zaproszenie, którym uczeń sam uruchamia swoje konto.

Przedmiotem testów jest granica między tym, co wolno zrobić nauczycielowi, a tym, co musi zrobić
uczeń. Nauczyciel wgrywa listę – i nic więcej: nie ustawia nikomu hasła, nie składa za nikogo
zgód, nie zakłada drugiego konta osobie, która już je ma. Uczeń pod linkiem ustawia hasło i sam
oświadcza, że zna regulamin. Każdy z tych testów pilnuje jednej strony tej granicy.
"""

from __future__ import annotations

import io

import pytest
from django.core import signing
from django.utils import timezone

from apps.accounts.bulk_registration import (
    ACTION_CREATE,
    ACTION_LINK,
    ACTION_SKIP,
    INVITE_SALT,
    MAX_ROWS,
    STATE_ACTIVE,
    STATE_INVITED,
    STATE_SELF_REGISTERED,
    accept_invitation,
    import_students,
    invitation_state,
    make_invite_token,
    pack_rows,
    preview_upload,
    read_invite_token,
    resend_invitation,
    unpack_rows,
)
from apps.accounts.consents import CONSENT_FIELD_NAMES, ConsentKind
from apps.accounts.models import ConsentRecord, Participant, User, Voivodeship
from apps.accounts.tests.factories import CoordinatorFactory, ParticipantFactory, UserFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db

SUPERVISOR_EMAIL = "nauczyciel@szkola.test"

#: Rok urodzenia osoby pełnoletniej i niepełnoletniej **względem dzisiaj**, a nie literały:
#: test roczników z zaszytym rokiem przestaje cokolwiek znaczyć po sylwestrze.
ADULT_YEAR = timezone.localdate().year - 25
MINOR_YEAR = timezone.localdate().year - 16

HEADER = "imię;nazwisko;e-mail;rok urodzenia;klasa;telefon;e-mail opiekuna prawnego"


class Upload(io.BytesIO):
    """Namiastka wgranego pliku: ``name`` i ``size``, czyli to, czego dotyka ``read_table``."""

    def __init__(self, data: bytes, name: str = "lista.csv"):
        super().__init__(data)
        self.name = name
        self.size = len(data)


def csv_upload(*rows: str, header: str = HEADER, name: str = "lista.csv") -> Upload:
    text = "\n".join([header, *rows])
    return Upload(text.encode("utf-8"), name=name)


def row(
    email: str,
    *,
    first: str = "Kasia",
    last: str = "Nowak",
    year: int = ADULT_YEAR,
    grade: int = 2,
    phone: str = "",
    guardian: str = "",
) -> str:
    return f"{first};{last};{email};{year};{grade};{phone};{guardian}"


def preview(*rows: str, header: str = HEADER, supervisor: str = SUPERVISOR_EMAIL):
    return preview_upload(
        csv_upload(*rows, header=header),
        with_supervisor=False,
        default_supervisor_email=supervisor,
    )


# --- czytanie pliku ---------------------------------------------------------------------------


def test_brak_wymaganej_kolumny_to_odmowa_z_nazwa_kolumny():
    """Plik bez kolumny „e-mail” odrzucamy **z nazwą** brakującej kolumny, a nie ogólnie.

    Domyślanie się kolumn po kolejności dałoby listę, w której nazwiska trafiły do rubryki adresu –
    i dowiedzielibyśmy się o tym z dwudziestu odbitych listów.
    """
    with pytest.raises(DomainError) as exc:
        preview(header="imię;nazwisko;rok urodzenia;klasa")
    assert "e-mail" in str(exc.value.detail)


def test_naglowki_rozpoznajemy_bez_wzgledu_na_diakrytyki_wielkosc_liter_i_kolejnosc():
    """„IMIĘ”, „imie” i „Rok urodzenia” w dowolnej kolejności to ten sam plik.

    Arkusz prowadzi nauczyciel, a nie nasz eksport – wymaganie dokładnego brzmienia nagłówka
    znaczyłoby odmowę dla większości plików, które naprawdę przychodzą.
    """
    result = preview(
        "kowalska@example.test;Kasia;3;" + str(ADULT_YEAR),
        header="E-MAIL;Imie;Klasa;ROCZNIK;nazwisko",
    )
    # Kolumna „nazwisko” jest w nagłówku, ale bez wartości – wiersz ma zostać odrzucony **z tego**
    # powodu, a nie z powodu nierozpoznanych nagłówków.
    assert result.rows[0].email == "kowalska@example.test"
    assert result.rows[0].birth_year == ADULT_YEAR


def test_srednik_przecinek_i_bom_czytamy_tak_samo():
    """Polski Excel zapisuje średnikami i dokleja BOM; „CSV UTF-8” daje przecinki."""
    data = ("﻿" + HEADER.replace(";", ",") + "\n" + row("a@example.test").replace(";", ",")).encode("utf-8")
    result = preview_upload(Upload(data), with_supervisor=False, default_supervisor_email=SUPERVISOR_EMAIL)
    assert result.to_create == 1


def test_puste_wiersze_arkusza_nie_sa_bledami():
    """Arkusz „ma” tysiąc wierszy – puste po prostu pomijamy, nie licząc ich jako pominiętych."""
    result = preview(row("a@example.test"), ";;;;;;", ";;;;;;")
    assert len(result.rows) == 1
    assert result.skipped == 0


def test_limit_wierszy_jest_odmowa_a_nie_cichym_obcieciem():
    """Plik ponad limit odrzucamy w całości: obcięcie listy zostawiłoby część klasy bez zaproszeń."""
    rows = [row(f"uczen{index}@example.test") for index in range(MAX_ROWS + 1)]
    with pytest.raises(DomainError) as exc:
        preview(*rows)
    assert exc.value.machine_code == "IMPORT_TOO_MANY_ROWS"


def test_plik_w_nieobslugiwanym_formacie_konczy_sie_odmowa():
    with pytest.raises(DomainError) as exc:
        preview_upload(Upload(b"cokolwiek", name="lista.pdf"), with_supervisor=False)
    assert exc.value.machine_code == "IMPORT_FORMAT"


# --- rozstrzyganie wierszy --------------------------------------------------------------------


def test_zly_adres_brak_nazwiska_i_klasa_spoza_zakresu_to_wiersze_pominiete():
    """Trzy różne usterki, jedno zachowanie: wiersz pominięty i powód wypisany przy nim.

    Import nie zatrzymuje się na pierwszym błędzie – nauczyciel ma zobaczyć **całą** listę usterek
    za jednym razem, bo poprawia plik w arkuszu, a nie w naszym formularzu.
    """
    result = preview(
        "Kasia;Nowak;to-nie-adres;2008;2;;",
        f"Jan;;jan@example.test;{ADULT_YEAR};2;;",
        f"Ola;Lis;ola@example.test;{ADULT_YEAR};9;;",
    )
    assert result.to_create == 0
    assert result.skipped == 3
    assert all(item.action == ACTION_SKIP for item in result.rows)
    assert any("e-mail" in problem for problem in result.rows[0].errors)


def test_adres_powtorzony_w_pliku_liczy_sie_raz():
    """„Jan.Kowalski@…” i „jan.kowalski@…” to jedna skrzynka i jedno konto."""
    result = preview(row("Jan.Kowalski@example.test"), row("jan.kowalski@example.test"))
    assert result.to_create == 1
    assert result.rows[1].action == ACTION_SKIP
    assert "powtórzony" in " ".join(result.rows[1].errors)


def test_istniejace_konto_uczestnika_jest_dowiazywane_a_nie_zakladane_drugi_raz():
    """Uczeń, który zapisał się sam tydzień wcześniej, nie traci konta i nie dostaje drugiego."""
    ParticipantFactory(user=UserFactory(email="ola@example.test"))
    result = preview(row("ola@example.test"))
    assert result.rows[0].action == ACTION_LINK
    assert result.to_create == 0


def test_adres_konta_bez_profilu_uczestnika_jest_pomijany():
    """Konto recenzenta albo koordynatora nie dostaje profilu uczestnika z cudzego pliku.

    Dorobienie profilu zmieniałoby komuś rolę w zawodach na podstawie arkusza wgranego przez
    osobę trzecią – a rolę w komitecie nadaje kod zaproszenia, nie lista klasowa.
    """
    UserFactory(email="recenzent@example.test")
    result = preview(row("recenzent@example.test"))
    assert result.rows[0].action == ACTION_SKIP
    assert "nie jest kontem uczestnika" in " ".join(result.rows[0].errors)


def test_niepelnoletni_bez_adresu_opiekuna_jest_oznaczony_ale_nie_odrzucony():
    """Brak adresu opiekuna prawnego to notka, nie błąd: zgodę zbieramy od ucznia po zaproszeniu."""
    result = preview(row("mlody@example.test", year=MINOR_YEAR))
    assert result.rows[0].action == ACTION_CREATE
    assert any("niepełnoletni" in note for note in result.rows[0].notes)


def test_niepoprawny_telefon_nie_odrzuca_wiersza():
    """Telefon jest udogodnieniem, a nie warunkiem konta – zły numer zostaje pusty z notką."""
    result = preview(row("a@example.test", phone="nie-numer"))
    assert result.rows[0].action == ACTION_CREATE
    assert result.rows[0].phone == ""
    assert any("telefon" in note for note in result.rows[0].notes)


def test_numer_telefonu_z_pliku_ladnie_sie_normalizuje():
    result = preview(row("a@example.test", phone="600 100 200"))
    assert result.rows[0].phone == "+48600100200"


def test_kolumna_opiekuna_szkolnego_dziala_tylko_w_imporcie_koordynatora():
    """Nauczyciel nie może przypisać ucznia komuś innemu – u niego tej kolumny po prostu nie ma.

    Plik z taką kolumną nie jest odrzucany (arkusze bywają wspólne), ale wartość jest ignorowana
    i wchodzi adres zalogowanego opiekuna.
    """
    header = HEADER + ";e-mail opiekuna szkolnego"
    line = row("a@example.test") + ";ktos.inny@szkola.test"
    mine = preview_upload(
        csv_upload(line, header=header), with_supervisor=False, default_supervisor_email=SUPERVISOR_EMAIL
    )
    assert mine.rows[0].supervisor_email == SUPERVISOR_EMAIL
    theirs = preview_upload(
        csv_upload(line, header=header), with_supervisor=True, default_supervisor_email=""
    )
    assert theirs.rows[0].supervisor_email == "ktos.inny@szkola.test"


# --- koszyk między podglądem a zatwierdzeniem --------------------------------------------------


def test_koszyk_wierszy_nie_da_sie_podmienic_miedzy_podgladem_a_zatwierdzeniem():
    """Podpis pilnuje, że zatwierdzamy tę listę, która stała w podglądzie – i tylko ją.

    Do koszyka nie wchodzą wiersze pominięte: po zatwierdzeniu nie miałyby czego zrobić, a ich
    obecność kazałaby zatwierdzeniu liczyć te same usterki drugi raz.
    """
    result = preview(row("a@example.test"), "Kasia;Nowak;to-nie-adres;2008;2;;")
    rows = unpack_rows(result.token)
    assert [item.email for item in rows] == ["a@example.test"]
    with pytest.raises(DomainError):
        unpack_rows(result.token + "x")
    assert pack_rows(rows) != ""


# --- zapis ------------------------------------------------------------------------------------


def do_import(*rows_text: str, actor=None, school: str = "XIV LO", **kwargs):
    result = preview(*rows_text, **kwargs)
    return import_students(
        unpack_rows(result.token),
        school_name=school,
        default_supervisor_email=SUPERVISOR_EMAIL,
        actor=actor or CoordinatorFactory(),
    )


def test_zaproszone_konto_jest_nieaktywne_bez_hasla_i_bez_zgod(django_capture_on_commit_callbacks):
    """Sedno przepływu: konto istnieje, ale nie da się na nie wejść i nikt się za nikogo nie zgodził."""
    with django_capture_on_commit_callbacks(execute=True):
        summary = do_import(row("kasia@example.test", grade=3))
    assert summary == {"created": 1, "linked": 0, "skipped": 0}
    user = User.objects.get(email="kasia@example.test")
    assert user.is_active is False
    assert user.email_verified_at is None
    assert user.has_usable_password() is False
    # ``participations``, nie ``participant``: profil należy do konkursu, a konto może mieć ich
    # tyle, w ilu konkursach startuje (§ 3.3). Import zakłada dokładnie jeden.
    participant = user.participations.get()
    assert participant.gdpr_consent_at is None
    assert participant.terms_accepted_at is None
    assert ConsentRecord.objects.filter(participant=participant).count() == 0
    assert participant.school == "XIV LO"
    assert participant.grade == 3
    assert participant.supervisor_email == SUPERVISOR_EMAIL
    assert participant.invited_at is not None
    assert invitation_state(participant) == STATE_INVITED


def test_import_wysyla_kazdemu_uczniowi_list_bez_danych_osob_trzecich(
    django_capture_on_commit_callbacks, mailoutbox
):
    """W liście jest imię ucznia i nazwa szkoły – i ani słowa o nazwisku czy nauczycielu.

    Adres przepisał z listy klasowej ktoś inny, więc list bywa doręczony pod zły adres; imię
    i szkoła wystarczą do rozpoznania sprawy, a nazwisko byłoby daną wydaną obcemu.
    """
    with django_capture_on_commit_callbacks(execute=True):
        do_import(row("kasia@example.test", first="Kasia", last="Nazwiskowska"))
    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == ["kasia@example.test"]
    assert "Kasia" in message.body
    assert "XIV LO" in message.body
    assert "Nazwiskowska" not in message.body
    assert SUPERVISOR_EMAIL not in message.body


def test_dowiazanie_istniejacego_konta_ustawia_opiekuna_i_nie_rusza_zgod(
    django_capture_on_commit_callbacks, mailoutbox
):
    """Konto, które już istnieje, dostaje wyłącznie adres opiekuna – żadnego listu i żadnej zmiany zgód."""
    participant = ParticipantFactory(user=UserFactory(email="ola@example.test"))
    consents_before = participant.gdpr_consent_at
    with django_capture_on_commit_callbacks(execute=True):
        summary = do_import(row("ola@example.test"))
    participant.refresh_from_db()
    assert summary == {"created": 0, "linked": 1, "skipped": 0}
    assert participant.supervisor_email == SUPERVISOR_EMAIL
    assert participant.gdpr_consent_at == consents_before
    assert participant.invited_at is None
    assert mailoutbox == []


def test_wojewodztwo_wchodzi_ze_szkoly_z_rejestru_a_bez_niej_zostaje_puste():
    """Listy klasowe nie mają kolumny „województwo” – bierzemy je ze szkoły albo pytamy ucznia."""
    from apps.schools.tests.factories import SchoolFactory

    school = SchoolFactory(voivodeship=Voivodeship.DOLNOSLASKIE)
    result = preview(row("a@example.test"))
    import_students(
        unpack_rows(result.token),
        school_name=school.name,
        school_ref=school,
        default_supervisor_email=SUPERVISOR_EMAIL,
        actor=CoordinatorFactory(),
    )
    assert Participant.objects.get(user__email="a@example.test").district == Voivodeship.DOLNOSLASKIE


def test_audyt_importu_niesie_liczby_a_nie_adresy():
    """Wpisy audytowe czytają osoby bez wglądu w listy klasowe – nie może w nich być adresu."""
    actor = CoordinatorFactory()
    do_import(row("kasia@example.test"), actor=actor)
    entries = AuditLog.objects.filter(
        action__in=["accounts.students_imported", "participant.invited_by_import"]
    )
    assert entries.count() == 2
    for entry in entries:
        assert "kasia@example.test" not in str(entry.diff)


def test_kosiarka_kont_nieaktywowanych_nie_kasuje_zaproszonych():
    """Zaproszenie żyje 14 dni, a kosiarka kasuje po czterech godzinach – musi je omijać.

    Bez tego wyjątku import wysłany w piątek znikałby z bazy przed poniedziałkiem, razem
    z dowiązaniem do opiekuna.
    """
    from datetime import timedelta

    from apps.accounts.tasks import purge_unactivated_accounts

    do_import(row("kasia@example.test"))
    User.objects.filter(email="kasia@example.test").update(date_joined=timezone.now() - timedelta(days=3))
    result = purge_unactivated_accounts()
    assert result["deleted"] == 0
    assert User.objects.filter(email="kasia@example.test").exists()


# --- zaproszenie i jego przyjęcie --------------------------------------------------------------


def invited_participant(**kwargs) -> Participant:
    do_import(row("kasia@example.test", **kwargs))
    return Participant.objects.select_related("user").get(user__email="kasia@example.test")


def consents(**overrides) -> dict:
    given = dict.fromkeys(CONSENT_FIELD_NAMES, True)
    given.update(overrides)
    return given


def test_token_otwiera_zaproszenie_a_po_uruchomieniu_konta_przestaje_dzialac():
    """Jednorazowość bierze się ze stanu konta, nie z wiersza w bazie – drugie wejście nic nie zmienia."""
    participant = invited_participant()
    token = make_invite_token(participant)
    assert read_invite_token(token).pk == participant.pk
    accept_invitation(
        participant,
        password="Poprawne-Haslo-2026",
        phone="600 100 200",
        district=Voivodeship.MAZOWIECKIE,
        given=consents(),
    )
    with pytest.raises(DomainError):
        read_invite_token(token)


def test_token_wystawiony_na_inny_adres_nie_otwiera_konta():
    """Po poprawieniu adresu konta stary link przestaje działać – to jedyna droga jego odwołania."""
    participant = invited_participant()
    token = signing.dumps({"pk": participant.user_id, "email": "ktos.inny@example.test"}, salt=INVITE_SALT)
    with pytest.raises(DomainError):
        read_invite_token(token)


def test_przyjecie_zaproszenia_ustawia_haslo_zgody_i_aktywuje_konto():
    """Uczeń robi to, czego nauczyciel zrobić nie mógł: ustala hasło i sam składa oświadczenia."""
    participant = invited_participant()
    accept_invitation(
        participant,
        password="Poprawne-Haslo-2026",
        phone="600 100 200",
        district=Voivodeship.MAZOWIECKIE,
        given=consents(),
    )
    user = User.objects.get(pk=participant.user_id)
    assert user.is_active is True
    assert user.email_verified_at is not None
    assert user.check_password("Poprawne-Haslo-2026")
    participant.refresh_from_db()
    assert participant.gdpr_consent_at is not None
    assert participant.district == Voivodeship.MAZOWIECKIE
    assert participant.phone == "+48600100200"
    kinds = set(ConsentRecord.objects.filter(participant=participant).values_list("kind", flat=True))
    assert ConsentKind.TERMS in kinds and ConsentKind.PRIVACY in kinds
    # Znacznik pochodzenia zostaje: odpowiada na pytanie „skąd to konto”, a nie „w jakim jest stanie”.
    assert participant.invited_at is not None
    assert invitation_state(participant) == STATE_ACTIVE


def test_bez_kompletu_zgod_konto_nie_staje_sie_aktywne():
    """Odmowa musi zapaść **przed** zapisem – konto nie ma prawa być aktywne ani na chwilę."""
    participant = invited_participant()
    with pytest.raises(DomainError):
        accept_invitation(
            participant,
            password="Poprawne-Haslo-2026",
            phone="600 100 200",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(terms_consent=False),
        )
    user = User.objects.get(pk=participant.user_id)
    assert user.is_active is False
    assert user.has_usable_password() is False


def test_niepelnoletni_musi_miec_zgode_opiekuna_tak_samo_jak_w_rejestracji():
    participant = invited_participant(year=MINOR_YEAR)
    with pytest.raises(DomainError):
        accept_invitation(
            participant,
            password="Poprawne-Haslo-2026",
            phone="600 100 200",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(guardian_consent=False),
        )


def test_slabe_haslo_jest_odrzucane_ta_sama_regula_co_przy_rejestracji():
    participant = invited_participant()
    with pytest.raises(DomainError) as exc:
        accept_invitation(
            participant,
            password="haslo",
            phone="600 100 200",
            district=Voivodeship.MAZOWIECKIE,
            given=consents(),
        )
    assert exc.value.machine_code == "WEAK_PASSWORD"


def test_ponowne_wyslanie_zaproszenia_dziala_tylko_dla_konta_ktore_czeka(
    django_capture_on_commit_callbacks, mailoutbox
):
    """Trzy różne stany, trzy różne odpowiedzi – „nic się nie stało” byłoby najgorszą z nich."""
    participant = invited_participant()
    mailoutbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        resend_invitation(participant, actor=participant.user)
    assert len(mailoutbox) == 1

    accept_invitation(
        participant,
        password="Poprawne-Haslo-2026",
        phone="600 100 200",
        district=Voivodeship.MAZOWIECKIE,
        given=consents(),
    )
    participant.refresh_from_db()
    with pytest.raises(DomainError) as exc:
        resend_invitation(participant)
    assert exc.value.machine_code == "INVITE_ALREADY_ACCEPTED"

    self_registered = ParticipantFactory(user=UserFactory(email="sam@example.test"))
    assert invitation_state(self_registered) == STATE_SELF_REGISTERED
    with pytest.raises(DomainError) as exc:
        resend_invitation(self_registered)
    assert exc.value.machine_code == "INVITE_NOT_IMPORTED"
