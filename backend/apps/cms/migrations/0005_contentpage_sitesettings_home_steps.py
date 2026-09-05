"""Nowy typ ``ContentPage``, sekcja kroków na stronie głównej i ustawienia serwisu.

Migracja schematu wygenerowana przez ``makemigrations``; wartości początkowe ``SiteSettings``
ustawia osobna migracja danych ``0006_site_settings_defaults``.
"""

import django.db.models.deletion
import wagtail.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cms', '0004_documentpage'),
        ('wagtailcore', '0094_alter_page_locale'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContentPage',
            fields=[
                ('page_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='wagtailcore.page')),
                ('intro', wagtail.fields.RichTextField(blank=True, verbose_name='wprowadzenie')),
                ('body', wagtail.fields.StreamField([('paragraph', 0), ('image', 3), ('document', 6), ('embed', 7), ('heading', 12), ('notice', 15)], blank=True, block_lookup={0: ('wagtail.blocks.RichTextBlock', (), {'features': ['h2', 'h3', 'h4', 'bold', 'italic', 'ol', 'ul', 'hr', 'link', 'document-link', 'superscript', 'subscript', 'blockquote'], 'label': 'akapit'}), 1: ('wagtail.images.blocks.ImageChooserBlock', (), {'label': 'obraz'}), 2: ('wagtail.blocks.CharBlock', (), {'label': 'podpis', 'max_length': 250, 'required': False}), 3: ('wagtail.blocks.StructBlock', [[('image', 1), ('caption', 2)]], {}), 4: ('wagtail.documents.blocks.DocumentChooserBlock', (), {'label': 'dokument'}), 5: ('wagtail.blocks.CharBlock', (), {'label': 'etykieta linku', 'max_length': 250, 'required': False}), 6: ('wagtail.blocks.StructBlock', [[('document', 4), ('label', 5)]], {}), 7: ('wagtail.embeds.blocks.EmbedBlock', (), {'label': 'osadzenie (film, prezentacja)'}), 8: ('wagtail.blocks.CharBlock', (), {'label': 'tekst', 'max_length': 250}), 9: ('wagtail.blocks.ChoiceBlock', [], {'choices': [('2', 'rozdział (H2)'), ('3', 'paragraf (H3)')], 'label': 'poziom'}), 10: ('wagtail.blocks.CharBlock', (), {'help_text': 'Fragment adresu po „#”, np. „rozdzial-3”. Zmiana psuje istniejące odnośniki.', 'label': 'kotwica', 'max_length': 100}), 11: ('wagtail.blocks.BooleanBlock', (), {'default': True, 'label': 'pokaż w spisie rozdziałów', 'required': False}), 12: ('wagtail.blocks.StructBlock', [[('text', 8), ('level', 9), ('anchor', 10), ('in_toc', 11)]], {}), 13: ('wagtail.blocks.ChoiceBlock', [], {'choices': [('info', 'informacja'), ('warning', 'ostrzeżenie')], 'label': 'ton'}), 14: ('wagtail.blocks.RichTextBlock', (), {'features': ['h2', 'h3', 'h4', 'bold', 'italic', 'ol', 'ul', 'hr', 'link', 'document-link', 'superscript', 'subscript', 'blockquote'], 'label': 'treść'}), 15: ('wagtail.blocks.StructBlock', [[('tone', 13), ('text', 14)]], {})}, verbose_name='treść')),
                ('show_in_menu', models.BooleanField(default=False, help_text='Pozycja w pasku nawigacji na górze serwisu.', verbose_name='pokaż w menu głównym')),
            ],
            options={
                'verbose_name': 'strona treści',
                'verbose_name_plural': 'strony treści',
            },
            bases=('wagtailcore.page',),
        ),
        migrations.AddField(
            model_name='homepage',
            name='steps',
            field=wagtail.fields.StreamField([('step', 2)], blank=True, block_lookup={0: ('wagtail.blocks.CharBlock', (), {'label': 'tytuł kroku', 'max_length': 120}), 1: ('wagtail.blocks.TextBlock', (), {'label': 'opis', 'max_length': 300}), 2: ('wagtail.blocks.StructBlock', [[('title', 0), ('text', 1)]], {})}, verbose_name='kroki'),
        ),
        migrations.AddField(
            model_name='homepage',
            name='steps_title',
            field=models.CharField(blank=True, help_text='Puste = sekcja kroków się nie pokazuje.', max_length=200, verbose_name='nagłówek sekcji „jak zacząć”'),
        ),
        migrations.CreateModel(
            name='SiteSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('site_name', models.CharField(default='Olimpiada Kwantowa', max_length=100, verbose_name='nazwa serwisu')),
                ('tagline', models.CharField(blank=True, default='Przyszłość ma naturę kwantową.', help_text='Zdanie pod logotypem i w nagłówku strony głównej.', max_length=200, verbose_name='hasło')),
                ('organizer_name', models.CharField(default='Fundacja Quantum AI', max_length=200, verbose_name='organizator')),
                ('organizer_address', models.CharField(blank=True, default='ul. Sanocka 9/103, 02-110 Warszawa', max_length=200, verbose_name='adres organizatora')),
                ('organizer_registry', models.CharField(blank=True, default='KRS 0000808359 · NIP 7010955891 · REGON 384899425', max_length=200, verbose_name='dane rejestrowe')),
                ('contact_email', models.EmailField(blank=True, default='contact@qaif.org', max_length=254, verbose_name='e-mail kontaktowy')),
                ('contact_phone', models.CharField(blank=True, default='+48 507 982 292', max_length=40, verbose_name='telefon')),
                ('contact_url', models.URLField(blank=True, default='https://www.qaif.org/', verbose_name='strona organizatora')),
                ('site', models.OneToOneField(editable=False, on_delete=django.db.models.deletion.CASCADE, to='wagtailcore.site')),
            ],
            options={
                'verbose_name': 'dane serwisu',
                'verbose_name_plural': 'dane serwisu',
            },
        ),
    ]
