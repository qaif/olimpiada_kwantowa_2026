"""Wspólna mechanika fabryk wielokonkursowych: „do którego konkursu należy ten wiersz”.

Fabryki domenowe (``apps/*/tests/factories.py``) dostają w zadaniu T7 jeden nowy argument:
``competition``. Wartość domyślna bierze się z kontekstu (``apps.tenancy.context``), więc żaden
z blisko trzech tysięcy istniejących testów nie musi dopisać ani jednej linijki – a test
wielokonkursowy wskazuje konkurs jawnie i dostaje obiekt tam, gdzie chciał.

Dlaczego to jest osobny moduł, a nie cztery kopie tej samej linijki w czterech fabrykach:
kolumna ``competition`` **dopiero powstaje**. Zadania T2 i T3 dokładają ją modelom kolejno
(``accounts`` w T2, ``competitions``/``submissions`` w T3), więc przez kilka wydań część modeli
to pole ma, a część jeszcze nie. Fabryka, która wpisywałaby ``competition`` na ślepo, wywracałaby
suitę na każdym stanie pośrednim i zmuszała do przepisywania testów tam i z powrotem. Dlatego
argument jest przyjmowany **zawsze**, a ustawiany **tylko wtedy, gdy model ma już to pole** –
o czym rozstrzyga introspekcja ``_meta``, a nie data wydania.

Efekt uboczny jest zamierzony: dzień, w którym T3 doda ``Edition.competition``, jest dniem,
w którym testy krzyżowe (``apps/tenancy/tests/test_isolation.py``) zaczynają naprawdę sprawdzać
izolację – bez zmiany choćby jednego znaku w tych testach.
"""

from __future__ import annotations

import factory
from django.core.exceptions import FieldDoesNotExist


def current_or_default_competition():
    """Konkurs, do którego należy obiekt budowany fabryką bez jawnego wskazania.

    Kolejność źródeł jest ta sama, co w produkcji: najpierw kontekst (w żądaniu ustawia go
    ``CompetitionMiddleware``, w teście – autouse ``_bind_competition`` z ``backend/conftest.py``),
    a dopiero potem pierwszy konkurs w bazie.

    Odczyt kontekstu jest darmowy (zmienna kontekstowa), więc domyślna wartość nie dokłada
    zapytania do żadnego testu – zapytanie pojawia się wyłącznie tam, gdzie kontekstu nie ma,
    czyli w testach, które same sobie bazę wyczyściły.

    Baza **bez ani jednego konkursu** dostaje Konkurs #1 odtworzony na miejscu i to jest zmiana
    z wydania D. Powód jest ten sam, dla którego ``conftest.root_page`` odtwarza korzeń drzewa
    stron: test transakcyjny czyści bazę po sobie, a ``flush`` przywraca wyłącznie typy treści
    i uprawnienia – nie wiersze wpisane przez ``RunPython`` (czyli ani korzenia stron, ani
    Konkursu #1 z ``tenancy.0002``). Do wydania D wiersz bez właściciela był tylko niewidoczny;
    od wydania D kolumna jest ``NOT NULL``, więc każdy test **uruchomiony po** teście
    transakcyjnym wywracałby się na ``IntegrityError`` – i to zależnie od kolejności pakietu.
    """
    from apps.tenancy.context import current_competition
    from apps.tenancy.models import Competition

    competition = current_competition()
    if competition is not None:
        return competition
    existing = Competition.objects.order_by("pk").first()
    if existing is not None:
        return existing
    # Import w środku funkcji: ``conftest`` korzenia jest modułem pytesta, a nie pakietem
    # aplikacji – na górze pliku nie byłoby go jeszcze w ``sys.modules``. Odtworzenie stoi tam,
    # bo tam stoi cała mechanika świata testowego (witryna, korzeń drzewa, host Konkursu #1),
    # a dwie kopie tej samej funkcji rozjechałyby się przy pierwszej zmianie.
    from conftest import restored_competition

    return restored_competition()


def model_has_competition(model) -> bool:
    """Czy ten model ma już kolumnę ``competition``.

    Introspekcja ``_meta``, a nie lista nazw modeli w kodzie testów: lista rozjeżdżałaby się
    z migracjami, a rozjazd objawiałby się jako test izolacji, który cicho niczego nie sprawdza.
    """
    try:
        model._meta.get_field("competition")
    except FieldDoesNotExist:
        return False
    return True


def without_unknown_competition(model, kwargs: dict) -> dict:
    """Zdejmuje ``competition`` z argumentów modelu, który tego pola jeszcze nie ma."""
    if "competition" in kwargs and not model_has_competition(model):
        kwargs = {key: value for key, value in kwargs.items() if key != "competition"}
    return kwargs


#: Skrót do propagacji konkursu na podfabryki: ``StageEntryFactory(competition=X)`` ma założyć
#: uczestnika i etap **tego** konkursu, a nie dwóch przypadkowych. Bez tego test krzyżowy budowałby
#: obiekt, którego połowa należy do konkursu A, a połowa do B – i nie sprawdzałby niczego.
SAME_COMPETITION = factory.SelfAttribute("..competition")


class CompetitionScopedFactory(factory.django.DjangoModelFactory):
    """Fabryka, która wie, do którego konkursu należy tworzony wiersz.

    Argument ``competition`` jest przyjmowany zawsze; do modelu trafia tylko wtedy, gdy model ma
    już takie pole (patrz docstring modułu). Modele bez własnego klucza (``Stage``, ``Problem``,
    ``StageEntry``, ``Review``…) korzystają z niego mimo to – przekazują go swoim podfabrykom
    przez ``SAME_COMPETITION``, bo droga do konkursu wiedzie u nich przez rodzica (§ 3.4).
    """

    class Meta:
        abstract = True

    competition = factory.LazyFunction(current_or_default_competition)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        return super()._create(model_class, *args, **without_unknown_competition(model_class, kwargs))

    @classmethod
    def _build(cls, model_class, *args, **kwargs):
        return super()._build(model_class, *args, **without_unknown_competition(model_class, kwargs))


def create_scoped(model, competition, **kwargs):
    """Wiersz modelu założony wprost, z konkursem ustawionym tylko wtedy, gdy model ma już to pole.

    Dla modeli, które nie mają (i nie będą miały) własnej fabryki – ``support.SupportTicket``,
    ``core.AuditLog``, ``accounts.MessageBroadcast``. Ta sama reguła, co w
    ``CompetitionScopedFactory``, wyrażona funkcją, żeby test krzyżowy nie musiał zakładać fabryki
    dla modelu, który poza nim nikomu nie jest potrzebny.
    """
    return model.objects.create(**without_unknown_competition(model, {**kwargs, "competition": competition}))


def grant_membership(user, competition, role: str):
    """Nadaje rolę w konkursie, o ile model członkostw już istnieje (T2).

    Do czasu T2 rolą jest globalna grupa Django i nadają ją fabryki kont – wtedy ta funkcja nic nie
    robi i zwraca ``None``. Po T2 to samo wywołanie zaczyna zapisywać ``accounts.Membership``, więc
    testy krzyżowe nie wymagają przepisania: zmienia się kod, nie asercja.
    """
    try:
        from django.apps import apps as django_apps

        Membership = django_apps.get_model("accounts", "Membership")
    except LookupError:
        return None
    membership, _ = Membership.objects.get_or_create(user=user, competition=competition, role=role)
    return membership
