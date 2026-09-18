"""Szesnaście województw jako regiony – każdemu konkursowi w bazie (§ 1.4.3).

Każdy konkurs dostaje **ten sam** zestaw startowy: kraj ``pl``, szesnaście województw jako jego
dzieci i nieaktywny region ``poza-polska``. Innego zestawu, który mógłby się komuś należeć, dziś
nie ma – do etapu 2 podział terytorialny był stałą w kodzie (``Voivodeship``) wspólną dla całej
instalacji.

**Kody są dokładnie dzisiejszymi slugami, nazwy – dzisiejszymi etykietami z diakrytykami,
kolejność – kolejnością deklaracji w ``Voivodeship``.** Dzięki temu backfill ``Participant.region``
i ``CommitteeMember.region`` jest złączeniem po kolumnie ``district``, a nie mapą przepisaną
ręcznie – a mapę przepisaną ręcznie da się pomylić.

**Po tej migracji nic się nie zmienia.** Reguła konfliktu interesów
(``apps.grading.services.has_district_conflict``) sięga do regionów wyłącznie przy włączonej fladze
``custom_regions``; flaga jest domyślnie wyłączona i Konkurs #1 jej nie włącza, więc formularze,
filtry panelu, eksporty i przydział recenzentów dalej czytają ``district``. Backfill jest tu po to,
żeby dzień włączenia flagi **nie był** dniem, w którym reguła konfliktu przestaje działać dla
profili założonych wcześniej: po nim każdy istniejący profil z województwem ma region o tym samym
znaczeniu, więc wynik reguły jest dla każdej istniejącej pary taki sam jak przed migracją.

**Wyjątek od reguły „w migracji tylko modele historyczne”:** ``Voivodeship`` i
``normalize_voivodeship`` importujemy z modułu na żywo. To jest **stała i czysta funkcja**,
a nie model – importujemy dane, nie zachowanie (tak samo jak ``0024`` importuje
``DEFAULT_CONSENTS``). Przepisanie szesnastu par „slug → etykieta” do migracji byłoby drugim
miejscem w systemie, w którym stoi lista województw.

Idempotencja: ``get_or_create`` / ``update_or_create`` po parze (konkurs, kod) i backfill zawężony
do wierszy, które regionu jeszcze nie mają. Migracja puszczona drugi raz – albo puszczona na bazie,
w której organizator dopisał już własne regiony – ustawia zestaw startowy na wartość z listy
województw, nie mnoży wierszy i nie nadpisuje regionu wybranego ręcznie.

Odwrót zeruje ``region_id`` w profilach wskazujących na regiony **z tego zestawu** i kasuje te
regiony; ``district`` zostaje nietknięte, więc cofnięcie nie traci ani jednej informacji. Regiony
dopisane przez organizatora po tej migracji i profile, które na nie wskazują, zostają.

``elidable=False``: ta migracja jest jedynym zapisem tego, skąd wzięły się regiony w bazie,
i nie wolno jej zwinąć przy ``squashmigrations``.
"""

from django.db import migrations

#: Kod kraju, pod którym stoi szesnaście województw. Poziom ``COUNTRY``, pozycja 0 – przed nimi.
COUNTRY_CODE = "pl"
COUNTRY_NAME = "Polska"

#: Region dla uczestników spoza Polski. Powstaje **zawsze**, ale nieaktywny: konkurs, który nie
#: dopuszcza zagranicy, nie pokaże go w formularzu ani razu. ``counts_for_conflict=False``, bo dwóch
#: uczestników z zagranicy nie jest ze sobą w konflikcie z tytułu miejsca zamieszkania.
ABROAD_CODE = "poza-polska"
ABROAD_NAME = "poza Polską"
ABROAD_POSITION = 99


def _starting_codes() -> list[str]:
    """Kody zestawu startowego w kolejności kasowania przy odwrocie: najpierw dzieci, potem kraj."""
    from apps.accounts.models import Voivodeship

    return [*Voivodeship.values, ABROAD_CODE, COUNTRY_CODE]


def create_regions(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    from apps.accounts.models import Voivodeship, normalize_voivodeship

    Competition = apps.get_model("tenancy", "Competition")
    Region = apps.get_model("accounts", "Region")
    Participant = apps.get_model("accounts", "Participant")
    CommitteeMember = apps.get_model("accounts", "CommitteeMember")

    for competition in Competition.objects.order_by("pk"):
        country, _ = Region.objects.get_or_create(
            competition_id=competition.pk,
            code=COUNTRY_CODE,
            defaults={"name": COUNTRY_NAME, "level": "COUNTRY", "position": 0},
        )
        by_code: dict[str, int] = {}
        for position, (code, label) in enumerate(Voivodeship.choices, start=1):
            region, _ = Region.objects.update_or_create(
                competition_id=competition.pk,
                code=code,
                defaults={
                    "name": str(label),
                    "level": "REGION",
                    "parent_id": country.pk,
                    "position": position,
                },
            )
            by_code[code] = region.pk
        Region.objects.update_or_create(
            competition_id=competition.pk,
            code=ABROAD_CODE,
            defaults={
                "name": ABROAD_NAME,
                "level": "COUNTRY",
                "position": ABROAD_POSITION,
                "is_active": False,
                "counts_for_conflict": False,
            },
        )

        for code, region_id in by_code.items():
            Participant.objects.filter(
                competition_id=competition.pk, district=code, region__isnull=True
            ).update(region_id=region_id)
            CommitteeMember.objects.filter(
                competition_id=competition.pk, district=code, region__isnull=True
            ).update(region_id=region_id)

        # Druga tura dla zapisów niekanonicznych. ``accounts.0007`` sprowadziło ``district`` do
        # wartości z listy, ale integracje i importy sprzed tamtej migracji zostawiły w bazie
        # „Mazowieckie” i „woj. mazowieckie”. Reguła konfliktu porównuje je **znormalizowane**, więc
        # profil z takim zapisem musi dostać ten sam region co profil z zapisem kanonicznym –
        # inaczej dzień włączenia flagi zmieniłby wynik reguły dla istniejącej pary.
        for model in (Participant, CommitteeMember):
            remaining = (
                model.objects.filter(competition_id=competition.pk, region__isnull=True)
                .exclude(district__in=["", *by_code])
                .exclude(district__isnull=True)
                .values_list("pk", "district")
            )
            for pk, district in remaining:
                region_id = by_code.get(normalize_voivodeship(district) or "")
                if region_id is not None:
                    model.objects.filter(pk=pk).update(region_id=region_id)


def drop_regions(apps, schema_editor):  # noqa: ARG001 - podpis RunPython
    Region = apps.get_model("accounts", "Region")
    Participant = apps.get_model("accounts", "Participant")
    CommitteeMember = apps.get_model("accounts", "CommitteeMember")
    InvitationCode = apps.get_model("accounts", "InvitationCode")

    codes = _starting_codes()
    doomed = list(Region.objects.filter(code__in=codes).values_list("pk", flat=True))
    if not doomed:
        return
    # Najpierw zdejmujemy wskazania – ``PROTECT`` na ``region`` nie pozwoliłby skasować wiersza,
    # na który patrzy choć jeden profil. ``district`` zostaje, więc informacja się nie gubi.
    for model in (Participant, CommitteeMember, InvitationCode):
        model.objects.filter(region_id__in=doomed).update(region_id=None)
    # Regiony dopisane przez organizatora **pod** krajem z zestawu startowego odczepiamy od rodzica:
    # ``parent`` jest ``CASCADE``, więc bez tego cofnięcie tej migracji zabrałoby przy okazji cudzą
    # pracę. Cofnięcie ma zdjąć zestaw startowy i **wyłącznie** jego.
    Region.objects.filter(parent_id__in=doomed).exclude(pk__in=doomed).update(parent_id=None)
    # Kolejność kasowania: najpierw województwa, na końcu kraj, pod którym stały. ``_starting_codes``
    # zwraca kody właśnie w tej kolejności – dzięki temu kasowanie idzie wiersz po wierszu, bez
    # kaskady, i widać w kodzie, co dokładnie znika.
    for code in codes:
        Region.objects.filter(code=code).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_region"),
    ]

    operations = [
        migrations.RunPython(create_regions, drop_regions, elidable=False),
    ]
