"""Bloki StreamField dla treści redakcyjnych.

Redaktor nie wpisuje HTML-a: ``RichTextBlock`` przepuszcza treść przez whitelistę Wagtaila
(``features`` niżej), a obrazy, dokumenty i osadzenia są wyborem z biblioteki, nie surowym
znacznikiem. W szablonach renderujemy je przez ``{% include_block %}`` albo ``|richtext``, nigdy
przez ``|safe`` – patrz docstring ``apps/cms/models.py``.
"""

from wagtail import blocks
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.embeds.blocks import EmbedBlock
from wagtail.images.blocks import ImageChooserBlock

#: Dozwolone formatowanie w akapicie. Świadomie bez ``image``/``embed`` (są osobnymi blokami)
#: i bez surowego HTML – whitelist Wagtaila jest jedyną drogą treści do szablonu.
RICH_TEXT_FEATURES = [
    "h2",
    "h3",
    "h4",
    "bold",
    "italic",
    "ol",
    "ul",
    "hr",
    "link",
    "document-link",
    "superscript",
    "subscript",
    "blockquote",
]


class ImageWithCaptionBlock(blocks.StructBlock):
    """Obraz z podpisem. Tekst alternatywny bierze się z opisu obrazu w bibliotece."""

    image = ImageChooserBlock(label="obraz")
    caption = blocks.CharBlock(required=False, max_length=250, label="podpis")

    class Meta:
        icon = "image"
        label = "obraz"
        template = "cms/blocks/image.html"


class DocumentBlock(blocks.StructBlock):
    """Załącznik do pobrania (np. treść zadań w PDF). Link idzie przez widok ``/documents/``."""

    document = DocumentChooserBlock(label="dokument")
    label = blocks.CharBlock(required=False, max_length=250, label="etykieta linku")

    class Meta:
        icon = "doc-full"
        label = "dokument"
        template = "cms/blocks/document.html"


class ArticleStreamBlock(blocks.StreamBlock):
    """Treść artykułu: akapit, obraz, dokument, osadzenie."""

    paragraph = blocks.RichTextBlock(features=RICH_TEXT_FEATURES, label="akapit")
    image = ImageWithCaptionBlock()
    document = DocumentBlock()
    embed = EmbedBlock(label="osadzenie (film, prezentacja)")

    class Meta:
        required = False
