"""Własny model dokumentu Wagtaila ``cms.Document`` – plik w prywatnym storage (audyt 10.10.2026, W2).

Pierwszy z czterech kroków podmiany ``WAGTAILDOCS_DOCUMENT_MODEL`` na instalacji, która ma już
dokumenty (Wagtail nie przenosi danych sam – „Custom document model” w dokumentacji Wagtaila):

- ``0031`` – pusta tabela ``cms_document``,
- ``0032`` – kopia wierszy ``wagtaildocs.Document`` z zachowaniem kluczy głównych, przepięcie tagów,
  indeksów, dziennika i uprawnień na nowy typ treści,
- ``0033`` – klucze obce załączników stron na ``cms.Document``,
- ``0034`` – wyłącznie wstecz: oddaje wiersze do ``wagtaildocs.Document`` przed cofnięciem ``0033``.

Kroki są osobnymi migracjami, bo PostgreSQL odmawia ``ALTER TABLE`` po zapisie danych w tej samej
transakcji („pending trigger events”). Pliki przenosi z publicznego bucketu do prywatnego polecenie
``manage.py migrate_documents_to_private`` – migracja nie dotyka storage, bo zapis do S3 nie cofa
się razem z bazą.
"""

import django.db.models.deletion
import modelsearch.index
import taggit.managers
import wagtail.models.media
from django.conf import settings
from django.db import migrations, models

import apps.cms.documents


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0030_hero_slider_images_intro_deadline"),
        ("taggit", "0006_rename_taggeditem_content_type_object_id_taggit_tagg_content_8fc721_idx"),
        ("wagtailcore", "0098_apitoken"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("title", models.CharField(max_length=255, verbose_name="title")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="created at")),
                ("file_size", models.PositiveBigIntegerField(editable=False, null=True)),
                ("file_hash", models.CharField(blank=True, editable=False, max_length=40)),
                (
                    "file",
                    models.FileField(
                        max_length=255,
                        storage=apps.cms.documents.private_documents_storage,
                        upload_to=apps.cms.documents.document_upload_to,
                        verbose_name="file",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        default=wagtail.models.media.get_root_collection_id,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="wagtailcore.collection",
                        verbose_name="collection",
                    ),
                ),
                (
                    "tags",
                    taggit.managers.TaggableManager(
                        blank=True,
                        help_text=None,
                        through="taggit.TaggedItem",
                        to="taggit.Tag",
                        verbose_name="tags",
                    ),
                ),
                (
                    "uploaded_by_user",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cms_documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="uploaded by user",
                    ),
                ),
            ],
            options={
                "verbose_name": "document",
                "verbose_name_plural": "documents",
                "permissions": [("choose_document", "Can choose document")],
                "abstract": False,
            },
            bases=(modelsearch.index.Indexed, models.Model),
        ),
    ]
