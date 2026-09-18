"""Zgody ze stałej jako wiersze w bazie – każdemu konkursowi (§ 1.1.2).

Konkurs #1 dostaje **dokładnie dzisiejszy** zestaw: te same cztery zgody, w tej samej kolejności,
z tym samym brzmieniem, tymi samymi slugami dokumentów, tymi samymi wersjami i tą samą regułą
wymagalności. Każdy inny konkurs stojący już w bazie dostaje ten sam zestaw startowy – bo innego
dziś nie ma: do etapu 2 zgody były stałą w kodzie wspólną dla całej instalacji.

**Po tej migracji nic się nie zmienia.** Zestaw zgód czyta ``apps.accounts.consents.consent_set``,
a ta funkcja sięga do tabeli wyłącznie przy włączonej fladze ``per_competition_consents``. Flaga
jest domyślnie wyłączona i Konkurs #1 jej nie włącza (decyzja organizatora D8): wiersze leżą
i czekają na pierwszą nowelizację regulaminu, a ich **równości ze stałą** pilnuje test
``apps/accounts/tests/test_consent_definitions.py::test_consent_definitions_match_the_constant``
(reguła § 0.7: migracja zamieniająca stałą na wiersz ma w tym samym commicie test porównujący
wiersz ze stałą, pole po polu).

**Wyjątek od reguły „w migracji tylko modele historyczne”:** ``DEFAULT_CONSENTS`` importujemy
z modułu na żywo. To jest **stała modułu**, a nie model – importujemy dane, nie zachowanie.
Alternatywą byłoby przepisanie czterech oświadczeń do migracji, czyli piąte miejsce w systemie,
w którym stoi treść zgody. Przepisanie da się pomylić; import nie.

Idempotencja: ``update_or_create`` po parze (konkurs, rodzaj). Migracja puszczona drugi raz –
albo puszczona na bazie, w której ktoś już wpisał definicje ręcznie – ustawia wiersz na wartość
ze stałej i nie mnoży duplikatów.

Odwrót kasuje **wyłącznie definicje**. ``ConsentRecord`` zostaje nietknięty i to jest cała różnica
między definicją a dowodem: definicja mówi, o co pytamy dzisiaj, dowód – na co ktoś zgodził się
wtedy. Kasowanie jest zawężone do rodzajów ze stałej, żeby cofnięcie tej migracji nie zabrało
definicji dopisanych przez organizatora po jej wykonaniu.

``elidable=False``: ta migracja jest jedynym zapisem tego, skąd wzięła się treść zgód w bazie,
i nie wolno jej zwinąć przy ``squashmigrations``.
"""

from django.db import migrations

#: Pola przepisywane ze stałej do wiersza. Lista stoi tutaj, a nie w pętli, żeby dodanie pola do
#: ``Consent`` i zapomnienie o nim w migracji było widoczne w jednym miejscu.
FIELDS = (
    "field_name",
    "text",
    "link_text",
    "document_slug",
    "version",
    "required",
    "required_for_minor",
    "help_text",
    "missing_message",
)


def write_definitions(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    from apps.accounts.consents import DEFAULT_CONSENTS

    Competition = apps.get_model("tenancy", "Competition")
    ConsentDefinition = apps.get_model("accounts", "ConsentDefinition")

    for competition in Competition.objects.order_by("pk"):
        for order, consent in enumerate(DEFAULT_CONSENTS):
            defaults = {name: getattr(consent, name) for name in FIELDS}
            defaults["ordering"] = order
            defaults["is_active"] = True
            ConsentDefinition.objects.update_or_create(
                competition_id=competition.pk,
                kind=str(consent.kind),
                defaults=defaults,
            )


def drop_definitions(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    from apps.accounts.consents import DEFAULT_CONSENTS

    ConsentDefinition = apps.get_model("accounts", "ConsentDefinition")
    ConsentDefinition.objects.filter(kind__in=[str(consent.kind) for consent in DEFAULT_CONSENTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0023_consent_definitions"),
    ]

    operations = [
        migrations.RunPython(write_definitions, drop_definitions, elidable=False),
    ]
