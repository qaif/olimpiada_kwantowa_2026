"""Klucze obce załączników stron (``ContentPageAttachment``, ``DocumentPageAttachment``, ``ArchiveDocument``)
na ``cms.Document``. Wartości się nie zmieniają: ``0032`` skopiowało dokumenty z tymi samymi kluczami.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0032_copy_wagtail_documents"),
    ]

    operations = [
        migrations.AlterField(
            model_name="archivedocument",
            name="document",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="cms.document",
                verbose_name="plik",
            ),
        ),
        migrations.AlterField(
            model_name="contentpageattachment",
            name="document",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="cms.document",
                verbose_name="plik",
            ),
        ),
        migrations.AlterField(
            model_name="documentpageattachment",
            name="document",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="cms.document",
                verbose_name="plik",
            ),
        ),
    ]
