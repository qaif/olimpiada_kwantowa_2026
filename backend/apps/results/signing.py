"""Pieczęć elektroniczna dokumentów (PAdES) – jedno wejście: ``sign_document``.

Po co pieczęć na dyplomie, skoro jest kod weryfikacyjny. Bo kod odpowiada na pytanie „czy taki
dokument wystawiono”, a pieczęć na pytanie „czy **ten plik** jest tym, co wystawiono”. Uczelnia,
która dostaje PDF pocztą, sprawdza go czytnikiem, a nie przepisywaniem kodu ze zdjęcia – i to
czytnik ma powiedzieć, że pliku nie ruszono po podpisaniu. Obie drogi zostają: kod działa na
papierze, pieczęć na pliku.

Trzy decyzje, które warto znać przed czytaniem kodu:

- **brak konfiguracji nie jest błędem**. Bez ``CERT_SIGN_P12_PATH`` funkcja oddaje wejście bez
  zmiany i mówi „niepodpisany”. Klucz pieczęci to materiał kryptograficzny organizacji; nie ma go
  ani na laptopie dewelopera, ani w testach, a dyplomy mają się tam składać normalnie,
- **awaria podpisu nie wstrzymuje dokumentu**. Wygasły certyfikat, brak pliku na wolumenie,
  niedostępne TSA – wszystko to trafia do logu, a uczestnik dostaje PDF bez pieczęci. Odwrotna
  decyzja znaczyłaby, że pomyłka w konfiguracji zatrzymuje wydawanie dyplomów w dniu gali,
- **import ``pyhanko`` jest leniwy**. Moduł czyta także panel koordynatora (rejestr dokumentów),
  a biblioteka podpisu ciągnie za sobą całą kryptografię – nie ma powodu ładować jej przy starcie
  procesu ani wywracać aplikacji, gdy obraz zbudowano bez tej zależności.

Czego ten moduł **nie** robi: nie obsługuje podpisu na tokenie USB ani w HSM. Kwalifikowana
pieczęć na karcie wymaga obecności karty przy podpisywaniu, a serwer podpisuje bez człowieka –
do tego służy usługa podpisu w chmurze dostawcy zaufania (README, „Dyplomy i zaświadczenia”).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

#: Nazwa pola podpisu w PDF-ie. Jedno, bo dokument pieczętuje wyłącznie organizator – miejsca na
#: kontrasygnatę na dyplomie nie ma i nie planujemy jej.
SIGNATURE_FIELD_NAME = "PieczecOrganizatora"


@dataclass(frozen=True)
class SignedDocument:
    """Wynik podpisywania: bajty do wydania oraz to, co o nich wiadomo.

    ``data`` jest zawsze kompletnym dokumentem – podpisanym, gdy się udało, i wejściowym, gdy się
    nie udało. Wywołujący nie musi więc obsługiwać przypadku „nie ma czego wydać”.
    """

    data: bytes
    signed: bool
    signer_name: str = ""


def is_configured() -> bool:
    """Czy pieczęć jest w ogóle włączona. Sam fakt istnienia ścieżki, bez czytania pliku."""
    return bool((getattr(settings, "CERT_SIGN_P12_PATH", "") or "").strip())


def sign_document(data: bytes) -> SignedDocument:
    """Podpisuje PDF pieczęcią organizatora albo oddaje go bez zmian.

    Wyjątków nie wypuszcza **żadnych**: każdy problem podpisu (brak biblioteki, brak pliku, złe
    hasło, wygasły certyfikat, milczące TSA) kończy się wpisem w logu i dokumentem bez pieczęci.
    To jest cała reguła tej funkcji i jedyny powód, dla którego łapiemy ``Exception`` szeroko –
    lista klas wyjątków ``pyhanko`` jest długa, zmienia się między wersjami, a skutek każdego
    z nich ma być tu identyczny.
    """
    if not is_configured():
        return SignedDocument(data=data, signed=False)
    try:
        return _sign(data)
    except Exception:  # patrz docstring: każda awaria podpisu daje ten sam skutek
        logger.exception("Nie udało się podpisać dokumentu – wydajemy go bez pieczęci.")
        return SignedDocument(data=data, signed=False)


def sign_pdf(data: bytes) -> bytes:
    """Same bajty dokumentu – dla wywołań, którym stan pieczęci jest obojętny."""
    return sign_document(data).data


def _sign(data: bytes) -> SignedDocument:
    """Właściwe podpisanie. Wydzielone, żeby ``sign_document`` był samą regułą „nigdy nie wybucha”."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigSeedSubFilter

    path = Path(settings.CERT_SIGN_P12_PATH)
    password = (getattr(settings, "CERT_SIGN_P12_PASSWORD", "") or "").encode()
    signer = signers.SimpleSigner.load_pkcs12(pfx_file=str(path), passphrase=password or None)
    if signer is None:  # pragma: no cover - pyhanko zwykle rzuca wyjątkiem, zanim tu dojdzie
        raise RuntimeError(f"Nie udało się wczytać pliku PKCS#12 z {path}.")
    meta = signers.PdfSignatureMetadata(
        field_name=SIGNATURE_FIELD_NAME,
        reason=getattr(settings, "CERT_SIGN_REASON", "") or None,
        location=getattr(settings, "CERT_SIGN_LOCATION", "") or None,
        # PAdES (ETSI), a nie zwykły podpis Adobe: tego wariantu wymagają polskie i unijne
        # przepisy o podpisie elektronicznym, a czytniki w urzędach sprawdzają właśnie jego.
        subfilter=SigSeedSubFilter.PADES,
    )
    writer = IncrementalPdfFileWriter(BytesIO(data))
    output = signers.sign_pdf(writer, meta, signer=signer, timestamper=_timestamper())
    return SignedDocument(data=output.getvalue(), signed=True, signer_name=signer_name(signer))


def _timestamper():
    """Znacznik czasu z TSA albo ``None``. Adres pusty = podpis z zegarem serwera."""
    url = (getattr(settings, "CERT_SIGN_TSA_URL", "") or "").strip()
    if not url:
        return None
    from pyhanko.sign.timestamps import HTTPTimeStamper

    return HTTPTimeStamper(url)


def signer_name(signer) -> str:
    """Nazwa pieczętującego z podmiotu certyfikatu: nazwa organizacji, a w jej braku – CN.

    Kolejność nie jest przypadkowa. Pieczęć elektroniczna należy do **podmiotu**, nie do osoby
    (to jej różnica wobec podpisu), więc na stronie weryfikacji ma stanąć nazwa olimpiady, a nie
    imię pracownika, który akurat zamówił certyfikat. CN zostaje wyjściem awaryjnym, bo bywa
    jedynym wypełnionym polem w certyfikatach testowych.
    """
    try:
        subject = signer.signing_cert.subject.native
    except Exception:  # nazwa jest ozdobą – jej brak nie może wywrócić podpisu
        return ""
    for key in ("organization_name", "common_name"):
        value = subject.get(key)
        if value:
            return str(value)[:200]
    return ""
