"""Eksport danych konta (art. 20 RODO – prawo do przenoszenia danych).

Przepis daje osobie prawo otrzymać swoje dane „w ustrukturyzowanym, powszechnie używanym formacie
nadającym się do odczytu maszynowego”. Stąd kształt paczki: **ZIP** z jednym plikiem ``dane.json``
i katalogiem ``pliki/`` z rozwiązaniami, które uczestnik sam wgrał. JSON, bo jest odczytywalny
maszynowo i jednocześnie czytelny dla człowieka bez żadnego narzędzia; ZIP, bo dane bez plików
rozwiązań nie byłyby kompletem, a rozwiązania potrafią ważyć dziesiątki megabajtów.

Trzy granice, których ten moduł nie przekracza:

- **nigdy cudze dane.** Wszystko, co tu wchodzi, jest wybrane po kluczu tego konta. Ogłoszona
  tabela wyników jest w eksporcie sprowadzona do **własnego wiersza** (suma, miejsce, decyzja):
  cała tabela jest jawna pod własnym adresem i nie ma powodu, żeby wyjeżdżała z prośby jednej
  osoby o jej własne dane,
- **nigdy komentarze wewnętrzne recenzentów.** Do paczki wchodzi wyłącznie to, co uczestnik
  i tak widzi w panelu – ``Review.comment_for_participant`` oraz publiczne adnotacje i uwagi
  do linii. ``comment_internal`` jest notatką komitetu o pracy, nie daną uczestnika, i nie
  opuszcza ``apps.results.feedback`` nigdy (PROJEKT.md 2.4). Recenzenci zostają anonimowi –
  podpisani „Recenzent A/B”, tak samo jak w informacji zwrotnej,
- **nigdy poświadczenia.** Nie ma tu hasła (nawet w postaci skrótu), tokenu API ani powiązań
  z dostawcami OAuth. Kopia skrótu hasła nie jest „danymi osobowymi do przeniesienia”, tylko
  materiałem do łamania offline.

Dlaczego własny budowniczy ZIP-a, a nie ``apps.submissions.packaging.build_zip``: tamten przyjmuje
**rozwiązania** i buduje paczkę wyłącznie z ich plików plus ``README.txt``. Nie umie dołożyć wpisu
z gotowych bajtów (``dane.json``) i nie powinien się tego uczyć – jego podpis opisuje paczkę prac
etapu i jest ścieżką krytyczną pobrań komitetu. Tutaj jest tak samo jak w
``apps.results.certificates.build_certificates_zip``: kilkanaście linii własnego kodu zamiast
przerabiania cudzego kontraktu. Pliki rozwiązań i tak jadą **strumieniem** ze storage'u, a nie
przez pamięć – to jedyna rzecz z tamtego modułu, którą trzeba było zachować.
"""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

from django.core.cache import cache
from django.utils import timezone

from apps.core.models import audit

from .models import User

logger = logging.getLogger(__name__)

#: Nazwa pliku z danymi w paczce. Po polsku, bo paczkę otwiera uczestnik, a nie system.
DATA_ENTRY_NAME = "dane.json"

#: Katalog na pliki rozwiązań w paczce.
FILES_PREFIX = "pliki/"

#: Porcja przepisywana ze storage'u do archiwum – ta sama wartość, co w ``submissions.packaging``.
COPY_CHUNK = 1024 * 1024

#: Wersja formatu paczki. Odbiorca, który zbuduje sobie import, ma po czym poznać, że kształt
#: pliku się zmienił – a my mamy czym odpowiedzieć na pytanie „z której wersji jest ten eksport”.
EXPORT_FORMAT_VERSION = 1


def _moment(value) -> str | None:
    """Znacznik czasu w ISO 8601, w strefie polskiej. ``None`` zostaje ``None``.

    Czas lokalny, a nie UTC: paczkę czyta człowiek, który zna godzinę oddania swojej pracy
    z zegarka, a nie z przeliczenia. Strefa jest w zapisie jawna, więc nic się nie gubi.
    """
    return timezone.localtime(value).isoformat() if value else None


def _account_section(user: User) -> dict:
    """Samo konto: tożsamość logowania i role. Bez hasła, tokenów i powiązań z dostawcami."""
    return {
        "email": user.email,
        "imie": user.first_name,
        "nazwisko": user.last_name,
        "zarejestrowane": _moment(user.date_joined),
        "adres_potwierdzony": _moment(user.email_verified_at),
        "aktywne": user.is_active,
        "role": sorted(user.groups.values_list("name", flat=True)),
    }


def _participant_section(participant) -> dict | None:
    """Profil uczestnika razem z preferencją publikacji nazwiska."""
    if participant is None:
        return None
    return {
        "kod_publiczny": participant.public_code,
        "szkola": participant.school,
        "szkola_z_rejestru": participant.school_ref.name if participant.school_ref_id else None,
        "wojewodztwo": participant.district,
        "klasa": participant.grade,
        # Pełna data i rocznik obok siebie: eksport art. 15 RODO ma pokazać to, co w bazie
        # **jest**, a w wierszach sprzed wydania 0.30.0 jest sam rocznik. Pusta data znaczy więc
        # „nie mamy dnia urodzin”, a nie „pominięto pole”.
        "data_urodzenia": participant.birth_date.isoformat() if participant.birth_date else None,
        "rok_urodzenia": participant.known_birth_year,
        "telefon": participant.phone,
        "opiekun_szkolny_email": participant.supervisor_email,
        "zgoda_na_publikacje_nazwiska": participant.publish_full_name,
        "zgoda_rodo_z_dnia": _moment(participant.gdpr_consent_at),
        "regulamin_zaakceptowany": _moment(participant.terms_accepted_at),
    }


def _committee_section(member) -> dict | None:
    """Profil członka komitetu. Bez listy przydzielonych prac – te są danymi uczestników."""
    if member is None:
        return None
    return {
        "wojewodztwo": member.district,
        "wojewodztwo_zweryfikowane": member.district_verified,
        "status": member.status,
        "komisja_odwolawcza": member.is_appeals_committee,
        "utworzony": _moment(member.created_at),
        "zatwierdzony": _moment(member.approved_at),
    }


def _supervisor_section(supervisor) -> dict | None:
    """Profil opiekuna szkolnego. Bez listy uczniów – to cudze dane, nie jego."""
    if supervisor is None:
        return None
    return {
        "szkola": supervisor.school,
        "szkola_z_rejestru": supervisor.school_ref.name if supervisor.school_ref_id else None,
        "telefon": supervisor.phone,
        "dane_szkoly_zweryfikowane": supervisor.verified,
        "utworzony": _moment(supervisor.created_at),
    }


def _consent_rows(queryset, owner: str) -> list[dict]:
    """Wiersze jednego właściciela: rodzaj, **wersja dokumentu**, kiedy wyrażona i wycofana.

    Wersja dokumentu jest tu najważniejsza i dlatego jest wypisana wprost: pytanie „na co ta osoba
    się zgodziła” ma sens dopiero razem z brzmieniem dokumentu, pod którym to zrobiła.
    """
    return [
        {
            "wlasciciel": owner,
            "rodzaj": record.kind,
            "wersja_dokumentu": record.document_version,
            "wyrazona": _moment(record.given_at),
            "wycofana": _moment(record.withdrawn_at),
            "droga": record.source,
            "potwierdzona_z_adresu": record.given_by_email,
        }
        for record in queryset.order_by("given_at", "id")
    ]


def _consents_section(participant, supervisor) -> list[dict]:
    """Dowody zgód **tego konta** – uczestnika i opiekuna szkolnego razem, z właścicielem w wierszu.

    Jedno konto bywa naraz profilem uczestnika i profilem opiekuna (rodzic, który sam kiedyś
    startował, a dziś jest opiekunem młodszego rodzeństwa – adresy bywają te same). Osobna sekcja
    dla każdej roli zmuszałaby do dwóch pytań o to samo prawo (art. 15/20 RODO pyta o **dane tej
    osoby**, nie „dane tej osoby jako uczestnika”); jedna lista z ``wlasciciel`` w każdym wierszu
    odpowiada od razu na całe pytanie, tak samo jak ``_forum_section`` łączy wpisy ze wszystkich
    konkursów w jedną listę zamiast rysować sekcję na każdy z osobna.

    ``ConsentRecord`` sam nie ma wspólnego „konta” do przefiltrowania po nim (patrz jego Meta) –
    dowód niesie właściciela w kolumnie ``participant`` **albo** ``supervisor``, nigdy oba naraz –
    więc dwa zapytania po dwóch relacjach są tu jedyną drogą, nie skrótem od jednego złączenia.
    """
    rows: list[dict] = []
    if participant is not None:
        rows += _consent_rows(participant.consents, "uczestnik")
    if supervisor is not None:
        rows += _consent_rows(supervisor.consents, "opiekun_szkolny")
    return rows


def _files_section(submission) -> list[dict]:
    """Metryka plików pracy: skrót SHA-256, rozmiar, typ i wynik skanu antywirusowego.

    ``sha256`` jest tu z konkretnego powodu: to jedyny dowód, że plik w paczce jest tym samym
    plikiem, który system przyjął w chwili oddania pracy. Bez niego eksport byłby kopią bez
    tożsamości.
    """
    return [
        {
            "nazwa": item.original_name,
            "sha256": item.sha256,
            "typ_mime": item.mime,
            "rozmiar_bajtow": item.size_bytes,
            "skan_antywirusowy": item.av_status,
            "wgrany": _moment(item.created_at),
            "nazwa_w_paczce": _zip_name(submission, item),
        }
        for item in submission.files.order_by("created_at", "id")
    ]


def _zip_name(submission, submission_file) -> str:
    """Nazwa pliku w paczce: ``pliki/zad<numer>-v<wersja>-<nazwa od uczestnika>``.

    Nazwa uczestnika zostaje, bo paczka jedzie **do niego** – to jego plik i jego nazwa; reguła
    anonimizacji nazw z ``submissions.packaging`` chroni przed odwrotną sytuacją (nazwisko autora
    trafiające do recenzenta) i tutaj nie ma zastosowania. Przez sito idzie sam kształt nazwy:
    bez ścieżek i bez znaków, którymi dałoby się wyjść z katalogu archiwum.
    """
    from apps.submissions.packaging import safe_download_name

    base = safe_download_name(submission_file.original_name)
    return f"{FILES_PREFIX}zad{submission.problem.number}-v{submission.version}-{base}"


def _entries_section(participant) -> list[dict]:
    """Zgłoszenia do etapów wraz z pracami i ich metryką.

    Jedno przejście po wpisach: etap, decyzja o kwalifikacji i prace oddane w tym etapie.
    Rozdzielenie tego na dwie sekcje („wpisy” i „prace”) zmuszałoby czytelnika paczki do
    sklejania ich po identyfikatorach, których w eksporcie świadomie nie ma.
    """
    if participant is None:
        return []
    from apps.competitions.models import StageEntry
    from apps.submissions.models import Submission

    entries = (
        StageEntry.objects.filter(participant=participant)
        .select_related("stage", "stage__edition")
        .order_by("stage__opens_at", "stage_id")
    )
    rows = []
    for entry in entries:
        submissions = (
            Submission.objects.filter(entry=entry)
            .select_related("problem")
            .prefetch_related("files")
            .order_by("problem__number", "version")
        )
        rows.append(
            {
                "edycja": entry.stage.edition.year_label,
                "etap": entry.stage.display_name,
                "status": entry.status,
                "zapisany": _moment(entry.created_at),
                "kwalifikacja_reczna": entry.manual_qualification,
                "prace": [
                    {
                        "zadanie_numer": submission.problem.number,
                        "zadanie_tytul": submission.problem.title,
                        "wersja": submission.version,
                        "oddana": _moment(submission.submitted_at),
                        "po_deadline_w_tolerancji": submission.is_late,
                        "status": submission.status,
                        "pliki": _files_section(submission),
                    }
                    for submission in submissions
                ],
            }
        )
    return rows


def _results_section(participant) -> list[dict]:
    """Wynik uczestnika w każdym etapie z **ogłoszonymi** wynikami: własny wiersz tabeli i oceny.

    Korzystamy z ``apps.results.feedback.participant_feedback``, czyli dokładnie z tego, co
    uczestnik widzi w panelu. To nie jest wygoda – to gwarancja: reguła „co wolno pokazać
    uczestnikowi o jego pracy” ma jedno miejsce, a eksport nie może być drugą, luźniejszą drogą
    do tych samych danych.
    """
    if participant is None:
        return []
    from apps.competitions.models import Stage, StageEntry
    from apps.core.points import points_json
    from apps.results.feedback import participant_feedback

    stage_ids = (
        StageEntry.objects.filter(participant=participant, stage__results_published_at__isnull=False)
        .values_list("stage_id", flat=True)
        .distinct()
    )
    rows = []
    for stage in Stage.objects.filter(pk__in=stage_ids).order_by("opens_at", "id"):
        feedback = participant_feedback(participant, stage)
        if feedback is None:  # pragma: no cover - publikacja zniknęła między zapytaniami
            continue
        rows.append(
            {
                "etap": stage.display_name,
                "wyniki_ogloszone": _moment(stage.results_published_at),
                "moj_wiersz_tabeli": {
                    "suma_punktow": points_json(feedback.published_total),
                    "miejsce": feedback.rank,
                    "wierszy_w_tabeli": feedback.rank_of,
                    "zakwalifikowany": feedback.qualified,
                },
                "oceny_koncowe": [
                    {
                        "zadanie_numer": problem.number,
                        "zadanie_tytul": problem.title,
                        "punkty": points_json(problem.score),
                        "praca_nieoddana": problem.missing,
                        "komentarze_dla_mnie": [
                            {"recenzent": review.label, "komentarz": review.comment}
                            for review in problem.reviews
                        ],
                    }
                    for problem in feedback.problems
                ],
            }
        )
    return rows


def _guardian_section(participant) -> dict | None:
    """Stan zgody opiekuna – ten sam, który uczestnik widzi w panelu."""
    if participant is None:
        return None
    from .guardian import guardian_status

    state = guardian_status(participant)
    record = state.get("record")
    return {
        "stan": state["state"],
        "adres_opiekuna": state["email"] or participant.guardian_email,
        "potwierdzona": _moment(record.given_at) if record is not None else None,
    }


def _preferences_section(user: User) -> dict:
    """Ustawienia interfejsu. Puste pola znaczą „ustawienie przeglądarki”, a nie brak danych."""
    preference = getattr(user, "preference", None)
    return {
        "jezyk": preference.language if preference is not None else "",
        "wysoki_kontrast": bool(preference.high_contrast) if preference is not None else False,
    }


def _participant(user: User):
    """Profil uczestnika **w konkursie tego żądania** albo ``None``.

    Eksport jest odpowiedzią administratora danych na pytanie „co o mnie wiecie”, a
    administratorem jest organizator **jednego** konkursu (``docs/UNIWERSALNY-ETAP-1.md`` § 3.3).
    Wrzucenie do paczki profilu z drugiej olimpiady – z jej szkołą, zgodami i kodem publicznym –
    byłoby oddaniem uczestnikowi danych, których ten organizator nie przetwarza, a przy okazji
    pokazaniem mu ich pod cudzą marką. Konkurs bierzemy z kontekstu, czyli stąd, skąd bierze go
    widok, który tę paczkę wydaje.
    """
    from apps.tenancy.context import current_competition

    from .services import participant_for

    return participant_for(user, current_competition())


def export_payload(user: User) -> dict:
    """Treść ``dane.json`` dla tego konta. Czysta funkcja – niczego nie zapisuje.

    Rozbita na sekcje po **rolach i obiektach**, a nie po tabelach: uczestnik pyta „co o mnie
    wiecie”, a nie „co macie w tabeli ``accounts_participant``”. Sekcja, której konto nie ma
    (profil komitetu przy uczestniku), jest ``null`` – a nie znika – żeby kształt pliku był ten
    sam dla każdego konta i dał się odczytać maszynowo bez zgadywania.
    """
    participant = _participant(user)
    supervisor = getattr(user, "school_supervisor", None)
    return {
        "wersja_formatu": EXPORT_FORMAT_VERSION,
        "wygenerowano": _moment(timezone.now()),
        "konto": _account_section(user),
        "profil_uczestnika": _participant_section(participant),
        "profil_komitetu": _committee_section(getattr(user, "committee_member", None)),
        "profil_opiekuna_szkolnego": _supervisor_section(supervisor),
        "zgody": _consents_section(participant, supervisor),
        "zgoda_opiekuna": _guardian_section(participant),
        "zgloszenia_do_etapow": _entries_section(participant),
        "wyniki_ogloszone": _results_section(participant),
        "wpisy_na_forum": _forum_section(user),
        "powiadomienia_z_forum": _forum_notifications_section(user),
        "zaswiadczenia_statusu_ucznia": _student_status_section(participant),
        "oceny_ai": _ai_section(participant),
        "ustawienia_interfejsu": _preferences_section(user),
    }


def _student_status_section(participant) -> list[dict]:
    """Zaświadczenia o statusie ucznia tego profilu – każda wersja, z decyzją i powodem odrzucenia.

    Sekcja jest w pliku **zawsze** (pusta lista przy konkursie bez tej funkcji), bo kształt pliku ma
    być ten sam dla każdego konta. Wszystkie wersje, a nie tylko bieżąca: każdą z nich uczestnik
    przesłał i każda decyzja dotyczyła jego. Nie ma tu tożsamości koordynatora, który rozpatrzył
    zaświadczenie – to dane pracownika organizatora, a nie uczestnika (ta sama granica, co
    „Recenzent A/B” przy ocenach). Sam plik, o ile jeszcze istnieje, jedzie w ``pliki/``.
    """
    if participant is None:
        return []
    from apps.student_status.models import StudentStatusCertificate

    rows = (
        StudentStatusCertificate.objects.filter(participant=participant)
        .select_related("edition")
        .order_by("edition_id", "version")
    )
    return [
        {
            "edycja": row.edition.year_label,
            "wersja": row.version,
            "biezaca": row.is_current,
            "stan": row.get_status_display(),
            "przeslano": _moment(row.uploaded_at),
            "rozpatrzono": _moment(row.decided_at),
            "powod_odrzucenia": row.rejection_reason or None,
            "plik": {
                "sha256": row.sha256,
                "rozmiar_bajty": row.size_bytes,
                "typ": row.mime,
                "skan_antywirusowy": row.scan_status,
                "w_paczce": row.is_clean,
                "usuniety": _moment(row.file_removed_at),
            },
        }
        for row in rows
    ]


def _ai_section(participant) -> list[dict]:
    """Oceny AI prac tej osoby – reguła w ``apps.ai_grading.services.export_section``.

    Zawsze **fakt** przekazania pracy do podmiotu przetwarzającego (kiedy, komu, jakim modelem):
    odbiorcy danych są informacją, do której osoba ma prawo z art. 15 ust. 1 lit. c RODO,
    niezależnie od tego, co pokazuje ekran. Treść sugestii – wyłącznie tam, gdzie uczestnik widzi
    ją i w panelu (koordynator włączył ją dla etapu, wyniki są ogłoszone): eksport nie może być
    drugą, luźniejszą drogą do tego, czego ekran nie pokazuje. Pusta lista, gdy prace nigdy nie
    wyszły do oceny AI – kształt pliku ma być ten sam dla każdego konta.
    """
    from apps.ai_grading.services import export_section

    return export_section(participant)


def _forum_section(user: User) -> list[dict]:
    """Wypowiedzi tej osoby na forum – **wyłącznie jej własne**, razem ze stanem moderacji.

    Art. 15 RODO pyta o dane **tej** osoby, a nie o rozmowę, w której brała udział. Dlatego jest tu
    treść jej wpisów i temat wątku, w którym stoją, a nie ani jedno cudze zdanie: paczka
    z odpowiedziami innych uczestników byłaby wydaniem ich danych osobie, która o nie nie pytała
    i nie ma do nich prawa (patrz docstring modułu). Z tego samego powodu nie ma tu zgłoszeń
    **cudzych** wpisów, które ta osoba wysłała do moderatora – zgłoszenie mówi o wypowiedzi kogoś
    innego, a nie o zgłaszającym.

    Uzasadnienie moderatora **jest** – i to jest ten sam powód, dla którego istnieje ekran „Twoje
    wpisy”: decyzja o odrzuceniu wypowiedzi dotyczy tej osoby, więc ma prawo ją dostać także
    w paczce, a nie wyłącznie na ekranie, o którym musi pamiętać.

    Wpisy z **każdego** konkursu, a nie tylko z bieżącego: paczkę pobiera konto, a nie uczestnik
    jednego konkursu, i pytanie brzmi „co o mnie wiecie”, a nie „co wiecie o mnie tutaj”. Konto
    bez ani jednego wpisu dostaje pustą listę – kształt pliku ma być ten sam dla każdego konta.
    """
    from apps.forum.models import ForumPost

    posts = (
        ForumPost.objects.filter(author=user)
        .select_related("thread", "thread__category", "competition")
        .order_by("created_at", "id")
    )
    return [
        {
            "konkurs": post.competition.name,
            "dzial": post.thread.category.name,
            "watek": post.thread.title,
            "tresc": post.body,
            "dodany": _moment(post.created_at),
            "poprawiony": _moment(post.edited_at),
            "stan": post.get_status_display(),
            "uzasadnienie_moderatora": post.moderation_note or None,
        }
        for post in posts
    ]


def _forum_notifications_section(user: User) -> dict:
    """Powiadomienia e-mail z forum: ustawienia konta, obserwowane wątki i decyzje czekające na list.

    To są kategorie danych, które rejestr czynności (wersja 1.9, wiersz forum) dopisał razem
    z powiadomieniami – ``apps.forum.notifications``. Tak jak przy wpisach: **wszystkie konkursy**
    i wyłącznie dane tej osoby. Temat wątku stoi przy obserwacji tylko wtedy, gdy ta osoba może go
    przeczytać (wątek opublikowany albo jej własny) – obserwacja wątku, który moderator potem
    odrzucił albo ukrył, nie może wynieść w paczce tematu cudzej, niepublikowanej wypowiedzi.

    Brak wiersza ustawień znaczy „ustawienia domyślne” (``preferences_for``) – paczka mówi wtedy,
    jakie to są wartości, i zaznacza, że konto ich nie zmieniało (``zmienione: null``).
    """
    from apps.forum.models import ForumDecisionNotice, ForumSubscription, ModerationStatus
    from apps.forum.notifications import preferences_for

    preferences = preferences_for(user)
    subscriptions = (
        ForumSubscription.objects.filter(user=user)
        .select_related("thread", "thread__category", "competition")
        .order_by("created_at", "id")
    )
    notices = (
        ForumDecisionNotice.objects.filter(user=user)
        .select_related("competition")
        .order_by("created_at", "id")
    )

    def readable_title(thread) -> str | None:
        if thread.status == ModerationStatus.PUBLISHED or thread.author_id == user.pk:
            return thread.title
        return None

    return {
        "ustawienia": {
            "listy_o_obserwowanych_watkach": preferences.get_frequency_display(),
            "listy_o_kolejce_moderacji": preferences.moderation_digest,
            "zmienione": _moment(preferences.updated_at) if preferences.pk else None,
        },
        "obserwowane_watki": [
            {
                "konkurs": subscription.competition.name,
                "dzial": subscription.thread.category.name,
                "watek": readable_title(subscription.thread),
                "obserwuje": subscription.is_active,
                "od": _moment(subscription.created_at),
                "nowosci_od": _moment(subscription.pending_since),
                "ostatni_list": _moment(subscription.last_notified_at),
            }
            for subscription in subscriptions
        ],
        "decyzje_moderatora_do_powiadomienia": [
            {
                "konkurs": notice.competition.name,
                "decyzja": notice.get_kind_display(),
                "zapisana": _moment(notice.created_at),
                "obsluzona": _moment(notice.handled_at),
            }
            for notice in notices
        ],
    }


@dataclass(frozen=True)
class ExportArchive:
    """Gotowa paczka: otwarty plik tymczasowy ustawiony na początku i liczba plików w środku.

    ``files`` jest jedyną liczbą, która trafia do audytu – nazwy plików nie, bo pochodzą od
    uczestnika i regularnie zawierają jego nazwisko.
    """

    stream: BinaryIO
    files: int


def _copy_file(source: BinaryIO, target: BinaryIO) -> None:
    """Przepisuje strumień kawałkami – paczka nie może rosnąć w pamięci procesu."""
    while True:
        chunk = source.read(COPY_CHUNK)
        if not chunk:
            return
        target.write(chunk)


def _submission_files(participant):
    """Pliki rozwiązań tego uczestnika: pary ``(praca, plik)``, od najstarszej wersji.

    Bierzemy **wszystkie** wersje, a nie tylko ostatnią: uczestnik oddał każdą z nich i każda
    jest jego danymi. Pliki odrzucone przez antywirusa też zostają w metryce (``dane.json``),
    ale nie wchodzą do archiwum – ich treść jest w kwarantannie i nie ma powodu wypuszczać jej
    z powrotem do przeglądarki.
    """
    if participant is None:
        return []
    from apps.submissions.models import Submission

    pairs = []
    submissions = (
        Submission.objects.filter(entry__participant=participant)
        .select_related("problem")
        .prefetch_related("files")
        .order_by("problem__number", "version")
    )
    for submission in submissions:
        for item in submission.files.all():
            if item.is_clean:
                pairs.append((submission, item))
    return pairs


def build_export_zip(user: User) -> ExportArchive:
    """Buduje paczkę ZIP z ``dane.json`` i plikami rozwiązań uczestnika.

    Archiwum powstaje w pliku tymczasowym, a nie w pamięci: rozwiązania finalisty to kilkadziesiąt
    megabajtów, a ``FileResponse`` i tak zamknie strumień po wysłaniu – nie zostaje nic do
    sprzątania nawet wtedy, gdy klient zerwie połączenie w połowie.

    Kompresja jest włączona (``ZIP_DEFLATED``), inaczej niż w paczce prac dla komitetu: tam wpisy
    są w całości skompresowanymi PDF-ami, a tutaj największym pojedynczym wpisem bywa ``dane.json``,
    który kurczy się kilkukrotnie.
    """
    from apps.submissions.storage import get_submission_storage

    participant = _participant(user)
    payload = export_payload(user)
    storage = get_submission_storage()
    stream = tempfile.TemporaryFile()
    count = 0
    try:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                DATA_ENTRY_NAME,
                json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            used: set[str] = set()
            for submission, submission_file in _submission_files(participant):
                name = _zip_name(submission, submission_file)
                if name in used:  # pragma: no cover - trójka (zadanie, wersja, nazwa) jest unikalna
                    continue
                used.add(name)
                source = storage.open(submission_file.object_key)
                try:
                    with archive.open(name, "w") as target:
                        _copy_file(source, target)
                finally:
                    source.close()
                count += 1
            count += _add_student_status_files(archive, participant, used)
    except BaseException:
        stream.close()
        raise
    stream.seek(0)
    return ExportArchive(stream=stream, files=count)


def _add_student_status_files(archive, participant, used: set[str]) -> int:
    """Skany zaświadczeń o statusie ucznia – wyłącznie te, które jeszcze są i przeszły skan.

    Nazwa w paczce powstaje z edycji i wersji (``pliki/zaswiadczenie-status-ucznia-e<id>-v<n>.<ext>``),
    a nie z nazwy od uczestnika – tej serwis nie zapisuje. Brak obiektu w storage nie wywraca eksportu:
    metryka i tak jest w ``dane.json``, a paczka bez jednego pliku jest lepsza niż brak paczki.
    """
    if participant is None:
        return 0
    from apps.student_status.models import StudentStatusCertificate
    from apps.student_status.services import open_scan

    added = 0
    for row in StudentStatusCertificate.objects.filter(participant=participant).order_by(
        "edition_id", "version"
    ):
        if not row.is_clean:
            continue
        name = f"{FILES_PREFIX}zaswiadczenie-status-ucznia-e{row.edition_id}-v{row.version}.{row.extension}"
        if name in used:  # pragma: no cover - para (edycja, wersja) jest unikalna
            continue
        source = open_scan(row)
        if source is None:
            continue
        used.add(name)
        try:
            with archive.open(name, "w") as target:
                _copy_file(source, target)
        finally:
            source.close()
        added += 1
    return added


def export_filename(user: User) -> str:
    """Nazwa pobieranego pliku: ``dane-konta-<kod albo id>-<data>.zip``.

    Bez adresu e-mail i bez nazwiska: plik ląduje w katalogu pobrań, bywa przesyłany dalej
    i widnieje w historii przeglądarki. Uczestnik rozpozna go po kodzie publicznym, czyli po tym
    samym identyfikatorze, którym posługuje się w rozmowie z organizatorem.
    """
    participant = _participant(user)
    marker = participant.public_code if participant is not None else f"konto-{user.pk}"
    return f"dane-konta-{marker}-{timezone.localdate().isoformat()}.zip"


# --- limit częstotliwości -----------------------------------------------------------------------

#: Odstęp między dwoma eksportami jednego konta. Dziesięć minut, bo budowa paczki czyta cały
#: storage uczestnika: przy kliknięciu w kółko jedno konto potrafi zająć worker na kilka minut.
#: Wartość jest tutaj, a nie w ``DEFAULT_THROTTLE_RATES``, bo zapis stawek DRF („10/min”) nie
#: wyraża okresu dziesięciu minut – da się w nim podać liczbę żądań na minutę, godzinę albo dobę.
EXPORT_INTERVAL_SECONDS = 600

#: Prefiks klucza w cache'u. Klucz niesie sam identyfikator konta – żadnego adresu e-mail.
EXPORT_CACHE_PREFIX = "account-export"


def _export_key(user: User) -> str:
    return f"{EXPORT_CACHE_PREFIX}:{user.pk}"


def export_wait_seconds(user: User) -> int:
    """Ile sekund zostało do kolejnego eksportu tego konta (``0`` = można pobierać).

    Licznik siedzi w cache'u, tak samo jak limity formularzy (``apps.web.throttle``), i tak samo
    nie niesie danych osobowych. Wygaśnięcie klucza **jest** końcem limitu – nie trzymamy historii
    prób, bo pytanie brzmi „czy minęło dziesięć minut od ostatniej paczki”, a nie „ile ich było”.
    """
    stamp = cache.get(_export_key(user))
    if stamp is None:
        return 0
    elapsed = timezone.now().timestamp() - float(stamp)
    return max(0, int(EXPORT_INTERVAL_SECONDS - elapsed) + 1)


def mark_exported(user: User) -> None:
    """Zapala licznik odstępu. Wołane po zbudowaniu paczki, a nie przed – nieudany eksport nie
    może zablokować kolejnej próby na dziesięć minut."""
    cache.set(_export_key(user), timezone.now().timestamp(), EXPORT_INTERVAL_SECONDS)


def audit_export(user: User, archive: ExportArchive, *, actor=None, request=None) -> None:
    """Wpis audytowy o wydaniu paczki – **bez treści**, z samymi liczbami.

    Dwie akcje, bo to dwa różne zdarzenia: ``account.exported`` (właściciel pobrał swoje dane)
    i ``account.exported_by_coordinator`` (organizator wydał paczkę cudzego konta). Przy drugim
    pytanie „kto to zrobił” jest pierwszym, jakie pada, a nie ciekawostką.
    """
    by_coordinator = actor is not None and actor.pk != user.pk
    audit(
        actor or user,
        "account.exported_by_coordinator" if by_coordinator else "account.exported",
        user,
        {"user_id": user.pk, "files": archive.files},
        request=request,
    )
