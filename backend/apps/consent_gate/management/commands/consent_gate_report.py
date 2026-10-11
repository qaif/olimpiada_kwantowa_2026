"""``manage.py consent_gate_report`` – ilu uczestników bramka zgód odeśle do ekranu „Uzupełnij zgody”.

Do sprawdzenia **przed** wdrożeniem CONS-01 i po każdej zmianie wersji dokumentu (``docs/OPERACJE.md``
§ 51): liczby per konkurs i per rodzaj zgody, bez żadnych danych osobowych – wynik można wkleić do
zgłoszenia albo wiadomości do organizatora. Listę osób koordynator pobiera z pulpitu (CSV).
"""

from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand

from apps.consent_gate import report, state
from apps.tenancy.context import competition_context
from apps.tenancy.models import Competition


class Command(BaseCommand):
    help = "Liczba uczestników z brakującymi zgodami (per konkurs i rodzaj zgody), bez danych osobowych."

    def handle(self, *args, **options):
        for competition in Competition.objects.order_by("pk"):
            # Kontekst konkursu: zestaw zgód, nazwa organizatora i zakresowanie czytają go tak samo
            # jak w żądaniu pod domeną tego konkursu.
            with competition_context(competition):
                rows = report.gaps(competition)
                total = len(rows)
                counter = Counter(consent.kind for *_identity, missing in rows for consent in missing)
                versions = {consent.kind: consent.version for consent in state.consents_for(competition)}
            self.stdout.write(f"{competition.slug}: {total} uczestników z brakującymi zgodami")
            for kind, number in sorted(counter.items()):
                self.stdout.write(f"  {kind} (wymagana wersja: {versions.get(kind, '?')}): {number}")
