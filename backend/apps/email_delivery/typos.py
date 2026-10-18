"""Podpowiedź „Czy chodziło Ci o …?” dla literówek w domenie adresu e-mail (MAIL-02 § 1.1).

Prawdziwe przypadki z logu relaya: ``kcadera@o2.plo``, ``…@gmial.com``. Uczestnik, który pomyli
się w domenie, zakłada konto, na które nigdy nie przyjdzie link aktywacyjny – i nie wie dlaczego.

Zasada działania (ta sama co w ``static/email_delivery/email-check.js`` – test pilnuje, że obie
strony dają te same odpowiedzi na wspólnych przypadkach i mają te same listy):

1. domena z listy znanych dostawców → brak podpowiedzi,
2. najbliższa znana domena w odległości Damerau-Levenshteina (wariant OSA – zamiana sąsiednich
   liter kosztuje 1, bo ``gamil`` to najczęstsza literówka) ≤ 1 dla domen do 6 znaków i ≤ 2 dla
   dłuższych; przy remisie wygrywa wcześniejsza na liście (lista jest w kolejności popularności),
3. literówka w TLD z **jawnej** mapy (``.con`` → ``.com``). Jawnej, a nie „najbliższy TLD”, bo
   ``.cm``, ``.co`` i ``.om`` to prawdziwe domeny krajowe: na liście jest tylko to, co w praktyce
   jest pomyłką. Podpowiedź i tak jest wyłącznie pytaniem – adres można zostawić,
4. przed mapą TLD – znane wzorce z produkcji (REG-UX-01): podwojony TLD (``.pl.pl``), znana domena
   z doklejoną etykietą (``gmail.com.pl``), marka z cudzym TLD (``gmail.pl``, ``wp.com``) i domena
   bez kropki (``jan@gmailcom`` – podpowiedź w komunikacie o błędzie składni).

Część lokalna adresu nie jest ruszana (``Jan.Kowalski+olimp@`` zostaje co do znaku), a domena IDN
jest porównywana i podpowiadana w postaci Unicode (``xn--…`` dekodowane przed porównaniem).
"""

from __future__ import annotations

from encodings import idna

#: Dostawcy poczty, których adresy na pewno istnieją, w kolejności popularności wśród naszych
#: uczestników: najpierw Polska (Olimpiada Kwantowa), potem świat (IQO – Chiny, Indie, Korea,
#: Rosja, Brazylia, Europa). Domena z tej listy nigdy nie dostaje podpowiedzi i nie jest pytana
#: w DNS (``dnscheck``). Kopia w ``static/email_delivery/email-check.js`` – test porównuje obie.
KNOWN_DOMAINS: tuple[str, ...] = (
    "gmail.com",
    "wp.pl",
    "o2.pl",
    "onet.pl",
    "interia.pl",
    "op.pl",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "gazeta.pl",
    "tlen.pl",
    "poczta.fm",
    "vp.pl",
    "int.pl",
    "onet.eu",
    "go2.pl",
    "interia.eu",
    "interia.com",
    "poczta.onet.pl",
    "spoko.pl",
    "autograf.pl",
    "buziaczek.pl",
    "live.com",
    "msn.com",
    "outlook.de",
    "outlook.fr",
    "outlook.es",
    "outlook.it",
    "hotmail.co.uk",
    "hotmail.fr",
    "hotmail.de",
    "hotmail.it",
    "hotmail.es",
    "live.fr",
    "googlemail.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "gmx.com",
    "gmx.net",
    "gmx.de",
    "web.de",
    "t-online.de",
    "mail.com",
    "email.com",
    "zoho.com",
    "yahoo.co.uk",
    "yahoo.co.in",
    "yahoo.co.jp",
    "yahoo.fr",
    "yahoo.de",
    "yahoo.es",
    "yahoo.it",
    "yahoo.com.br",
    "ymail.com",
    "qq.com",
    "163.com",
    "126.com",
    "sina.com",
    "foxmail.com",
    "yeah.net",
    "aliyun.com",
    "naver.com",
    "daum.net",
    "hanmail.net",
    "mail.ru",
    "bk.ru",
    "list.ru",
    "inbox.ru",
    "yandex.ru",
    "yandex.com",
    "ya.ru",
    "rambler.ru",
    "ukr.net",
    "rediffmail.com",
    "orange.fr",
    "free.fr",
    "laposte.net",
    "libero.it",
    "seznam.cz",
    "uol.com.br",
    "bol.com.br",
)

#: Ostatnia etykieta domeny, która w praktyce jest pomyłką, i jej poprawka. Bez ``.co`` (Kolumbia,
#: popularna domena firm) – podpowiadałoby to poprawny adres w drugą stronę.
TLD_TYPOS: dict[str, str] = {
    "plo": "pl",
    "pll": "pl",
    "ppl": "pl",
    "lp": "pl",
    "pol": "pl",
    "con": "com",
    "cmo": "com",
    "ocm": "com",
    "cpm": "com",
    "comm": "com",
    "coom": "com",
    "copm": "com",
    "comn": "com",
    "vom": "com",
    "xom": "com",
    "cim": "com",
    "cm": "com",
    "om": "com",
    "nte": "net",
    "ner": "net",
    "nett": "net",
    "ogr": "org",
    "orgg": "org",
    "rog": "org",
}

#: Marki, których adres ma **jedną** domenę pocztową, i ta domena (REG-UX-01 § 1.1). Służy dwóm
#: wzorcom spoza odległości edycyjnej: „marka z cudzym TLD” (``gmail.pl`` – trzy zmiany od
#: ``gmail.com``, ``wp.com`` – trzy od ``wp.pl``) i „domena bez kropki” (``jan@gmail``). Wyłącznie
#: marki bez drugiej poczty pod inną końcówką: ``outlook``/``hotmail``/``yahoo`` mają krajowe warianty,
#: ale te są na liście znanych domen i podpowiedzi nie dostają, więc mapa ich nie zasłoni.
BRAND_DOMAINS: dict[str, str] = {
    "gmail": "gmail.com",
    "googlemail": "googlemail.com",
    "icloud": "icloud.com",
    "outlook": "outlook.com",
    "hotmail": "hotmail.com",
    "yahoo": "yahoo.com",
    "wp": "wp.pl",
    "o2": "o2.pl",
    "onet": "onet.pl",
    "interia": "interia.pl",
    "op": "op.pl",
    "tlen": "tlen.pl",
    "gazeta": "gazeta.pl",
    "vp": "vp.pl",
    "int": "int.pl",
}

#: Domena do tylu znaków włącznie dostaje podpowiedź wyłącznie przy odległości 1. Krótkie domeny
#: leżą blisko siebie (``wp.pl``, ``op.pl``, ``vp.pl``, ``o2.pl``) – dwie zmiany w pięciu znakach
#: to już zupełnie inny adres, a nie literówka.
SHORT_DOMAIN_LENGTH = 6


def distance(left: str, right: str) -> int:
    """Odległość Damerau-Levenshteina w wariancie OSA (zamiana sąsiednich znaków = 1 krok)."""
    rows = len(left) + 1
    cols = len(right) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            table[i][j] = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                table[i][j] = min(table[i][j], table[i - 2][j - 2] + 1)
    return table[-1][-1]


def unicode_domain(domain: str) -> str:
    """Domena małymi literami, bez kropki końcowej, z etykietami ``xn--`` w postaci Unicode."""
    domain = (domain or "").strip().rstrip(".").lower()
    labels = []
    for label in domain.split("."):
        if label.startswith("xn--"):
            try:
                label = idna.ToUnicode(label)
            except UnicodeError:
                pass
        labels.append(label)
    return ".".join(labels)


def suggest_domain(domain: str) -> str | None:
    """Poprawiona domena albo ``None``. ``domain`` może być w postaci ASCII (``xn--``) albo Unicode."""
    domain = unicode_domain(domain)
    if not domain or domain in KNOWN_DOMAINS:
        return None
    if "." not in domain:
        return _dotless_domain(domain)
    limit = 1 if len(domain) <= SHORT_DOMAIN_LENGTH else 2
    best: str | None = None
    best_distance = limit + 1
    for known in KNOWN_DOMAINS:
        # Różnica długości jest dolnym ograniczeniem odległości – tani filtr przed tabelą.
        if abs(len(known) - len(domain)) > limit:
            continue
        current = distance(domain, known)
        if current < best_distance:
            best, best_distance = known, current
    if best is not None and best_distance <= 1:
        return best
    # Przy odległości 2 wzorzec wygrywa: ``outlook.pl`` jest o dwie zmiany od ``outlook.de``,
    # ale ktoś, kto pisze z Polski, ma na myśli ``outlook.com`` (marka z cudzym TLD).
    pattern = _known_pattern(domain)
    if pattern is not None:
        return pattern
    if best is not None:
        return best
    head, _dot, tld = domain.rpartition(".")
    fixed = TLD_TYPOS.get(tld)
    if fixed and head:
        return f"{head}.{fixed}"
    return None


def _known_pattern(domain: str) -> str | None:
    """Wzorce z produkcji, których odległość edycyjna nie łapie (REG-UX-01 § 1.1).

    - podwojona ostatnia etykieta (``szkola.edu.pl.pl``, ``wp.pl.pl``) – bez niej, a wynik idzie
      jeszcze raz przez podpowiedź (``gmial.com.com`` → ``gmail.com``). Dotyczy każdej domeny, także
      szkolnej, bo ``.pl.pl`` nie bywa prawdziwym adresem – a podpowiedź i tak da się odrzucić,
    - znana domena z doklejoną etykietą (``gmail.com.pl``),
    - marka z cudzym TLD (``gmail.pl``, ``wp.com``) – wyłącznie dla dwóch etykiet, żeby
      ``op.edu.pl`` czy ``int.lodz.pl`` (prawdziwe domeny instytucji) nie dostały podpowiedzi.
    """
    labels = domain.split(".")
    if len(labels) >= 3 and labels[-1] == labels[-2]:
        shorter = ".".join(labels[:-1])
        return suggest_domain(shorter) or shorter
    if len(labels) >= 3 and ".".join(labels[:-1]) in KNOWN_DOMAINS:
        return ".".join(labels[:-1])
    if len(labels) == 2 and labels[0] in BRAND_DOMAINS:
        return BRAND_DOMAINS[labels[0]]
    return None


def _dotless_domain(domain: str) -> str | None:
    """Domena bez kropki (``gmail``, ``gmailcom``, ``wppl``) – sama marka albo znana domena bez kropek.

    Taki adres odrzuca już składnia, więc podpowiedź trafia do komunikatu o błędzie (``fields``);
    porównanie jest dokładne, bez odległości – w kilku literach bez kropki odległość 1 to zgadywanie.
    """
    if domain in BRAND_DOMAINS:
        return BRAND_DOMAINS[domain]
    for known in KNOWN_DOMAINS:
        if known.replace(".", "") == domain:
            return known
    return None


def suggest(address: str) -> str | None:
    """Adres z poprawioną domeną albo ``None``. Część lokalna zostaje co do znaku."""
    local, at, domain = (address or "").strip().rpartition("@")
    if not at or not local:
        return None
    fixed = suggest_domain(domain)
    return f"{local}@{fixed}" if fixed else None
