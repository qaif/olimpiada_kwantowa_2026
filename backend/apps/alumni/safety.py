"""Ochrona małoletnich w sieci absolwentów (przegląd krytyka, H1 i M2).

Mentor jest dorosłym, którego platforma **poleca** dziecku. Dlatego trzy rzeczy, które dorosły
może napisać do dziecka albo dziecko do dorosłego **poza** moderowaną rozmową, przechodzą przez
organizatora:

- **notatka prośby małoletniego** – czeka na akceptację koordynatora, zanim przeczyta ją mentor
  (mentor widzi do tego czasu sam temat), i nigdy nie jedzie e-mailem,
- **opis i odnośniki mentora** – małoletni widzi je dopiero po akceptacji koordynatora; każda zmiana
  treści unieważnia akceptację (skrót treści, :func:`review_hash`),
- **wzorce danych kontaktowych** (telefon, adres e-mail, @nazwa, komunikatory) w notatce i w opisie –
  zakładają automatyczne zgłoszenie albo znacznik w kolejce koordynatora. To jest sygnał dla
  człowieka, a nie filtr: wzorzec nie blokuje zapisu, bo fałszywe trafienia („rok 2026, sala 112”)
  są nieuniknione, a blokada uczyłaby tylko omijania.

Wiek mentee liczymy ostrożnie (M2): przy akceptacji zapisujemy datę urodzenia; kanał zostaje
ostrzejszy, dopóki **zapisana** data nie da 18 lat, a zmiana daty w profilu osoby w otwartej relacji
trafia do audytu i do koordynatorów (``apps.alumni.signals``).
"""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

#: Wzorce „to wygląda na dane kontaktowe”. Krótkie i jawne – lista jest do przeczytania przez
#: koordynatora, który dostaje zgłoszenie, a nie do zgadywania, co uruchomiło alarm.
CONTACT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("telefon", re.compile(r"(?:\+?\d[\s\-./()]*){7,}")),
    (
        "e-mail",
        re.compile(r"[\w.+-]+\s*(?:@|\(at\)|\[at\])\s*[\w-]+\s*(?:\.|\(dot\)|\[dot\])\s*[a-z]{2,}", re.I),
    ),
    ("@nazwa", re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{3,}")),
    (
        "komunikator",
        re.compile(
            r"\b(?:discord|instagram|insta|telegram|whats\s*app|snap(?:chat)?|signal|messenger|"
            r"tiktok|wechat|viber|kik|t\.me|wa\.me|m\.me)\b",
            re.I,
        ),
    ),
)


def contact_hits(*texts: str) -> list[str]:
    """Nazwy wzorców kontaktowych znalezionych w tekstach (bez powtórzeń, w stałej kolejności)."""
    found = []
    for name, pattern in CONTACT_PATTERNS:
        if any(pattern.search(text or "") for text in texts) and name not in found:
            found.append(name)
    return found


def reviewed_text(profile) -> tuple[str, ...]:
    """Treść profilu, którą małoletni widzi dopiero po akceptacji koordynatora."""
    return (profile.bio or "", profile.linkedin_url or "", profile.github_url or "")


def review_hash(profile) -> str:
    return hashlib.sha256("\x1f".join(reviewed_text(profile)).encode()).hexdigest()


def has_reviewable_content(profile) -> bool:
    return any(reviewed_text(profile))


def approved_for_minors(profile) -> bool:
    """Czy opis i odnośniki tego profilu wolno pokazać małoletniemu.

    Profil, który nie jest mentorem, nie poleca się dzieciom i nie potrzebuje akceptacji; profil bez
    opisu i odnośników nie ma czego ukrywać.
    """
    if not profile.mentor_available or not has_reviewable_content(profile):
        return True
    return bool(profile.reviewed_hash) and profile.reviewed_hash == review_hash(profile)


def needs_review(profile) -> bool:
    return not approved_for_minors(profile)


def stored_minor(mentorship, today=None) -> bool:
    """Czy mentee był/jest małoletni według daty zapisanej przy akceptacji (``None`` = brak zapisu)."""
    from apps.chat.services import is_adult

    if mentorship.mentee_birth_date is None and not mentorship.mentee_birth_year:
        return False
    snapshot = SimpleNamespace(
        birth_date=mentorship.mentee_birth_date, birth_year=mentorship.mentee_birth_year or 0
    )
    return not is_adult(snapshot, today)


def mentee_is_minor(mentorship, today=None) -> bool:
    """Małoletni **teraz** albo według daty z chwili akceptacji – ostrzejsza z dwóch odpowiedzi."""
    from apps.chat.services import is_adult

    return (not is_adult(mentorship.mentee, today)) or stored_minor(mentorship, today)


def mentor_is_adult(mentorship, profile=None, today=None) -> bool:
    """Pełnoletni **teraz** albo potwierdzony przy dołączeniu – ostrzejsza odpowiedź dla nadzoru."""
    from apps.chat.services import is_adult

    if is_adult(mentorship.mentor, today):
        return True
    if profile is None:
        profile = getattr(mentorship.mentor, "alumni_profile", None)
    return bool(profile is not None and profile.adult_confirmed_at)
