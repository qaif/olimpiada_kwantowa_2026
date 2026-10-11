"""``manage.py seed_accessibility_statement <slug> [--language pl|en] [--publish]`` – deklaracja dostępności.

Strona ``/dokumenty/deklaracja-dostepnosci/`` w drzewie stron konkursu (A11Y-01, ``docs/tasks/A11Y-01.md``
§ 5). Odnośnik do niej stoi w stopce obu motywów (``templates/theme/footer.html`` i paczka
``iqo-quantum`` ≥ 1.1.2) – ten sam wzorzec stałej ścieżki, co „Polityka prywatności (RODO)”.

**Treść jest projektem do zatwierdzenia przez organizatora.** Struktura idzie za wzorem deklaracji
z ustawy z 4 kwietnia 2019 r. o dostępności cyfrowej (wstęp, daty, stan dostępności, sposób
przygotowania, informacje zwrotne, procedura, dostępność architektoniczna), a dane kontaktowe
i nazwa organizatora pochodzą z ``SiteSettings`` witryny konkursu. Dlatego domyślnie komenda
zapisuje **wersję roboczą** (rewizja bez publikacji): organizator czyta ją w podglądzie ``/cms/``,
poprawia i publikuje sam. ``--publish`` publikuje od razu (przebieg testów dostępności, staging).

Idempotencja: strona rozpoznawana po slugu w sekcji dokumentów. Ponowny przebieg na stronie, która
ma już opublikowaną wersję, **odmawia** (``--force`` nadpisuje) – deklaracja po zatwierdzeniu jest
dokumentem organizatora i komenda nie może cicho zastąpić jej projektem.

Język: ``--language`` albo język domyślny konkursu (``pl`` → polska, inny → angielska). Wersji
w pozostałych językach interfejsu nie ma: deklaracja jest dokumentem prawnym i ma brzmienie
w języku organizatora (oraz angielskim dla olimpiady międzynarodowej).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from wagtail.models import Site
from wagtail.rich_text import RichText

from apps.accessibility.availability import PAGE_SLUG
from apps.cms.models import DocumentPage, SiteSettings
from apps.cms.site_tree import ensure_document_index, home_page, take_document_page

TITLES = {"pl": "Deklaracja dostępności", "en": "Accessibility statement"}
STATUS = {
    "pl": "Projekt – do zatwierdzenia przez organizatora",
    "en": "Draft – to be approved by the organiser",
}


def _heading(text: str, anchor: str) -> tuple[str, dict]:
    return ("heading", {"text": text, "level": "2", "anchor": anchor, "in_toc": True})


def _p(html: str) -> tuple[str, RichText]:
    return ("paragraph", RichText(html))


def _contact(site: SiteSettings, lang: str) -> str:
    parts = []
    if site.contact_email:
        parts.append(f'<a href="mailto:{site.contact_email}">{site.contact_email}</a>')
    if site.contact_phone:
        label = "tel." if lang == "pl" else "phone"
        parts.append(f"{label} {site.contact_phone}")
    return ", ".join(parts) or ("[adres e-mail]" if lang == "pl" else "[e-mail address]")


def blocks_pl(site: SiteSettings, address: str, today: str) -> list:
    org = site.organizer_name
    return [
        (
            "notice",
            {
                "tone": "warning",
                "text": RichText(
                    "<p>Projekt deklaracji przygotowany przez zespół techniczny na podstawie audytu "
                    "z października 2026 r. Przed publikacją organizator uzupełnia daty i osobę "
                    "kontaktową oraz zatwierdza treść.</p>"
                ),
            },
        ),
        _p(
            f"<p>{org} zobowiązuje się zapewnić dostępność swojej strony internetowej zgodnie "
            "z przepisami ustawy z dnia 4 kwietnia 2019 r. o dostępności cyfrowej stron internetowych "
            "i aplikacji mobilnych podmiotów publicznych. Organizator nie jest podmiotem publicznym "
            "w rozumieniu tej ustawy – stosuje ją dobrowolnie, a za miarę dostępności przyjmuje "
            "standard WCAG 2.1 na poziomie AA.</p>"
            f"<p>Deklaracja dostępności dotyczy serwisu <strong>{site.site_name}</strong> "
            f"({address}).</p>"
        ),
        _heading("Daty publikacji i aktualizacji", "daty"),
        _p(
            "<ul><li>Data publikacji strony internetowej: [do uzupełnienia przez organizatora].</li>"
            f"<li>Data ostatniej istotnej aktualizacji: {today}.</li></ul>"
        ),
        _heading("Stan dostępności cyfrowej", "stan"),
        _p(
            "<p>Strona internetowa jest <strong>częściowo zgodna</strong> z ustawą o dostępności "
            "cyfrowej z powodu niezgodności lub wyłączeń wymienionych poniżej.</p>"
        ),
        _heading("Treści niedostępne", "niezgodnosci"),
        _p(
            "<ul>"
            "<li>Część dokumentów do pobrania (PDF) opublikowanych przed 2026 r. to skany lub pliki "
            "bez struktury nagłówków – na żądanie przekażemy ich treść w formie dostępnej.</li>"
            "<li>Zabezpieczenie formularza rejestracji (CAPTCHA) jest obrazkiem bez wersji dźwiękowej. "
            "Osoba, która nie może go odczytać, zakłada konto przez kontakt z organizatorem "
            "(adres podany przy formularzu).</li>"
            "<li>Treści zadań konkursowych z rysunkami i wzorami matematycznymi mogą nie mieć pełnych "
            "opisów alternatywnych; opis przekazujemy na żądanie uczestnika przed etapem.</li>"
            "<li>Nagrania wideo z wydarzeń (materiały warsztatowe) mogą nie mieć napisów.</li>"
            "<li>Rozwiązania przesyłane przez uczestników (skany, zdjęcia) są dokumentami osób trzecich "
            "i nie podlegają tej deklaracji.</li>"
            "</ul>"
        ),
        _heading("Przygotowanie deklaracji dostępności", "przygotowanie"),
        _p(
            f"<p>Deklarację sporządzono dnia {today} na podstawie samooceny przeprowadzonej przez "
            "zespół techniczny z użyciem narzędzi automatycznych (axe-core, reguły WCAG 2.1 A i AA) "
            "oraz kontroli ręcznej obsługi klawiaturą, trybu wysokiego kontrastu, powiększenia "
            "do 200 % i układu od prawej do lewej. Kontrola automatyczna jest powtarzana przy każdej "
            "zmianie oprogramowania.</p>"
        ),
        _heading("Ułatwienia w serwisie", "ulatwienia"),
        _p(
            "<ul>"
            "<li>Odnośnik „Przejdź do treści” na początku każdej strony (pierwsze naciśnięcie "
            "klawisza Tab).</li>"
            "<li>Tryb wysokiego kontrastu – przełącznik w pasku konta (ikona kółka).</li>"
            "<li>Wybór języka interfejsu i obsługa tekstu od prawej do lewej (język arabski).</li>"
            "<li>Serwis działa przy powiększeniu tekstu i strony do 200 % oraz na ekranach "
            "o szerokości od 320 pikseli.</li>"
            "<li>Menu i formularze działają także bez JavaScriptu i bez myszy.</li>"
            "</ul>"
        ),
        _heading("Informacje zwrotne i dane kontaktowe", "kontakt"),
        _p(
            "<p>Wszystkie problemy z dostępnością cyfrową tej strony internetowej można zgłaszać "
            f"do: [osoba kontaktowa – do uzupełnienia], {_contact(site, 'pl')}. Tą samą drogą można "
            "składać wnioski o udostępnienie informacji niedostępnej oraz żądania zapewnienia "
            "dostępności cyfrowej.</p>"
        ),
        _heading("Procedura wnioskowo-skargowa", "procedura"),
        _p(
            "<p>Każdy ma prawo wystąpić z żądaniem zapewnienia dostępności cyfrowej strony "
            "internetowej lub jakiegoś jej elementu, a także zażądać udostępnienia informacji "
            "za pomocą alternatywnego sposobu dostępu, na przykład przez odczytanie niedostępnego "
            "cyfrowo dokumentu, opisanie zawartości filmu bez audiodeskrypcji itp. Żądanie powinno "
            "zawierać dane osoby zgłaszającej, wskazanie strony internetowej lub jej elementu oraz "
            "sposób kontaktu. Jeżeli osoba żądająca zgłasza potrzebę otrzymania informacji w formie "
            "alternatywnej, powinna także określić tę formę.</p>"
            "<p>Organizator zrealizuje żądanie niezwłocznie, nie później niż w ciągu 7 dni od dnia "
            "jego wystąpienia. Jeżeli dotrzymanie tego terminu nie będzie możliwe, poinformujemy "
            "o terminie realizacji, nie dłuższym niż 2 miesiące. Jeżeli zapewnienie dostępności "
            "cyfrowej nie będzie możliwe, zaproponujemy alternatywny sposób dostępu do informacji.</p>"
            "<p>W przypadku odmowy można złożyć skargę do organizatora tą samą drogą. Po wyczerpaniu "
            "tej procedury można także złożyć wniosek do Rzecznika Praw Obywatelskich "
            '(<a href="https://bip.brpo.gov.pl/">bip.brpo.gov.pl</a>).</p>'
        ),
        _heading("Dostępność architektoniczna", "architektura"),
        _p(
            f"<p>{site.organizer_address or '[adres siedziby – do uzupełnienia]'}. Informacje "
            "o dostępności miejsc zawodów stacjonarnych (wejścia, windy, toalety, miejsca dla osób "
            "z niepełnosprawnościami, pętla indukcyjna) organizator podaje w zaproszeniu na dany "
            "etap; potrzeby uczestnika zgłasza się koordynatorowi przed etapem.</p>"
        ),
    ]


def blocks_en(site: SiteSettings, address: str, today: str) -> list:
    org = site.organizer_name
    return [
        (
            "notice",
            {
                "tone": "warning",
                "text": RichText(
                    "<p>Draft statement prepared by the technical team after the October 2026 audit. "
                    "Before publication the organiser fills in the dates and the contact person and "
                    "approves the text.</p>"
                ),
            },
        ),
        _p(
            f"<p>{org} is committed to making this website accessible. The organiser applies the "
            "Polish Act of 4 April 2019 on the digital accessibility of websites and mobile "
            "applications voluntarily and measures accessibility against WCAG 2.1 level AA.</p>"
            f"<p>This statement applies to <strong>{site.site_name}</strong> ({address}).</p>"
        ),
        _heading("Publication and update dates", "dates"),
        _p(
            "<ul><li>Website published: [to be completed by the organiser].</li>"
            f"<li>Last major update: {today}.</li></ul>"
        ),
        _heading("Compliance status", "status"),
        _p(
            "<p>This website is <strong>partially compliant</strong> with WCAG 2.1 level AA due to "
            "the non-compliances and exemptions listed below.</p>"
        ),
        _heading("Non-accessible content", "non-accessible"),
        _p(
            "<ul>"
            "<li>Some downloadable PDF documents are scans or lack a heading structure – we will "
            "provide their content in an accessible form on request.</li>"
            "<li>Problem statements with drawings and mathematical formulas may lack full text "
            "alternatives; a description is provided on request before the round.</li>"
            "<li>Event videos may lack captions.</li>"
            "<li>Files uploaded by participants and delegations (scans, photos, passports) are "
            "third-party content not covered by this statement.</li>"
            "</ul>"
        ),
        _heading("Preparation of this statement", "preparation"),
        _p(
            f"<p>This statement was prepared on {today} based on a self-assessment by the technical "
            "team using automated tools (axe-core, WCAG 2.1 A and AA rules) and manual checks of "
            "keyboard operation, high-contrast mode, 200% zoom and right-to-left layout. Automated "
            "checks run on every software change.</p>"
        ),
        _heading("Accessibility features", "features"),
        _p(
            "<ul>"
            "<li>“Skip to content” link at the top of every page (first Tab press).</li>"
            "<li>High-contrast mode – toggle in the account bar (half-filled circle icon).</li>"
            "<li>Eleven interface languages, including right-to-left Arabic.</li>"
            "<li>The site works at 200% zoom and on screens from 320 pixels wide.</li>"
            "<li>Menus and forms work without JavaScript and without a mouse.</li>"
            "</ul>"
        ),
        _heading("Feedback and contact information", "contact"),
        _p(
            "<p>Please report accessibility problems and request information in an alternative "
            f"format: [contact person – to be completed], {_contact(site, 'en')}. We respond within "
            "7 days; if a request cannot be met within that time we will tell you when it will be "
            "(no later than within 2 months) or offer an alternative way to access the "
            "information.</p>"
        ),
        _heading("Enforcement procedure", "enforcement"),
        _p(
            "<p>If your request is refused you can file a complaint with the organiser by the same "
            "means. After that you may contact the Polish Commissioner for Human Rights "
            '(<a href="https://bip.brpo.gov.pl/">bip.brpo.gov.pl</a>).</p>'
        ),
        _heading("Physical accessibility", "physical"),
        _p(
            "<p>Accessibility information for the venue of the final round (entrances, lifts, "
            "toilets, quiet room) is sent with the invitation to the final; team leaders report "
            "participants’ needs in the delegation logistics form.</p>"
        ),
    ]


class Command(BaseCommand):
    help = "Zakłada projekt deklaracji dostępności (/dokumenty/deklaracja-dostepnosci/) w witrynie konkursu."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Identyfikator konkursu, np. kwantowa albo iqo.")
        parser.add_argument(
            "--language", choices=sorted(TITLES), help="Język deklaracji (domyślnie z konkursu)."
        )
        parser.add_argument("--publish", action="store_true", help="Opublikuj od razu (bez zatwierdzenia).")
        parser.add_argument(
            "--force", action="store_true", help="Nadpisz stronę, która ma już opublikowaną wersję."
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.tenancy.models import Competition

        competition = Competition.objects.filter(slug=options["slug"]).first()
        if competition is None:
            raise CommandError(f"Nie ma konkursu o slugu {options['slug']!r}.")
        site = Site.objects.filter(pk=competition.site_id).first() if competition.site_id else None
        home = home_page(site)
        if home is None:
            raise CommandError("Witryna konkursu nie ma strony głównej – najpierw drzewo stron.")
        lang = options["language"] or ("pl" if competition.default_language == "pl" else "en")
        settings_row = SiteSettings.for_site(home.get_site())
        domain = competition.primary_domain or home.get_site().hostname
        today = timezone.localdate().strftime("%d.%m.%Y" if lang == "pl" else "%d %B %Y")
        builder = blocks_pl if lang == "pl" else blocks_en

        index, _ = ensure_document_index(home)
        page, _ = take_document_page(DocumentPage, index, home, PAGE_SLUG)
        created = page is None
        if not created and page.live and page.first_published_at and not options["force"]:
            raise CommandError(
                f"{page.url} jest już opublikowana – to dokument organizatora. Nadpisanie: --force."
            )
        if created:
            page = DocumentPage(title=TITLES[lang], slug=PAGE_SLUG, live=False)
            index.add_child(instance=page)
            page = DocumentPage.objects.get(pk=page.pk)

        page.title = TITLES[lang]
        page.show_in_menus = False
        page.body = builder(settings_row, domain, today)
        page.status_label = STATUS[lang]
        page.document_date = timezone.localdate()
        revision = page.save_revision()
        if options["publish"]:
            revision.publish()
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_accessibility_statement: {'utworzono' if created else 'zaktualizowano'} "
                f"{PAGE_SLUG} ({lang}) w witrynie {home.get_site().hostname} – "
                f"{'opublikowano' if options['publish'] else 'wersja robocza do zatwierdzenia w /cms/'}."
            )
        )
