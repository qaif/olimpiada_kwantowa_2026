"""Zestawy wtyczek z tabeli 6.1 speca – wspólne dla ustawień (``CMS_PLACEHOLDER_CONF``) i wtyczek.

Osobny moduł bez importów Django: czytają go ustawienia (``config/settings/base.py``), które nie
mogą importować modeli ani ``cms_plugins``.

- **ART** – ``ArticleStreamBlock`` Wagtaila: tekst, obraz, dokument, film,
- **DOC** – ``DocumentStreamBlock``: ART + śródtytuł, ramka, tabela dwukolumnowa, harmonogram
  i terminy etapów (wtyczka żywa ``StageTimelinePlugin`` z ``apps.live``, DJ-01f).
"""

ART_PLUGINS = ["TextPlugin", "ImageWithCaptionPlugin", "DocumentLinkPlugin", "EmbedPlugin"]
DOC_PLUGINS = [
    *ART_PLUGINS,
    "HeadingPlugin",
    "NoticePlugin",
    "DefinitionListPlugin",
    "SchedulePlugin",
    "StageTimelinePlugin",
]
TEXT_ONLY = ["TextPlugin"]
