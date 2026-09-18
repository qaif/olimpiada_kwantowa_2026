"""Formularz ekranu „Nowy konkurs” — nazwa, identyfikator, szablon i marka startowa.

Osobny moduł od ``apps.web.competition_forms`` (ekran „Ustawienia konkursu”), bo to są dwa różne
formularze o dwóch różnych właścicielach: tamten zmienia **istniejący** wiersz i jest ``ModelForm``,
ten opisuje **czynność** i jest zwykłym ``Form``. Zakładanie konkursu to kilkanaście wierszy
w pięciu aplikacjach (``apps/tenancy/provisioning.py``), więc ``ModelForm`` po ``Competition``
byłby formularzem jednego z tych wierszy udającym formularz całości — i pierwszym miejscem, w
którym ktoś dopisałby ``form.save()``.

**Identyfikator jest tu zarazem etykietą subdomeny** i to jest cała trudność tego formularza.
Wartość wchodzi jednocześnie do trzech światów o trzech różnych zbiorach zakazanych napisów:

- **DNS**: nazwa hosta, czyli ``[a-z0-9-]`` bez myślnika na brzegach (RFC 1123). Długość 3–30
  znaków jest nasza, nie normy: krócej niż trzy znaki nie da się przeczytać w adresie, a dłużej
  niż trzydzieści nikt nie przepisze z plakatu,
- **infrastruktura platformy**: ``www``, ``mail``, ``meet``, ``s3``, ``monitor`` i reszta nazw,
  które na tej domenie **już coś znaczą** albo znaczyć będą. Konkurs pod ``mail`` przejąłby nazwę,
  której MX-y i SPF używają do poczty całej platformy, a ``www`` — przekierowanie na stronę
  główną. Objaw byłby za każdym razem inny i za każdym razem nie do odwrócenia jednym kliknięciem,
- **adresy aplikacji**: ``apps.cms.models.RESERVED_SLUGS``. Slug konkursu jest zarazem slugiem
  strony głównej w drzewie stron Wagtaila, więc lista zajętych pierwszych segmentów obowiązuje
  go tak samo, jak prefiks ścieżki (``Competition.clean``).

Czwarte sprawdzenie jest bazodanowe: identyfikator zajęty przez inny konkurs, host zajęty przez
inną witrynę. Powtarza je czynność zakładająca konkurs (i **ona** jest tu regułą — sprawdza to, co
sprawdzi baza, w tej samej transakcji), ale formularz musi umieć powiedzieć „ta nazwa jest zajęta”
**przy polu**, a nie komunikatem nad całą stroną po nieudanym zapisie.

Czego w formularzu nie ma: domeny, trybu adresowania i prefiksu ścieżki. Adres wynika z
identyfikatora (``<slug>.<SITE_DOMAIN>``) i to jest sens tego ekranu — konkurs zakładany z panelu
nie wymaga niczego od administratora serwera. Konkurs pod **własną** domeną zakłada się nadal
komendą ``manage.py create_competition``, bo wymaga wpisu w DNS-ie, którego panel nie zrobi.
"""

from __future__ import annotations

import re

from django import forms
from django.conf import settings
from wagtail.models import Site

from apps.tenancy.models import Competition, validate_hex_colour
from apps.tenancy.templates_catalog import TEMPLATE_CHOICES, TEMPLATES

#: Kształt etykiety subdomeny: małe litery, cyfry i myślnik, 3–30 znaków. Wielkie litery sprowadzamy
#: do małych **przed** sprawdzeniem: nazwa hosta jest nieczuła na wielkość, więc ``Fizyczna``
#: i ``fizyczna`` są tym samym adresem, a odmowa byłaby odmową z powodu, którego w adresie nie
#: widać. Zmiana jest jawna, bo podgląd pokazuje adres, który naprawdę powstanie.
SLUG_RE = re.compile(r"^[a-z0-9-]{3,30}$")

#: Nazwy, które na domenie platformy już coś znaczą (albo znaczyć będą) i dlatego nie mogą zostać
#: identyfikatorem konkursu. Lista jest jawna, a nie wyprowadzana z DNS-u: rekordu, którego
#: jeszcze nie ma, nie da się odpytać, a nazwa raz wydana konkursowi zostaje w linkach na lata.
#:
#: Skąd te, a nie inne: ``www`` (przekierowanie na stronę główną), ``mail``/``smtp``/``imap``/
#: ``pop``/``webmail``/``autodiscover``/``autoconfig`` (poczta i jej autokonfiguracja),
#: ``ns``/``ns1``/``ns2`` (serwery nazw), ``s3`` (bucket mediów), ``meet`` (Jitsi),
#: ``monitor``/``status`` (monitoring i strona statusu), ``ftp`` (zwyczajowa nazwa), reszta —
#: segmenty, które w tym serwisie są adresami aplikacji.
RESERVED_LABELS = frozenset(
    {
        "admin",
        "api",
        "autoconfig",
        "autodiscover",
        "cms",
        "ftp",
        "imap",
        "internal",
        "mail",
        "media",
        "meet",
        "monitor",
        "ns",
        "ns1",
        "ns2",
        "pop",
        "s3",
        "setup",
        "smtp",
        "static",
        "status",
        "webmail",
        "www",
    }
)


def platform_host(slug: str) -> str:
    """Adres, pod którym stanie konkurs o tym identyfikatorze: ``<slug>.<SITE_DOMAIN>``."""
    return f"{slug}.{settings.SITE_DOMAIN}".lower()


def reserved_labels() -> frozenset[str]:
    """Pełna lista zakazanych etykiet: infrastruktura platformy **i** adresy aplikacji.

    Import jest lokalny, bo ``apps.cms.models`` ciągnie modele Wagtaila, a ten moduł bywa
    wczytywany przy budowaniu mapy adresów — ta sama ostrożność, co w ``Competition.clean``.
    """
    from apps.cms.models import RESERVED_SLUGS

    return RESERVED_LABELS | RESERVED_SLUGS


class NewCompetitionForm(forms.Form):
    """Komplet danych, których potrzebuje ``create_competition_from_template``.

    Formularz **nie zakłada konkursu** i nie ma metody, która by to robiła: jego zadaniem jest
    powiedzieć, czy z tych wartości da się go założyć, a założeniem zajmuje się czynność wołana
    przez widok. Dzięki temu podgląd (``dry_run=True``) i zapis idą tą samą drogą, a formularz
    ma jedno zadanie.
    """

    name = forms.CharField(
        label="Pełna nazwa konkursu",
        max_length=200,
        help_text="Tak, jak ma być w nagłówku serwisu i w dyplomach, np. „Olimpiada Fizyczna”.",
    )
    short_name = forms.CharField(
        label="Nazwa skrócona",
        max_length=60,
        required=False,
        help_text="Do tematów listów i wąskich miejsc. Puste = ta sama, co pełna.",
    )
    slug = forms.CharField(
        label="Identyfikator (adres konkursu)",
        max_length=30,
        help_text=(
            "Od 3 do 30 znaków: małe litery, cyfry i myślnik. Z niego powstaje adres serwisu "
            "i przedrostki kodów uczestników — po założeniu konkursu nie da się go zmienić "
            "z panelu."
        ),
    )
    template = forms.ChoiceField(
        label="Szablon startowy",
        choices=[(name, TEMPLATES[name]["label"]) for name in TEMPLATE_CHOICES],
        help_text="Etapy, formaty plików i zestaw zgód na start. Wszystko da się potem zmienić.",
    )
    organizer = forms.CharField(
        label="Organizator (podmiot prawny)",
        max_length=200,
        required=False,
        help_text="Puste = powtórzy nazwę konkursu, do poprawienia w „Ustawieniach konkursu”.",
    )
    contact_email = forms.EmailField(
        label="Adres kontaktowy organizatora",
        required=False,
        help_text="Widoczny w stopce i w klauzuli informacyjnej.",
    )
    accent_colour = forms.CharField(
        label="Kolor akcentu",
        max_length=7,
        required=False,
        validators=[validate_hex_colour],
        help_text="Zapis szesnastkowy, np. #1f6feb. Puste = motyw domyślny serwisu.",
    )
    edition_label = forms.CharField(
        label="Oznaczenie pierwszej edycji",
        max_length=60,
        required=False,
        help_text="Np. „I edycja 2026/2027”. Puste = bieżący rocznik szkolny liczony od września.",
    )

    def clean_slug(self) -> str:
        """Cztery sita naraz — kształt, infrastruktura, adresy aplikacji, zajętość w bazie.

        Kolejność jest kolejnością od najtańszego i zarazem od najbardziej zrozumiałego: komunikat
        „zły kształt” jest odpowiedzią na to, co człowiek właśnie wpisał, a „nazwa zajęta” — na to,
        czego nie mógł wiedzieć.
        """
        slug = (self.cleaned_data["slug"] or "").strip().lower()
        if not SLUG_RE.match(slug):
            raise forms.ValidationError(
                "Identyfikator ma od 3 do 30 znaków i składa się z małych liter, cyfr i myślnika.",
                code="invalid_shape",
            )
        if slug.startswith("-") or slug.endswith("-"):
            raise forms.ValidationError(
                "Identyfikator nie może zaczynać się ani kończyć myślnikiem — nazwa hosta z takim "
                "myślnikiem nie jest poprawna.",
                code="edge_hyphen",
            )
        if slug in reserved_labels():
            raise forms.ValidationError(
                "Ta nazwa należy do adresów platformy (poczta, monitoring, panel) i konkurs "
                "przejąłby je pod tą domeną. Wybierz inną.",
                code="reserved",
            )
        host = platform_host(slug)
        if Competition.objects.filter(slug=slug).exists():
            raise forms.ValidationError("Konkurs o tym identyfikatorze już istnieje.", code="taken_slug")
        if Competition.objects.filter(primary_domain__iexact=host).exists():
            raise forms.ValidationError(f"Adres {host} należy już do innego konkursu.", code="taken_domain")
        if Site.objects.filter(hostname__iexact=host).exists():
            # Witryna bez konkursu też zajmuje host: drugie drzewo stron pod tym samym adresem
            # znaczyłoby, że o treści decyduje kolejność wierszy w bazie.
            raise forms.ValidationError(
                f"Adres {host} jest już zajęty przez inną witrynę.", code="taken_site"
            )
        return slug

    @property
    def host(self) -> str:
        """Adres konkursu wynikający z identyfikatora — dla podglądu i dla wołania czynności."""
        return platform_host(self.cleaned_data["slug"])
