# Licencja: dlaczego AGPL-3.0

Repozytorium nie deklarowało dotąd żadnej licencji. Brak licencji **nie** znaczy „wolne oprogramowanie”
— znaczy „wszystkie prawa zastrzeżone”: bez wyraźnej zgody nikt inny nie może legalnie uruchomić tego
kodu u siebie, zmienić go ani przekazać dalej. Dla systemu, który ma **przejąć inny organizator**, jest
to stan nie do utrzymania, więc do repozytorium trafia plik [`LICENSE`](../LICENSE) z pełnym tekstem
**GNU Affero General Public License v3.0** (AGPL-3.0, dosłowna kopia z <https://www.gnu.org/licenses/agpl-3.0.txt>).

## Dlaczego akurat AGPL-3.0

To jest **platforma internetowa**, a nie biblioteka. Kto ją przejmie, zwykle jej nie „rozpowszechni”:
postawi ją na własnym serwerze i udostępni uczestnikom przez przeglądarkę. Zwykła GPL-3.0 w takim
układzie nie wymaga niczego — poprawki zostają na cudzym serwerze i nie wracają. AGPL zamyka tę lukę
sekcją 13: kto pozwala **korzystać z programu przez sieć**, musi udostępnić użytkownikom źródła swojej
wersji. Skutek praktyczny jest taki, jakiego chce organizator publicznej olimpiady:

- kolejny organizator (inna olimpiada, inne województwo, inny kraj) **może** wziąć system, zmienić go
  i prowadzić na nim własne zawody — za darmo i bez pytania o zgodę,
- poprawki, które przy tym powstaną (a powstaną: regulamin, skala, progi i obieg oceniania są u każdego
  trochę inne), **wracają do wspólnej puli**, zamiast rozejść się po prywatnych forkach,
- nikt nie zamknie tego systemu w zamkniętym produkcie SaaS sprzedawanym szkołom.

Zgodność jest w porządku: cały stos (Django, Wagtail, DRF, Celery, psycopg, reportlab, openpyxl, pypdf)
jest na licencjach permisywnych (BSD/MIT/Apache) albo LGPL/BSD, a te da się łączyć z AGPL-3.0. Kroje
DejaVu w `backend/static/fonts/` mają własną licencję (`DejaVu-LICENSE.txt`) i zostają na niej —
plik licencji leży obok fontów i ma tam zostać.

## Alternatywy, które odrzuciliśmy

| Licencja | Co daje | Dlaczego nie tutaj |
|---|---|---|
| **MIT** | maksymalna swoboda, najkrótszy tekst, zero tarcia przy adopcji | pozwala zamknąć pochodną wersję; praca finansowana ze środków fundacji mogłaby zniknąć w cudzym zamkniętym produkcie |
| **Apache-2.0** | jak MIT plus wyraźna licencja patentowa i ochrona znaku | ta sama luka „usługa sieciowa” co MIT; klauzula patentowa nie jest tu problemem, który mamy |
| **GPL-3.0** | copyleft przy rozpowszechnianiu | nie obejmuje udostępniania przez sieć, czyli dokładnie tego trybu, w którym ten system żyje |
| **brak licencji** | – | „wszystkie prawa zastrzeżone”: adopcja przez innego organizatora jest nielegalna |

Gdyby organizator **świadomie** wolał najszerszą adopcję kosztem zwrotu poprawek, zamiana jest prosta:
`LICENSE` zastępuje się tekstem MIT albo Apache-2.0, poprawia się pole `license` w
`backend/pyproject.toml` i sekcję „Dokumentacja” w `README.md`. Zmiana licencji na **luźniejszą** jest
możliwa dopóty, dopóki właścicielem majątkowych praw autorskich jest jeden podmiot (niżej) — po
przyjęciu wkładu od osób z zewnątrz wymagałaby ich zgody.

## Co organizator musi rozstrzygnąć

1. **Właściciel praw majątkowych.** Zakładamy **Fundację Quantum AI** (`SiteSettings.organizer_name`,
   kontakt `contact@qaif.org`) jako podmiot, na który przeniesiono majątkowe prawa autorskie do kodu
   zamówionego i sfinansowanego przez fundację. Jeżeli umowy z wykonawcami mówią co innego (licencja
   niewyłączna zamiast przeniesienia praw, współautorstwo), publikacja na AGPL wymaga aneksu albo zgody
   współuprawnionych. **To jest jedyna rzecz, której nie da się rozstrzygnąć z poziomu repozytorium.**
2. **Nota o prawach autorskich w nagłówkach plików.** Sama AGPL jej nie wymaga, ale jej brak utrudnia
   dowodzenie autorstwa. Jeśli organizator chce ją mieć, wzór z końca `LICENSE` („How to Apply These
   Terms…”) wstawia się na początek plików źródłowych:
   `Copyright (C) 2026 Fundacja Quantum AI` + trzy akapity odesłania do licencji.
3. **Publiczne repozytorium.** AGPL sekcja 13 wymaga, żeby użytkownicy serwisu mogli **pobrać źródła
   uruchomionej wersji**. Najtańsze spełnienie tego obowiązku: publiczne repozytorium (GitHub/GitLab)
   i odnośnik do niego w stopce serwisu albo na `/status/`, obok numeru wersji z `APP_VERSION`.
   Dopóki repozytorium jest prywatne, obowiązek istnieje, ale nie ma jak go wykonać.
4. **Znak towarowy i treść.** Licencja obejmuje **kod**. Nie obejmuje ani nazwy „Olimpiada Kwantowa”,
   ani logotypu organizatora (`backend/static/img/`, logotypy partnerów w `apps/cms/fixtures/partners/`),
   ani treści redakcyjnych i dokumentów organizatora (regulamin, polityka RODO, standardy ochrony
   małoletnich). Kto przejmie system, przejmuje silnik — nie markę. Warto to napisać wprost w `README.md`
   przy pierwszej publikacji repozytorium.

## Co się zmieniło w repozytorium

| Plik | Zmiana |
|---|---|
| `LICENSE` | pełny, dosłowny tekst AGPL-3.0 (661 wierszy) — tekstu licencji **nie wolno modyfikować** |
| `backend/pyproject.toml` | `license = "AGPL-3.0-or-later"` w metadanych projektu (identyfikator SPDX) |
| `README.md` | sekcja „Dokumentacja” z odnośnikiem do `LICENSE` i do tego uzasadnienia |
