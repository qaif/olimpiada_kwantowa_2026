"""Przełącznik ``path_prefix_routing`` zaczyna być czytany – konkursy pod prefiksem zostają tam, gdzie są.

Do tej zmiany konkurs w trybie ``PATH`` odpowiadał pod ``/<prefiks>/`` pod **każdym** hostem, a flaga
``path_prefix_routing`` nie miała czytelnika (uwaga T43). Od teraz prefiks liczy się wyłącznie pod
hostem, którego konkurs ma tę flagę włączoną (``apps.tenancy.resolution.hosts_path_prefixes``) –
flaga jest bramką **gospodarza**, bo to jego ciasteczka dzieli konkurs pod prefiksem.

Migracja zachowuje działające instalacje: jeśli w bazie stoi choć jeden aktywny konkurs w trybie
``PATH``, konkurs witryny domyślnej (host platformy) dostaje ``path_prefix_routing: true``. Baza bez
takiego konkursu – w tym produkcja Olimpiady Kwantowej z jednym konkursem – nie zmienia się
w ogóle, więc ``feature_flags`` Konkursu #1 zostaje puste.

Idempotentna (drugi przebieg nie ma czego zmienić), bez cofania skutku: wyłączenie bramki przy
cofnięciu migracji zgasiłoby konkursy pod prefiksem, a stary kod i tak flagi nie czyta.
"""

from django.db import migrations

FLAG = "path_prefix_routing"


def open_platform(apps, schema_editor):
    Competition = apps.get_model("tenancy", "Competition")
    if not Competition.objects.filter(routing_mode="PATH", is_active=True).exclude(path_prefix="").exists():
        return
    platform = Competition.objects.filter(site__is_default_site=True).first()
    if platform is None:
        return
    flags = dict(platform.feature_flags or {})
    if flags.get(FLAG) is True:
        return
    flags[FLAG] = True
    Competition.objects.filter(pk=platform.pk).update(feature_flags=flags)


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0009_document_kind_student_status"),
    ]

    operations = [
        migrations.RunPython(open_platform, migrations.RunPython.noop, elidable=False),
    ]
