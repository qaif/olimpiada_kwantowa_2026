"""Sprowadza istniejące, wpisywane ręcznie województwa do wartości z listy ``Voivodeship``.

Pole ``district`` było wolnym tekstem, więc w bazie leżą zapisy w kilku konwencjach naraz
(„Mazowieckie”, „mazowiecki”, „woj. mazowieckie”). Po dołożeniu ``choices`` tylko slugi ASCII są
legalne – bez tej migracji stare wiersze przestałyby przechodzić walidację formularzy i admina,
a reguła konfliktu interesów porównywałaby ze sobą dwa różne zapisy tego samego okręgu.

Logika normalizacji jest tu **skopiowana**, a nie zaimportowana z ``apps.accounts.models``:
migracja musi dawać ten sam wynik za rok, także gdy helper w modelach zmieni się albo zniknie.

Wiersz, którego nie umiemy przypisać, zostaje nietknięty i ląduje w ostrzeżeniu na stdout.
Migracja nigdy się z tego powodu nie wywraca: produkcja musi dać się zmigrować, a ręczne
poprawienie kilku egzotycznych wpisów jest zadaniem dla koordynatora, nie dla wdrożenia.
"""

import re
import unicodedata

from django.db import migrations

VOIVODESHIP_VALUES = (
    "dolnoslaskie",
    "kujawsko-pomorskie",
    "lubelskie",
    "lubuskie",
    "lodzkie",
    "malopolskie",
    "mazowieckie",
    "opolskie",
    "podkarpackie",
    "podlaskie",
    "pomorskie",
    "slaskie",
    "swietokrzyskie",
    "warminsko-mazurskie",
    "wielkopolskie",
    "zachodniopomorskie",
)

PREFIX_RE = re.compile(r"^(wojewodztwo|woj\.?)\s+")


# „ł” nie ma rozkładu NFKD (to osobna litera, nie „l” z dokładanym znakiem) – przepisujemy ręcznie.
STROKED_L = str.maketrans({"ł": "l", "Ł": "L"})


def _fold(text):
    decomposed = unicodedata.normalize("NFKD", text.translate(STROKED_L))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


BY_FOLDED = {_fold(value): value for value in VOIVODESHIP_VALUES}


def normalize_voivodeship(text):
    """Kopia ``apps.accounts.models.normalize_voivodeship`` – patrz docstring modułu."""
    if not text:
        return None
    folded = PREFIX_RE.sub("", _fold(str(text)).strip()).strip()
    if not folded:
        return None
    return BY_FOLDED.get(folded) or BY_FOLDED.get(f"{folded}e")


MODELS = (("Participant", "uczestnik"), ("CommitteeMember", "członek komitetu"), ("InvitationCode", "kod"))


def normalise(apps, schema_editor):
    unmapped = []
    for model_name, label in MODELS:
        model = apps.get_model("accounts", model_name)
        for row in model.objects.exclude(district=None).exclude(district="").iterator():
            normalized = normalize_voivodeship(row.district)
            if normalized is None:
                unmapped.append(f"{label} #{row.pk}: {row.district!r}")
                continue
            if normalized != row.district:
                model.objects.filter(pk=row.pk).update(district=normalized)
    if unmapped:
        print(
            "\nUWAGA: nie rozpoznano województwa w {} wierszach – zostały bez zmian "
            "i wymagają ręcznej poprawki:\n  {}".format(len(unmapped), "\n  ".join(unmapped))
        )


def noop(apps, schema_editor):
    """Wstecz nie ma czego odtwarzać: pierwotne brzmienie nie było nigdzie zapamiętane."""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_district_voivodeship_choices"),
    ]

    operations = [
        migrations.RunPython(normalise, noop),
    ]
