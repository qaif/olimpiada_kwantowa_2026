"""``manage.py retention_report`` – co zrobiłaby retencja danych, gdyby ruszyła teraz.

Dry-run istnieje, bo anonimizacja jest **nieodwracalna**: po niej nie ma z czego odtworzyć imienia,
nazwiska ani adresu e-mail uczestnika. Przed pierwszym uruchomieniem automatu (i po każdej zmianie
``Edition.data_retention_months``) organizator ma prawo zobaczyć listę, zanim zobaczy skutek.

Komenda **niczego nie zmienia** i nie ma flagi, która by to zmieniała. Anonimizacja odbywa się
dwiema drogami: zadaniem okresowym (raz na dobę) i przyciskiem „Wykonaj teraz” na
``/coordinator/retention/``. Trzecia droga – przełącznik w komendzie – byłaby jedynym miejscem,
w którym dane znikają bez wpisu o tym, kto o tym zdecydował.

Raport nie wypisuje ani jednego adresu e-mail: konta identyfikuje ``public_code``, czyli ten sam
pseudonim, pod którym uczestnik stoi w ogłoszonych tabelach. Wydruk komendy trafia do logu
wdrożeniowego i na cudze ekrany – lista adresów byłaby dokładnie tą daną, którą retencja ma usunąć.
"""

from django.core.management.base import BaseCommand

from apps.accounts.retention import plan


class Command(BaseCommand):
    help = "Pokazuje, które konta uczestników zostałyby zanonimizowane przez retencję danych."

    def handle(self, *args, **options):
        plans = plan()
        if not plans:
            self.stdout.write("Żadnej edycji nie upłynął jeszcze okres retencji.")
            return

        total_due = 0
        for item in plans:
            deadline = item.deadline.isoformat() if item.deadline else "—"
            self.stdout.write(f"\n{item.edition.year_label} (termin retencji: {deadline})")
            for candidate in item.due:
                self.stdout.write(f"  anonimizacja: {candidate.participant.public_code}")
            for candidate in item.blocked:
                self.stdout.write(f"  zostaje ({candidate.reason}): {candidate.participant.public_code}")
            total_due += len(item.due)
            self.stdout.write(f"  razem: {len(item.due)} do anonimizacji, {len(item.blocked)} wstrzymanych")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDry-run: {total_due} kont do anonimizacji w {len(plans)} edycjach. "
                "Nic nie zostało zmienione."
            )
        )
