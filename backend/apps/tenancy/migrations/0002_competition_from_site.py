"""Konkurs #1 powstaje z **danych już stojących w bazie**, a nie z seedów.

Ograniczenie nadrzędne całego etapu brzmi: „zachowaj działającą i skonfigurowaną obecną Olimpiadę
Kwantową” (``docs/UNIWERSALNY-ETAP-1.md`` § 0). Dlatego ta migracja czyta produkcję zamiast wpisywać
literały: witrynę bierze z ``wagtailcore.Site`` (tej samej, którą Wagtail serwuje), markę i dane
organizatora z ``cms.SiteSettings`` (tych samych, które widzi redaktor w ``/cms/``), a nadawcę
listów z ustawień instalacji. Jedynym literałem jest identyfikator ``kwantowa``.

Czego migracja **nie** robi: nie tworzy stron, nie uruchamia żadnego ``seed_*``, nie dotyka
``wagtailcore.Page``, nie zmienia slugów ani ``Site.hostname``, nie **zapisuje** ``SiteSettings``
(czyta je) i nie tworzy ani nie kasuje rewizji stron. Po jej wykonaniu serwis odpowiada dokładnie
tak samo jak przed nim – konkurs istnieje, ale nikt go jeszcze nie czyta.

Baza pusta (świeża instalacja, baza testowa) jest obsłużona tą samą drogą: brak ``SiteSettings``
znaczy „weź nazwę z witryny”, a brak witryny – „nie ma z czego zrobić konkursu”, i wtedy migracja
nic nie robi. Tworzenie konkursu bez witryny byłoby wpisaniem danych, których nikt nie podał.
"""

from django.conf import settings
from django.db import migrations

#: Jedyny literał w tej migracji. Identyfikator konkursu jest kluczem w adresach komend
#: (``manage.py create_competition --slug``) i w eksportach, więc nie może zależeć od tego, co
#: akurat stoi w ``SiteSettings`` – tam redaktor ma prawo zmienić każdą literę.
SLUG = "kwantowa"

#: Pola ``Competition`` wypełniane wprost z ``cms.SiteSettings``: (pole konkursu, pole ustawień).
#: Tabela zamiast ciągu przypisań, bo to jest **mapowanie**, a nie logika – i ma się dać
#: porównać wzrokiem z tabelą w § 0.1 dokumentu.
FROM_SITE_SETTINGS = (
    ("short_name", "site_name"),
    ("tagline", "tagline"),
    ("organizer_name", "organizer_name"),
    ("organizer_address", "organizer_address"),
    ("organizer_registry", "organizer_registry"),
    ("contact_email", "contact_email"),
    ("contact_phone", "contact_phone"),
    ("organizer_url", "contact_url"),
)


def create_competition(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    Site = apps.get_model("wagtailcore", "Site")
    SiteSettings = apps.get_model("cms", "SiteSettings")

    if Competition.objects.exists():
        # Idempotencja: powtórzone ``migrate`` na bazie, gdzie konkurs już jest, nie ma prawa
        # dołożyć drugiego właściciela tych samych danych.
        return

    site = Site.objects.filter(is_default_site=True).first() or Site.objects.order_by("pk").first()
    if site is None:
        # Baza bez witryny to baza, w której nie postawiono jeszcze drzewa stron (``cms.0002``).
        # Konkurs bez witryny nie miałby ani strony głównej, ani domeny – zostawiamy to komendzie
        # ``create_competition``, która zakłada jedno i drugie świadomie.
        return

    row = SiteSettings.objects.filter(site=site).first()
    values = {
        # Nazwa konkursu: ustawienia serwisu, potem nazwa witryny, na końcu nazwa instalacji.
        # Trzy źródła, bo każde następne jest tym, które postawiła migracja wcześniejsza –
        # i wszystkie trzy niosą dziś ten sam napis.
        "name": (getattr(row, "site_name", "") or site.site_name or settings.WAGTAIL_SITE_NAME),
        "slug": SLUG,
        # Logotyp jest **wskazaniem** tego samego obrazu z biblioteki, a nie kopią pliku:
        # redaktor podmieniający znak w stopce podmienia go w jednym miejscu.
        "logo_id": getattr(row, "organizer_logo_id", None),
        # Nadawca i prefiks tematu z ustawień instalacji – dziś globalnych. Wpisujemy je do
        # konkursu, żeby kod poza żądaniem (zadania Celery) miał jedno źródło, a nie dwa.
        "from_email": settings.DEFAULT_FROM_EMAIL,
        "email_subject_prefix": settings.EMAIL_SUBJECT_PREFIX,
        # Domena dublowana z witryny celowo – patrz docstring pola w modelu.
        "primary_domain": site.hostname,
        # Reszta to wartości domyślne modelu wpisane **jawnie**: „tak jak dziś” ma być widoczne
        # w migracji, a nie do odtworzenia z definicji pola sprzed kilku wydań.
        "routing_mode": "DOMAIN",
        "path_prefix": "",
        "feature_flags": {},
        "is_active": True,
        "default_language": "pl",
    }
    for field, source in FROM_SITE_SETTINGS:
        values[field] = getattr(row, source, "") or ""

    Competition.objects.create(site=site, **values)


def remove_competition(apps, schema_editor):
    """Wycofanie kasuje **wyłącznie** konkurs założony tutaj, rozpoznany po identyfikatorze.

    Konkursy założone później komendą zostają: cofnięcie tej migracji jest cofnięciem jednego
    kroku wdrożenia, a nie czyszczeniem instalacji.
    """
    apps.get_model("tenancy", "Competition").objects.filter(slug=SLUG).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0001_initial"),
        # Stan ``cms`` z logotypem organizatora w ustawieniach serwisu: bez tej zależności model
        # historyczny ``SiteSettings`` nie miałby pola, z którego bierze się ``logo``.
        ("cms", "0011_sitesettings_organizer_logo_alter_contentpage_body_and_more"),
    ]

    operations = [
        # ``elidable=False``: tej migracji nie wolno zwinąć przy ``squashmigrations``. Jest
        # jedynym miejscem, w którym Konkurs #1 powstaje z danych produkcji.
        migrations.RunPython(create_competition, remove_competition, elidable=False)
    ]
