"""Scalenie gałęzi ``tenancy``: rodzaj szablonu „list zapraszający (wiza)” (LOG-01) i rodzaje medali (MED-01).

Obie gałęzie zmieniały listę wyboru ``DocumentTemplate.kind``; komplet obu list zapisuje ``0017``.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenancy', '0015_document_kind_visa_invitation'),
        ('tenancy', '0015_documenttemplate_award_kinds'),
    ]

    operations = [
    ]
