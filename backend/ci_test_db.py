"""Bazy testowe dla shardu CI: migracje **raz**, potem kopie dla workerów xdist (docs/TESTY.md § 5).

Użycie (z ``backend/``, przy ``DJANGO_SETTINGS_MODULE=config.settings.test``)::

    python ci_test_db.py 4                      # test_<baza> + test_<baza>_gw0 … _gw3
    python -m pytest --reuse-db -n 4 …          # workery zastają gotowe bazy

Bez tego każdy worker (``--create-db``) przechodzi wszystkie migracje sam – w CI cztery naraz na
czterech rdzeniach, razem z Postgresem, po 1,5–2,5 min (pomiar 8.10.2026, czas przygotowania
pierwszego testu każdego workera). Tutaj migracje idą jeden raz, bez konkurencji o procesor,
a kopia to ``CREATE DATABASE … TEMPLATE`` (Django ``clone_test_db`` – ta sama droga, którą idzie
``manage.py test --parallel``) – sekunda na workera. Nazwy kopii są dokładnie tymi, których szuka
pytest-django pod xdist (``test_<baza>_gw<N>``), a ``--reuse-db`` każe mu ich użyć: wtedy
``migrate`` niczego nie stosuje, tylko sprawdza stan i emituje ``post_migrate``.

Treść bazy jest ta sama, co przy ``--create-db``: ten sam ``create_test_db`` w tym samym środowisku
testowym (``setup_test_environment`` – tak jak fikstura ``django_test_environment`` pytest-django).
Gdyby którejś kopii zabrakło (inna liczba workerów), pytest-django założy ją sam, jak dotąd –
wolniej, ale z tym samym wynikiem. Skrypt jest wyłącznie dla jednorazowego Postgresa CI: kasuje
i zakłada bazy ``test_*`` bez pytania.
"""

import sys


def main(workers: int) -> None:
    import django

    django.setup()

    from django.db import connection
    from django.test.utils import setup_test_environment

    setup_test_environment(debug=False)
    connection.creation.create_test_db(verbosity=1, autoclobber=True, keepdb=False)
    for index in range(workers):
        connection.creation.clone_test_db(suffix=f"gw{index}", verbosity=1, autoclobber=True, keepdb=False)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
