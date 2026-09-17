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

**Czego tu jeszcze nie ma i dlaczego.** Etapy (``stages``) i dokumenty (``documents``) są opisane,
ale ``create_competition`` ich jeszcze nie zakłada: ``competitions.Edition`` dostaje klucz obcy do
konkursu dopiero w wydaniu B (T3), a szablony dokumentów w etapie 2 (T4). Dane stoją tu wcześniej
świadomie — szablon ma być **jednym** opisem konkursu, a nie opisem rozsypanym po trzech wydaniach.
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
#:     etapy w kolejności: ``(kind, tytuł, dni od poprzedniego terminu)``. Odstępy, nie daty —
#:     daty wpisuje koordynator w panelu, bo to jego harmonogram, a nie nasz.
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
            ("ELIM", "Eliminacje", 0),
            ("DISTRICT", "Etap wojewódzki (rozmowa)", 45),
            ("FINAL", "Finał", 45),
            ("TRAINING", "Trening", 0),
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
            ("ELIM", "Zawody I stopnia (szkolne)", 0),
            ("DISTRICT", "Zawody II stopnia (okręgowe)", 60),
            ("FINAL", "Zawody III stopnia (centralne)", 60),
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
