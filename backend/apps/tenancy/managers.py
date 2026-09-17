"""Zawężanie zapytań do jednego konkursu – w managerze, nie w widoku.

Reguła, na której stoi izolacja w wariancie „jedna baza, wiersz właściciela”: **filtr należy do
querysetu**. Ukrycie wiersza w pętli szablonu zostawia adres szczegółu otwarty (IDOR), a filtr
dopisany w widoku jest filtrem, który następny widok pominie. Jedno miejsce znaczy, że zapomnienie
jest widoczne jako brak deklaracji ``competition_path``, a nie jako wyciek.

Aplikacje domenowe podłączą się tu w zadaniach T3–T5; w etapie T1 klasy istnieją, są przetestowane
i nikt ich jeszcze nie używa. To jest świadome: warstwa izolacji ma być gotowa **przed** pierwszym
modelem, który jej potrzebuje, a nie powstawać przy okazji.
"""

from __future__ import annotations

from django.db import models


class CompetitionScopedQuerySet(models.QuerySet):
    """Queryset modelu, który należy do konkursu – wprost albo przez łańcuch kluczy obcych.

    Każdy model deklaruje **swoją** drogę do konkursu atrybutem klasy:

    - ``SubmissionQuerySet.competition_path = "competition"`` (kolumna denormalizacyjna),
    - ``ReviewQuerySet.competition_path = "submission__competition"``,
    - ``StageEntryQuerySet.competition_path = "stage__edition__competition"``.

    Ścieżka jest atrybutem, a nie argumentem wywołania, bo jest **własnością modelu**: gdyby
    podawał ją wołający, dwa widoki tego samego modelu mogłyby podać dwie różne, a jedna z nich
    byłaby błędna.
    """

    #: Domyślnie własna kolumna. Modele bez FK do konkursu nadpisują ten atrybut łańcuchem relacji.
    competition_path: str = "competition"

    def for_competition(self, competition):
        """Wiersze **jednego** konkursu.

        ``None`` nie widzi niczego – domyślnie zamknięte. Odwrotna decyzja („brak konkursu =
        wszystko”) zamieniłaby każdy błąd rozstrzygania hosta w wyciek między konkursami, i to
        wyciek cichy: odpowiedź wyglądałaby poprawnie.
        """
        if competition is None:
            return self.none()
        return self.filter(**{self.competition_path: competition})


#: Manager z zakresowaniem. Osobna nazwa, bo ``Model.objects`` bywa nadpisywany managerem z własnymi
#: metodami – ``from_queryset`` przenosi tam ``for_competition`` bez dziedziczenia po dwóch klasach.
CompetitionScopedManager = models.Manager.from_queryset(CompetitionScopedQuerySet)
