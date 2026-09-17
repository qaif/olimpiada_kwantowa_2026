"""Katalog szablonów startowych konkursu: ``kwantowa``, ``przedmiotowa``, ``pusty``.

Moduł jest **danymi**, nie kodem: same słowniki, zero zapytań do bazy, zero importów modeli.
Powód jest praktyczny. Szablon odpowiada na pytanie „co domyślnie ma nowy konkurs”, a na to pytanie
odpowiada się przy czytaniu, a nie przy debugowaniu — gdyby wartości powstawały w funkcjach,
odpowiedź trzeba by za każdym razem uruchomić. Wołający (``manage.py create_competition``) zna
sposób zakładania konkursu; katalog zna wyłącznie wartości.

**Czym szablon nie jest.** Szablon to nie seed. Komendy ``seed_cms``, ``seed_regulamin``,
``seed_legacy_content``, ``seed_partners`` i ``seed_edition_kwantowa`` są narzędziami importu treści
**Olimpiady Kwantowej** — wpisują konkretne akapity i konkretne daty z harmonogramu organizatora.
Szablon ``kwantowa`` odwzorowuje **strukturę** tamtej konfiguracji (etapy, formaty plików, komplet
zgód), a nie jej treść: nowy konkurs dostaje puste strony i puste terminy, bo cudza treść w cudzym
serwisie jest błędem, nawet gdy przechodzi testy (``docs/UNIWERSALNY-ETAP-1.md`` § 4.5).

**Terminy są odstępami, nie datami.** Etap opisujemy dwiema liczbami dni (``opens_after_days``,
``length_days``), bo szablon nie zna harmonogramu organizatora i nie ma prawa go zgadywać.
``create_competition`` odkłada te odstępy od **dnia bazowego** (pierwszy dzień następnego miesiąca)
i zapisuje wynik jako wartość początkową; komplet terminów etapu należy potem do koordynatora,
który poprawia go w panelu. Data w tym pliku znaczyłaby, że konkurs założony w marcu i konkurs
założony w listopadzie dostają ten sam nieaktualny rocznik.

**Czego tu nadal nie ma i dlaczego.** ``documents`` to lista kontrolna, a nie treść: szablony
dokumentów z podstawieniami są etapem 2 (§ 5.3), więc komenda wypisuje slugi, a wpisuje je redakcja.
``upload_formats`` i ``consents`` też są na razie listą kontrolną — formaty plików stoją na
**zadaniu** (``competitions.Problem.allowed_formats``, a zadań szablon nie zakłada), a zgody na
stałej ``apps.accounts.consents.CONSENTS``, dopóki nie przełączy ich flaga ``per_competition_consents``
(etap 2). Dane stoją tu mimo to, bo szablon ma być **jednym** opisem konkursu, a nie opisem
rozsypanym po wydaniach.
"""

from __future__ import annotations

#: Nazwa szablonu odwzorowującego dzisiejszą Olimpiadę Kwantową (struktura, nie treść).
TEMPLATE_KWANTOWA = "kwantowa"
#: Typowa olimpiada przedmiotowa MEN: trzy stopnie, jeden format pliku, podstawowy zestaw zgód.
TEMPLATE_PRZEDMIOTOWA = "przedmiotowa"
#: Nic poza szkieletem serwisu — organizator układa etapy i dokumenty sam.
TEMPLATE_PUSTY = "pusty"

#: Sekcje drugiego poziomu drzewa stron zakładane dla **każdego** konkursu. Te same slugi, co
#: ``cms.0002_initial_tree`` dla Konkursu #1, w tej samej kolejności — kolejność wyznacza menu.
#: Model podajemy napisem (``"cms.NewsIndexPage"``), a nie klasą: to jest katalog danych i nie ma
#: powodu, żeby jego zaimportowanie ciągnęło za sobą modele Wagtaila.
#:
#: ``dokumenty`` (``cms.DocumentIndexPage``) jest w zestawie, bo do tej sekcji prowadzą odnośniki
#: w zgodach przy rejestracji (``apps/accounts/consents.py``, ``document_slug``). Sekcja powstaje
#: pusta: regulamin i klauzulę RODO wpisuje organizator, a podstawienia w szablonach dokumentów
#: są etapem 2 (§ 5.3).
DEFAULT_PAGES: tuple[tuple[str, str, str], ...] = (
    ("cms.NewsIndexPage", "aktualnosci", "Aktualności"),
    ("cms.ProblemsPage", "zadania", "Zadania"),
    ("cms.DocumentIndexPage", "dokumenty", "Dokumenty"),
    ("cms.ArchiveIndexPage", "archiwum", "Archiwum"),
    ("cms.ResultsPage", "wyniki", "Wyniki"),
)

#: Znaczenie kluczy jednego etapu w ``stages``. Opis stoi osobno, bo to jedyne pole szablonu, które
#: nie jest napisem ani listą napisów — a od niego zależy oś czasu zakładanej edycji.
#:
#: ``kind``
#:     wartość ``apps.competitions.models.StageKind`` (``"ELIM"``, ``"DISTRICT"``, ``"FINAL"``,
#:     ``"TRAINING"``). Napis, a nie stała klasy: katalog jest danymi i nie importuje modeli.
#: ``name``
#:     nazwa startowa etapu. Koordynator zmienia ją w panelu; ``--sync-dates`` (seed Konkursu #1)
#:     jej nie rusza i tutaj też jest wartością początkową, a nie ustaleniem.
#: ``format``
#:     wartość ``apps.competitions.models.StageFormat`` (``"SUBMISSIONS"``, ``"INTERVIEW"``,
#:     ``"QUIZ"``). Forma decyduje o tym, **co** uczestnik w etapie robi, więc musi być w szablonie:
#:     etap wojewódzki Olimpiady Kwantowej jest rozmową, a nie arkuszem zadań.
#: ``opens_after_days``
#:     ile dni po terminie oddania **poprzedniego** etapu otwiera się ten. Dla pierwszego etapu
#:     w kolejności liczy się od dnia bazowego (patrz docstring modułu).
#: ``length_days``
#:     ile dni trwa okno oddawania prac (otwarcie 00:00, termin 23:59 czasu polskiego — te same
#:     godziny brzegowe, co w ``seed_edition_kwantowa``). ``None`` znaczy **piaskownica bez
#:     terminu**: etap treningowy jest otwarty bez końca (``competitions.TRAINING_DEADLINE``),
#:     nie kwalifikuje nikogo i nie przesuwa osi czasu następnego etapu.
STAGE_FIELDS: tuple[str, ...] = ("kind", "name", "format", "opens_after_days", "length_days")

#: Szablony. Każdy klucz jest wartością argumentu ``--from-template``.
#:
#: Znaczenie pól:
#:
#: ``label``
#:     nazwa dla człowieka — wypisywana w pomocy komendy i w podsumowaniu.
#: ``name_pattern`` / ``short_name_pattern``
#:     wzorce nazwy z jednym podstawieniem ``{name}``. Wzorzec, a nie stała, bo nazwę własną podaje
#:     organizator, a szablon wie tylko, czy ją czymś opakować (``pusty`` nie opakowuje niczym).
#: ``email_subject_prefix_pattern``
#:     prefiks tematu listu; ``{short_name}`` = nazwa skrócona konkursu. Wzorzec odwzorowuje
#:     dzisiejszy ``EMAIL_SUBJECT_PREFIX`` (``"[Olimpiada Kwantowa] "``), łącznie ze spacją na końcu.
#: ``accent_colour``
#:     kolor akcentu w zapisie szesnastkowym albo ``""`` (= motyw domyślny serwisu).
#: ``feature_flags``
#:     **różnice** wobec ``apps.tenancy.models.FEATURE_DEFAULTS``. Pusty słownik znaczy „domyślnie”,
#:     czyli dokładnie tyle, co dziś — i taki ma zostać, dopóki flagi nie zaczną czegoś przełączać.
#: ``pages``
#:     sekcje drugiego poziomu (patrz ``DEFAULT_PAGES``).
#: ``stages``
#:     etapy w kolejności, każdy jako słownik (patrz ``STAGE_FIELDS``). Odstępy, nie daty — daty
#:     wpisuje koordynator w panelu, bo to jego harmonogram, a nie nasz.
#: ``upload_formats``
#:     rozszerzenia plików przyjmowanych w zgłoszeniu.
#: ``consents``
#:     rodzaje zgód (``apps.accounts.consents.ConsentKind``) pokazywanych przy rejestracji.
#: ``documents``
#:     slugi dokumentów, które konkurs musi mieć, zanim otworzy rejestrację — to są cele odnośników
#:     w zgodach. Zakłada je etap 2 (T4); tutaj są listą kontrolną wypisywaną przez komendę.
#: ``safe_seeds``
#:     komendy, które wolno uruchomić dla **nowego** konkursu. Wolno wyłącznie tym, które są
#:     globalne i idempotentne — dziś jest to sam ``seed_schools`` (wykaz SIO/RSPO jest wspólny dla
#:     całej instalacji, a bez niego wyszukiwarka szkół w rejestracji nie ma czego pokazać).
#:     Seedy treści **nigdy** tu nie trafią: nadpisują strony treścią Olimpiady Kwantowej.
TEMPLATES: dict[str, dict] = {
    TEMPLATE_KWANTOWA: {
        "label": "Olimpiada Kwantowa (struktura dzisiejszej konfiguracji)",
        "name_pattern": "{name}",
        "short_name_pattern": "{name}",
        "email_subject_prefix_pattern": "[{short_name}] ",
        "accent_colour": "",
        "feature_flags": {},
        "pages": DEFAULT_PAGES,
        "stages": (
            # Odstępy odwzorowują kształt I edycji: dwa etapy zdalne po ok. dwa miesiące, między
            # nimi jeden dzień przerwy, a finał — zjazd stacjonarny — kilka miesięcy później.
            # Liczby są okrągłe **celowo**: dokładne daty z harmonogramu organizatora stoją
            # w ``seed_edition_kwantowa`` i należą do Konkursu #1, a nie do każdego nowego konkursu.
            {
                "kind": "ELIM",
                "name": "Eliminacje",
                "format": "SUBMISSIONS",
                "opens_after_days": 0,
                "length_days": 60,
            },
            {
                "kind": "DISTRICT",
                "name": "Etap wojewódzki (rozmowa)",
                "format": "INTERVIEW",
                "opens_after_days": 1,
                "length_days": 60,
            },
            {
                # Cztery dni zjazdu (otwarcie pierwszego dnia, termin czwartego) — tyle, ile trwa
                # finał w Krakowie. Godziny sesji egzaminacyjnej ustawia koordynator.
                "kind": "FINAL",
                "name": "Finał",
                "format": "SUBMISSIONS",
                "opens_after_days": 120,
                "length_days": 3,
            },
            {
                # Piaskownica: otwarta od dnia bazowego, bez terminu, poza kwalifikacją i poza
                # publiczną osią czasu (``StageKind.TRAINING``).
                "kind": "TRAINING",
                "name": "Trening",
                "format": "SUBMISSIONS",
                "opens_after_days": 0,
                "length_days": None,
            },
        ),
        "upload_formats": ("pdf", "ipynb", "py", "jpg"),
        "consents": ("TERMS", "PRIVACY", "GUARDIAN", "PUBLISH_NAME"),
        "documents": ("regulamin", "rodo", "zgoda-opiekuna", "standardy-ochrony-maloletnich"),
        "safe_seeds": ("seed_schools",),
    },
    TEMPLATE_PRZEDMIOTOWA: {
        "label": "Olimpiada przedmiotowa (trzy stopnie, jeden format pliku)",
        "name_pattern": "{name}",
        "short_name_pattern": "{name}",
        "email_subject_prefix_pattern": "[{short_name}] ",
        "accent_colour": "",
        "feature_flags": {},
        "pages": DEFAULT_PAGES,
        "stages": (
            # Trzy stopnie po dwa miesiące, jeden dzień przerwy między nimi. Rozporządzenie MEN
            # o olimpiadach terminów nie narzuca (ustala je regulamin konkursu), więc te odstępy są
            # wyłącznie sensowną wartością początkową do poprawienia w panelu.
            {
                "kind": "ELIM",
                "name": "Zawody I stopnia (szkolne)",
                "format": "SUBMISSIONS",
                "opens_after_days": 0,
                "length_days": 60,
            },
            {
                "kind": "DISTRICT",
                "name": "Zawody II stopnia (okręgowe)",
                "format": "SUBMISSIONS",
                "opens_after_days": 1,
                "length_days": 60,
            },
            {
                "kind": "FINAL",
                "name": "Zawody III stopnia (centralne)",
                "format": "SUBMISSIONS",
                "opens_after_days": 1,
                "length_days": 60,
            },
        ),
        "upload_formats": ("pdf",),
        # Bez zgody na publikację nazwiska: olimpiada przedmiotowa ogłasza wyniki na podstawie
        # rozporządzenia, a nie zgody — pytanie o nią byłoby pytaniem o coś, czego i tak nie
        # respektujemy. Zgoda opiekuna zostaje: uczestnicy bywają niepełnoletni.
        "consents": ("TERMS", "PRIVACY", "GUARDIAN"),
        "documents": ("regulamin", "rodo", "zgoda-opiekuna"),
        "safe_seeds": ("seed_schools",),
    },
    TEMPLATE_PUSTY: {
        "label": "Pusty (sam szkielet serwisu)",
        "name_pattern": "{name}",
        "short_name_pattern": "{name}",
        "email_subject_prefix_pattern": "[{short_name}] ",
        "accent_colour": "",
        "feature_flags": {},
        "pages": DEFAULT_PAGES,
        "stages": (),
        "upload_formats": ("pdf",),
        # Dwie zgody są minimum, bez którego nie wolno przyjąć zgłoszenia: akceptacja regulaminu
        # i klauzula informacyjna RODO. Reszta należy do organizatora.
        "consents": ("TERMS", "PRIVACY"),
        "documents": ("regulamin", "rodo"),
        # Konkurs bez etapów nie otwiera rejestracji, więc wykaz szkół nie jest mu na nic
        # potrzebny — a wdrożenie i tak odświeża go osobno (``scripts/deploy.sh``, krok 6/8).
        "safe_seeds": (),
    },
}

#: Nazwy szablonów w kolejności od najbogatszego do pustego — tyle, ile potrzebuje ``choices``
#: argumentu komendy i komunikat o błędnej nazwie.
TEMPLATE_CHOICES: tuple[str, ...] = (TEMPLATE_KWANTOWA, TEMPLATE_PRZEDMIOTOWA, TEMPLATE_PUSTY)
