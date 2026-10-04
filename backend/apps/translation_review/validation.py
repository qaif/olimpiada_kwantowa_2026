"""Czy poprawka tłumacza może trafić do interfejsu – reguły z L10N-01 § 5.

Dlaczego tak surowo: Django **nie escapuje** wyniku ``{% translate %}`` ani ``{% blocktranslate %}``.
Napis z szablonu jest ``SafeString``, więc ``gettext`` oddaje tłumaczenie oznaczone jako bezpieczne
i ląduje ono w HTML-u dosłownie – także w atrybucie (``title="{% translate … %}"``). Katalog
z repozytorium jest tekstem zaufanym, bo przechodzi recenzję kodu; poprawka wolontariusza nie jest.
Nie da się jej też po prostu escapować przy zapisie: ten sam ``msgid`` bywa użyty w kodzie Pythona
(``gettext`` → autoescape szablonu → ``&amp;amp;``) i w treści listu e-mail (gołe ``&amp;``).

Rozwiązaniem jest reguła „tłumaczenie to zwykły tekst plus **dokładnie** te elementy, które ma
już źródło”: te same znaczniki HTML (z atrybutami, przepisane z ``msgid``), te same placeholdery,
żadnego nowego ``<``, ``>`` ani cudzysłowu, którego źródło nie miało. Tekst, który przejdzie te
reguły, nie umie niczego, czego nie umiał tekst źródłowy – w żadnym kontekście użycia.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

#: ``%(name)s``, ``%s``, ``%d``, ``%.2f`` … – ten sam wzorzec, co w ``apps/core/tests/test_translations.py``.
PERCENT = re.compile(r"%(?:\([A-Za-z_][A-Za-z0-9_]*\))?[-#0 +]*\d*(?:\.\d+)?[sdifr]")
#: ``{name}`` (``str.format``) – bez podwójnych klamer, które są dosłowną klamrą.
BRACE = re.compile(r"(?<!\{)\{[A-Za-z_][A-Za-z0-9_]*\}(?!\})")
#: Znacznik HTML w najprostszym sensie: od ``<`` do najbliższego ``>``.
TAG = re.compile(r"<[^<>]*>")
#: Nadpisanie kierunku tekstu (LRO/RLO) – pozwala ukryć w napisie inną treść, niż widać
#: („Trojan Source”). Osadzenia i izolaty kierunku zostają, bo potrzebuje ich arabski.
BIDI_OVERRIDES = frozenset("‭‮")
#: Cudzysłowy, które zamykają atrybut HTML. Dozwolone wyłącznie wtedy, gdy ma je źródło – wtedy
#: napis na pewno nie stoi w atrybucie z takim cudzysłowem (zepsułby go już dziś).
QUOTES = ('"', "`")
MAX_LENGTH = 4000


def placeholders(text: str) -> list[str]:
    cleaned = text.replace("%%", "")
    return sorted(PERCENT.findall(cleaned) + BRACE.findall(cleaned))


def _outside_tags(text: str) -> str:
    return TAG.sub("", text)


def _lone(text: str) -> Counter:
    """Znaki formatowania, które **nie** są placeholderem: samotne ``%``, ``{``, ``}``."""
    cleaned = BRACE.sub("", PERCENT.sub("", text.replace("%%", "")))
    return Counter(char for char in cleaned if char in "%{}")


def _placeholders_ok(got: list[str], msgid: str, msgid_plural: str | None, plural_index: int | None) -> bool:
    """Ta sama reguła form mnogich, co w teście katalogów (``test_placeholders_match``)."""
    if msgid_plural is None or plural_index is None:
        return got == placeholders(msgid)
    singular, plural = placeholders(msgid), placeholders(msgid_plural)
    if plural_index == 0:
        # Forma pierwsza bywa w językach bez liczby mnogiej formą jedyną – dopuszczamy oba źródła.
        return got in (singular, plural)
    if got == plural:
        return True
    # Forma „jeden” w językach z kilkoma formami (rosyjskie 21) może pominąć liczbę – ale nie może
    # dołożyć obcej nazwy.
    return bool(got) and set(got) <= set(plural) | set(singular)


def clean_translation(
    text: str,
    *,
    msgid: str,
    msgid_plural: str | None = None,
    plural_index: int | None = None,
) -> str:
    """Znormalizowane tłumaczenie albo ``ValidationError`` z listą wszystkich problemów naraz.

    Normalizacja (zamiast odmowy) tam, gdzie poprawka jest jednoznaczna: końce linii, białe znaki
    na brzegach dopasowane do źródła (``msgfmt`` wymaga zgodności ``\\n`` na początku i końcu)
    i apostrof ASCII → ``’`` w napisie, którego źródło apostrofu nie ma.
    """
    source = msgid_plural if (msgid_plural is not None and plural_index) else msgid
    sources = [msgid] if msgid_plural is None else [msgid, msgid_plural]
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lead = source[: len(source) - len(source.lstrip())]
    trail = source[len(source.rstrip()) :]
    body = text.strip()
    errors: list[str] = []
    if not body:
        raise ValidationError(_("Tłumaczenie nie może być puste."))

    if not any("'" in _outside_tags(item) for item in sources):
        body = "".join(
            part if TAG.fullmatch(part) else part.replace("'", "’")
            for part in re.split(f"({TAG.pattern})", body)
        )
    text = f"{lead}{body}{trail}"

    limit = min(MAX_LENGTH, 4 * len(source) + 200)
    if len(text) > limit:
        errors.append(_("Tłumaczenie jest za długie (najwyżej %(limit)d znaków).") % {"limit": limit})

    for char in sorted(set(text)):
        if char in BIDI_OVERRIDES and not any(char in item for item in sources):
            errors.append(_("Tłumaczenie zawiera niedozwolony znak sterujący kierunkiem tekstu."))
        elif unicodedata.category(char) == "Cc" and (
            char not in "\n\t" or not any(char in item for item in sources)
        ):
            label = {"\n": _("nowa linia"), "\t": _("tabulator")}.get(char, f"U+{ord(char):04X}")
            errors.append(_("Niedozwolony znak sterujący: %(char)s.") % {"char": label})

    tags = Counter(TAG.findall(text))
    if all(tags != Counter(TAG.findall(item)) for item in sources):
        expected = " ".join(TAG.findall(source)) or _("(brak)")
        errors.append(
            _("Znaczniki HTML muszą być dokładnie takie, jak w tekście źródłowym: %(tags)s")
            % {"tags": expected}
        )
    outside = _outside_tags(text)
    source_outside = _outside_tags(source)
    for char in "<>":
        if outside.count(char) != source_outside.count(char):
            errors.append(
                _("Znak „%(char)s” jest dozwolony wyłącznie jako część znacznika z tekstu źródłowego.")
                % {"char": char}
            )
    for char in QUOTES:
        if char in outside and not any(char in _outside_tags(item) for item in sources):
            errors.append(
                _("Użyj cudzysłowów typograficznych (np. “…”, «…», „…”) zamiast znaku %(char)s.")
                % {"char": char}
            )

    if not _placeholders_ok(placeholders(text), msgid, msgid_plural, plural_index):
        expected = " ".join(placeholders(source)) or _("(brak)")
        errors.append(
            _("Tłumaczenie musi zawierać te same zmienne, co tekst źródłowy: %(placeholders)s")
            % {"placeholders": expected}
        )
    if _lone(text) != _lone(source):
        errors.append(
            _(
                "Znaki procentu i nawiasów klamrowych mają w tym napisie znaczenie techniczne – "
                "zapisz je dokładnie tak, jak w tekście źródłowym."
            )
        )

    if errors:
        raise ValidationError(list(dict.fromkeys(errors)))
    return text
