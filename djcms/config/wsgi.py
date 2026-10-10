import os

from django.core.wsgi import get_wsgi_application

# Bezpiecznik (audyt bezpieczeństwa 10.10.2026): `DJCMS_BUILD=1` ustawia WYŁĄCZNIE djcms/Dockerfile
# na czas `collectstatic` – ustawienia przyjmują wtedy stały, jawny SECRET_KEY i pomijają kontrole
# sekretów (djcms/config/settings/base.py, `BUILDING_IMAGE`). Ta sama zmienna w środowisku DZIAŁAJĄCEGO
# serwera (dopisana do compose, skopiowana z polecenia budowania) po cichu podmieniałaby klucz na
# stałą z repozytorium – podpisy sesji i CSRF dałoby się wtedy podrobić. Serwer WSGI z nią nie wstaje.
_building = os.environ.get("DJCMS_BUILD", "").strip().lower()
if _building not in ("", "0", "false", "no", "off"):
    raise RuntimeError(
        "DJCMS_BUILD jest ustawione w środowisku działającego serwera – ta zmienna jest wyłącznie dla "
        "budowania obrazu (collectstatic w djcms/Dockerfile). Usuń ją ze środowiska usługi djcms."
    )

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
application = get_wsgi_application()
