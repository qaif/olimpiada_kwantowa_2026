"""``manage.py competition_languages <slug> [kody…] [--default KOD]`` — języki interfejsu konkursu.

Bez kodów komenda tylko **pokazuje** stan: język domyślny i zbiór języków interfejsu
(``Competition.interface_languages``, I18N-01 § 1). Z kodami – zapisuje zbiór (i ewentualnie
język domyślny) po tej samej walidacji, co ekran „Ustawienia konkursu” i ``/admin/``
(``Competition.full_clean``), i zostawia wpis w dzienniku audytu.

Po co komenda, skoro jest ekran: ekran koordynatora stoi za przełącznikiem
``competition_settings_page``, a konkurs zakładany z szablonu bywa konfigurowany przez operatora,
zanim ktokolwiek dostanie rolę koordynatora. Jedna linijka w procedurze wdrożenia
(``docs/OPERACJE.md``) jest też łatwiejsza do powtórzenia niż opis klikania po panelu.

Przykład (konkurs międzynarodowy): ``competition_languages iqo en zh-hans hi es ar fr bn pt ru id pl
--default en``.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import audit
from apps.tenancy.context import competition_context
from apps.tenancy.models import Competition


class Command(BaseCommand):
    help = "Pokazuje albo ustawia języki interfejsu konkursu (Competition.interface_languages)."

    def add_arguments(self, parser):
        parser.add_argument("slug", help="Identyfikator konkursu, np. iqo.")
        parser.add_argument(
            "languages",
            nargs="*",
            help=f"Kody języków z settings.LANGUAGES: {', '.join(c for c, _ in settings.LANGUAGES)}.",
        )
        parser.add_argument("--default", dest="default", help="Nowy język domyślny konkursu.")

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(slug=options["slug"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"Nie ma konkursu o identyfikatorze „{options['slug']}”.") from exc

        languages = options["languages"]
        default = options["default"]
        if languages or default:
            before = {
                "default_language": competition.default_language,
                "interface_languages": list(competition.ui_languages),
            }
            if default:
                competition.default_language = default
            if languages:
                # Kolejność z ``settings.LANGUAGES``, powtórzenia odpadają – ta sama lista
                # wpisana w innej kolejności ma dać ten sam wiersz (i ten sam przełącznik).
                order = [code for code, _label in settings.LANGUAGES]
                unknown = sorted(set(languages) - set(order))
                if unknown:
                    raise CommandError(f"Nieznane kody języków: {', '.join(unknown)}.")
                competition.interface_languages = [code for code in order if code in set(languages)]
            try:
                competition.full_clean()
            except ValidationError as exc:
                raise CommandError(
                    "; ".join(f"{k}: {' '.join(v)}" for k, v in exc.message_dict.items())
                ) from exc
            with transaction.atomic(), competition_context(competition):
                competition.save(update_fields=["default_language", "interface_languages"])
                audit(
                    None,
                    "competition.languages_changed",
                    competition,
                    {
                        "before": before,
                        "after": {
                            "default_language": competition.default_language,
                            "interface_languages": list(competition.ui_languages),
                        },
                    },
                )
            self.stdout.write(self.style.SUCCESS("Zapisano."))

        self.stdout.write(f"Konkurs: {competition.slug} ({competition.name})")
        self.stdout.write(f"Język domyślny: {competition.default_language}")
        self.stdout.write(f"Języki interfejsu: {' '.join(competition.ui_languages)}")
