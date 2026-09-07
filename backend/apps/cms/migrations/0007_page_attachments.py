"""Wiele plików do pobrania przy stronie: ``DocumentPage.attachment`` → ``…Attachment`` (orderable).

Pojedyncze pole zmuszało do wyboru jednej postaci dokumentu. Regulamin ma dwie: podpisany PDF
(wersja do druku i do cytowania) oraz plik źródłowy .docx – i obie mają być do pobrania, w tej
kolejności. Analogiczna lista pojawia się przy ``ContentPage`` (skład komitetów).

Kolejność operacji jest istotna: najpierw powstają tabele, potem migracja danych przenosi
istniejące załączniki, a dopiero na końcu znika stara kolumna. Odwrotna kolejność – ta, którą
proponuje ``makemigrations`` – skasowałaby powiązanie, zanim ktokolwiek zdąży je odczytać.

Migracja wsteczna przenosi z powrotem **pierwszy** plik dokumentu (``sort_order``); pozostałe
przepadają, bo stare pole mieści jeden. To świadome zawężenie, nie przeoczenie: wycofanie
schematu nie ma jak zachować danych, których stara struktura nie potrafi zapisać.
"""

import django.db.models.deletion
import modelcluster.fields
from django.db import migrations, models

#: Etykiety ról – te same, co w ``apps.cms.attachments``. Migracja nie importuje tamtego modułu:
#: kod aplikacji zmienia się w czasie, a migracja ma odtwarzać ten sam stan także za rok.
LABEL_PDF = "PDF do druku"
LABEL_SOURCE = "Wersja źródłowa ({extension})"


def move_attachment_to_inline(apps, schema_editor):
    """Istniejący ``DocumentPage.attachment`` staje się pierwszym wierszem listy plików."""
    DocumentPage = apps.get_model("cms", "DocumentPage")
    DocumentPageAttachment = apps.get_model("cms", "DocumentPageAttachment")

    rows = []
    for page in DocumentPage.objects.filter(attachment__isnull=False).select_related("attachment"):
        # ``file_extension`` jest property modelu Wagtaila – historyczny model go nie ma,
        # więc rozszerzenie liczymy z nazwy pliku tak samo, jak robi to Wagtail.
        extension = page.attachment.file.name.rsplit(".", 1)[-1].lower() if page.attachment.file else ""
        label = LABEL_PDF if extension == "pdf" else LABEL_SOURCE.format(extension=extension.upper())
        rows.append(
            DocumentPageAttachment(
                page_id=page.pk,
                document_id=page.attachment_id,
                label=label,
                sort_order=0,
            )
        )
    DocumentPageAttachment.objects.bulk_create(rows)


def move_inline_to_attachment(apps, schema_editor):
    """Wstecz: pierwszy plik z listy wraca do pola ``attachment``."""
    DocumentPage = apps.get_model("cms", "DocumentPage")
    DocumentPageAttachment = apps.get_model("cms", "DocumentPageAttachment")

    for page in DocumentPage.objects.all():
        first = DocumentPageAttachment.objects.filter(page_id=page.pk).order_by("sort_order", "pk").first()
        if first is not None:
            page.attachment_id = first.document_id
            page.save(update_fields=["attachment"])


class Migration(migrations.Migration):
    dependencies = [
        ("cms", "0006_site_settings_defaults"),
        ("wagtaildocs", "0014_alter_document_file_size"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentPageAttachment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("sort_order", models.IntegerField(blank=True, editable=False, null=True)),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        help_text="Rola pliku, np. „PDF do druku”. Puste = tytuł pliku z biblioteki.",
                        max_length=100,
                        verbose_name="etykieta",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="wagtaildocs.document",
                        verbose_name="plik",
                    ),
                ),
                (
                    "page",
                    modelcluster.fields.ParentalKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="cms.contentpage",
                    ),
                ),
            ],
            options={
                "verbose_name": "plik strony",
                "verbose_name_plural": "pliki strony",
                "ordering": ["sort_order"],
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="DocumentPageAttachment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("sort_order", models.IntegerField(blank=True, editable=False, null=True)),
                (
                    "label",
                    models.CharField(
                        blank=True,
                        help_text="Rola pliku, np. „PDF do druku”. Puste = tytuł pliku z biblioteki.",
                        max_length=100,
                        verbose_name="etykieta",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="wagtaildocs.document",
                        verbose_name="plik",
                    ),
                ),
                (
                    "page",
                    modelcluster.fields.ParentalKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attachments",
                        to="cms.documentpage",
                    ),
                ),
            ],
            options={
                "verbose_name": "plik dokumentu",
                "verbose_name_plural": "pliki dokumentu",
                "ordering": ["sort_order"],
                "abstract": False,
            },
        ),
        migrations.RunPython(move_attachment_to_inline, move_inline_to_attachment),
        migrations.RemoveField(model_name="documentpage", name="attachment"),
    ]
