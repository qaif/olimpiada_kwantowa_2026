"""Katalog języków docelowych tłumaczeń zadań (TR-01 § 1).

To **nie** jest lista języków interfejsu (``settings.LANGUAGES``, jedenaście pozycji): zadanie IQO
tłumaczy się na język ucznia, a delegacje przyjeżdżają z krajów, których języka platforma nie ma
i mieć nie musi (niemiecki, ukraiński, wietnamski…). Interfejs takiego ucznia zostaje angielski,
a treść zadania – w jego języku.

Kod jest znacznikiem BCP 47 w postaci, którą rozumie atrybut ``lang`` w HTML-u (``zh-Hans``,
``pt-BR``) – ten sam kod trafia na stronę ucznia, żeby przeglądarka dobrała krój i kierunek pisma.
Nazwy są podwójne: angielska (lista wyboru na anglojęzycznym ekranie opiekuna, eksport) i własna
(„Deutsch”, „Українська”) – uczeń i opiekun rozpoznają swój język po jego własnej nazwie.
"""

from __future__ import annotations

#: (kod BCP 47, nazwa angielska, nazwa własna, pismo od prawej do lewej)
LANGUAGES: tuple[tuple[str, str, str, bool], ...] = (
    ("sq", "Albanian", "Shqip", False),
    ("ar", "Arabic", "العربية", True),
    ("hy", "Armenian", "Հայերեն", False),
    ("az", "Azerbaijani", "Azərbaycanca", False),
    ("eu", "Basque", "Euskara", False),
    ("be", "Belarusian", "Беларуская", False),
    ("bn", "Bengali", "বাংলা", False),
    ("bs", "Bosnian", "Bosanski", False),
    ("bg", "Bulgarian", "Български", False),
    ("ca", "Catalan", "Català", False),
    ("zh-Hans", "Chinese (Simplified)", "简体中文", False),
    ("zh-Hant", "Chinese (Traditional)", "繁體中文", False),
    ("hr", "Croatian", "Hrvatski", False),
    ("cs", "Czech", "Čeština", False),
    ("da", "Danish", "Dansk", False),
    ("nl", "Dutch", "Nederlands", False),
    ("en", "English", "English", False),
    ("et", "Estonian", "Eesti", False),
    ("fil", "Filipino", "Filipino", False),
    ("fi", "Finnish", "Suomi", False),
    ("fr", "French", "Français", False),
    ("ka", "Georgian", "ქართული", False),
    ("de", "German", "Deutsch", False),
    ("el", "Greek", "Ελληνικά", False),
    ("he", "Hebrew", "עברית", True),
    ("hi", "Hindi", "हिन्दी", False),
    ("hu", "Hungarian", "Magyar", False),
    ("is", "Icelandic", "Íslenska", False),
    ("id", "Indonesian", "Bahasa Indonesia", False),
    ("ga", "Irish", "Gaeilge", False),
    ("it", "Italian", "Italiano", False),
    ("ja", "Japanese", "日本語", False),
    ("kk", "Kazakh", "Қазақ тілі", False),
    ("ko", "Korean", "한국어", False),
    ("ky", "Kyrgyz", "Кыргызча", False),
    ("lv", "Latvian", "Latviešu", False),
    ("lt", "Lithuanian", "Lietuvių", False),
    ("mk", "Macedonian", "Македонски", False),
    ("ms", "Malay", "Bahasa Melayu", False),
    ("mn", "Mongolian", "Монгол", False),
    ("ne", "Nepali", "नेपाली", False),
    ("nb", "Norwegian", "Norsk", False),
    ("fa", "Persian", "فارسی", True),
    ("pl", "Polish", "Polski", False),
    ("pt", "Portuguese", "Português", False),
    ("pt-BR", "Portuguese (Brazil)", "Português (Brasil)", False),
    ("ro", "Romanian", "Română", False),
    ("ru", "Russian", "Русский", False),
    ("sr", "Serbian", "Српски", False),
    ("si", "Sinhala", "සිංහල", False),
    ("sk", "Slovak", "Slovenčina", False),
    ("sl", "Slovenian", "Slovenščina", False),
    ("es", "Spanish", "Español", False),
    ("sv", "Swedish", "Svenska", False),
    ("tg", "Tajik", "Тоҷикӣ", False),
    ("ta", "Tamil", "தமிழ்", False),
    ("th", "Thai", "ไทย", False),
    ("tr", "Turkish", "Türkçe", False),
    ("tk", "Turkmen", "Türkmençe", False),
    ("uk", "Ukrainian", "Українська", False),
    ("ur", "Urdu", "اردو", True),
    ("uz", "Uzbek", "Oʻzbekcha", False),
    ("vi", "Vietnamese", "Tiếng Việt", False),
)

_BY_CODE = {code: (english, native, rtl) for code, english, native, rtl in LANGUAGES}

#: Najdłuższy kod w katalogu z zapasem – długość kolumn ``language``/``code``.
CODE_MAX_LENGTH = 16


def is_known(code: str) -> bool:
    return code in _BY_CODE


def english_name(code: str) -> str:
    """Nazwa angielska – albo sam kod, gdy języka już nie ma w katalogu (stary wiersz w bazie)."""
    return _BY_CODE.get(code, (code, code, False))[0]


def native_name(code: str) -> str:
    return _BY_CODE.get(code, (code, code, False))[1]


def label(code: str) -> str:
    """„German – Deutsch”: nazwa do list wyboru i tabel. Bez powtórzenia, gdy obie są równe."""
    english, native = english_name(code), native_name(code)
    return english if english == native else f"{english} – {native}"


def is_rtl(code: str) -> bool:
    return _BY_CODE.get(code, ("", "", False))[2]


def choices() -> list[tuple[str, str]]:
    return [(code, label(code)) for code, *_rest in LANGUAGES]
