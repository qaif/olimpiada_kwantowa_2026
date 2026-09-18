"""Adresy ekranów „Słownik placówek” i „Profil rejestracji” – wzorce gotowe do wpięcia w ``urls.py``.

Osobny moduł, bo ``apps/web/urls.py`` ma w etapie 2 jednego właściciela na wydanie (zadanie
„montaż”, ``docs/UNIWERSALNY-ETAP-2.md`` § 4.1), a zadania wydania H biegną równolegle. Montaż
polega na rozwinięciu tej listy wewnątrz ``urlpatterns`` panelu:

.. code-block:: python

    from .urls_institutions import urlpatterns as institution_urlpatterns
    ...
    urlpatterns = [
        ...,
        *institution_urlpatterns,
    ]

Wzorce **nie są** bramkowane w urlconfie: o tym, czy ekran istnieje w tym konkursie, rozstrzyga
flaga czytana w widoku (404 przy wyłączonej, § 2.1). Bramka w adresach znaczyłaby mapę adresów
zależną od konkursu, czyli ``reverse()`` dający raz adres, a raz ``NoReverseMatch`` – i pozycję
menu, której nie da się sprawdzić testem widoku.

**Czynność ma własny adres, a nie pole „akcja”.** Dopisanie placówki, wyłączenie i włączenie są
trzema różnymi POST-ami pod trzy różne adresy – ta sama reguła, co na ekranie regionów
i integracji: rozróżnianie po nazwie wciśniętego przycisku zależy od tego, czy przeglądarka ją
przyśle, a przy wysyłce klawiaturą nie zawsze przysyła.

Adresy stałe (``import/``, ``new/``, ``export.csv``, ``template.csv``) stoją **przed** wzorcem
``<int:pk>/``: wzorzec liczbowy nie złapałby napisu, ale kolejność wzorców jest umową, a
czytelniejsza jest ta, w której stała ścieżka poprzedza wzorzec z parametrem.

Pliki mają w adresie **rozszerzenie**, a nie parametr formatu (``export/<str:fmt>/``): wychodzi
stąd jeden format, a adres kończący się na ``.csv`` jest tym, co przeglądarka i arkusz
koordynatora rozpoznają bez pytania.

Ekran „Profil rejestracji” jedzie tą samą listą, choć ma **własną flagę** (``institution_types``,
nie ``custom_school_directory``): wchodzi w tym samym wydaniu i z tego samego zadania, a druga
lista na jeden ekran byłaby tylko drugim miejscem do zapomnienia przy montażu. Plan (§ 2.2)
przypisuje edycję profilu jako drugą sekcję istniejącego ekranu ``/coordinator/registration/``
(``apps/web/views/coordinator.py``); ten plik nie jest własnością zadania T23, więc ekran stoi
pod własnym adresem – patrz raport zadania.
"""

from __future__ import annotations

from django.urls import path

from .views import coordinator_institutions

urlpatterns = [
    path(
        "coordinator/institutions/",
        coordinator_institutions.InstitutionListView.as_view(),
        name="coordinator-institutions",
    ),
    path(
        "coordinator/institutions/new/",
        coordinator_institutions.InstitutionCreateView.as_view(),
        name="coordinator-institution-create",
    ),
    path(
        "coordinator/institutions/import/",
        coordinator_institutions.InstitutionImportView.as_view(),
        name="coordinator-institutions-import",
    ),
    path(
        "coordinator/institutions/export.csv",
        coordinator_institutions.InstitutionExportView.as_view(),
        name="coordinator-institutions-export",
    ),
    path(
        "coordinator/institutions/template.csv",
        coordinator_institutions.InstitutionTemplateView.as_view(),
        name="coordinator-institutions-template",
    ),
    path(
        "coordinator/institutions/<int:pk>/",
        coordinator_institutions.InstitutionEditView.as_view(),
        name="coordinator-institution",
    ),
    path(
        "coordinator/institutions/<int:pk>/activate/",
        coordinator_institutions.InstitutionActivateView.as_view(),
        name="coordinator-institution-activate",
    ),
    path(
        "coordinator/institutions/<int:pk>/deactivate/",
        coordinator_institutions.InstitutionDeactivateView.as_view(),
        name="coordinator-institution-deactivate",
    ),
    path(
        "coordinator/registration-profile/",
        coordinator_institutions.RegistrationProfileView.as_view(),
        name="coordinator-registration-profile",
    ),
]
