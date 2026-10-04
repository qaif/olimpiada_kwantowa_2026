"""Dyplomy medalowe i zaświadczenia o udziale w języku ucznia (MED-01 § 2).

Rejestr, numer, kod weryfikacyjny, pieczęć, paczka ZIP i strona ``/dyplomy/<kod>/`` zostają
w ``apps.results.certificates`` – ten moduł dokłada wyłącznie **skład**: treść w języku ucznia
i tekst w dowolnym piśmie (``apps.medals.typesetting``). Wpina się w ``render_pdf`` przez
``register_composer`` (``apps.medals.apps``), więc każde dotychczasowe wejście – panel
koordynatora, „Moje dyplomy” ucznia, paczka ZIP – daje medalowy dyplom bez jednej zmiany w widokach.

Które dokumenty składa ten moduł (``handles``):

- cztery rodzaje medalowe – zawsze (wystawia je wyłącznie ekran medali),
- zaświadczenie o udziale (``UCZESTNIK``) – wyłącznie w konkursie z flagą ``medals``. Olimpiada
  Kwantowa składa je dalej po staremu, co do bajtu.

Język dokumentu jest zamrażany przy wystawieniu (``CertificateLanguage``). Dokument bez tego
wiersza (zaświadczenie wystawione z dotychczasowego panelu) bierze język ucznia z chwili pobrania.
Pismo, którego nie da się złożyć, daje dokument po angielsku – jawnie, z wpisem w logu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from io import BytesIO

from django.conf import settings
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from apps.results.certificate_layout import DEFAULT_LAYOUT, block, is_visible
from apps.results.certificates import (
    _draw_background,
    _draw_picture,
    _draw_qr,
    _image_bytes,
    _merge_background,
    competition_logo,
    resolve_template,
    verification_url,
)
from apps.results.models import Certificate, CertificateKind

from . import typesetting
from .models import Award, CertificateLanguage, enabled

logger = logging.getLogger(__name__)

#: Rodzaj dokumentu każdej nagrody. ``NONE`` nie ma dyplomu – dostaje zaświadczenie o udziale.
AWARD_KINDS: dict[str, str] = {
    Award.GOLD: CertificateKind.MEDAL_GOLD,
    Award.SILVER: CertificateKind.MEDAL_SILVER,
    Award.BRONZE: CertificateKind.MEDAL_BRONZE,
    Award.HONOURABLE: CertificateKind.HON_MENTION,
}
KIND_AWARDS = {kind: award for award, kind in AWARD_KINDS.items()}
PARTICIPATION_KIND = CertificateKind.UCZESTNIK

#: Napisy dokumentu. ``gettext_noop``, bo tłumaczy je dopiero ``localized_content`` – w języku
#: **ucznia**, a nie w języku żądania, z którego dokument pobrano (koordynator po polsku).
#: Zdania są bez rodzaju gramatycznego („za wybitny wynik”), żeby jedno zdanie pasowało do każdego
#: nazwiska w każdym z jedenastu języków – a nie „uzyskał(a)” przepisane na arabski.
TITLES = {
    CertificateKind.MEDAL_GOLD: gettext_noop("Złoty medal"),
    CertificateKind.MEDAL_SILVER: gettext_noop("Srebrny medal"),
    CertificateKind.MEDAL_BRONZE: gettext_noop("Brązowy medal"),
    CertificateKind.HON_MENTION: gettext_noop("Wyróżnienie"),
    CertificateKind.UCZESTNIK: gettext_noop("Zaświadczenie o udziale"),
}
STATEMENTS = {
    CertificateKind.MEDAL_GOLD: gettext_noop("za wybitny wynik w zawodach %(competition)s"),
    CertificateKind.MEDAL_SILVER: gettext_noop("za wybitny wynik w zawodach %(competition)s"),
    CertificateKind.MEDAL_BRONZE: gettext_noop("za wybitny wynik w zawodach %(competition)s"),
    CertificateKind.HON_MENTION: gettext_noop("za wyróżniający się wynik w zawodach %(competition)s"),
    CertificateKind.UCZESTNIK: gettext_noop("za udział w zawodach %(competition)s"),
}
#: Niewidoczny znak „od lewej do prawej” (U+200E) – zamyka adres URL w akapicie RTL.
LRM = "‎"
EDITION_LINE = gettext_noop("edycja %(edition)s")
SIGNATURE_LINE = gettext_noop("Przewodniczący Komitetu Organizacyjnego")
NUMBER_LABEL = gettext_noop("Numer dokumentu: %(number)s")
CODE_LABEL = gettext_noop("Kod weryfikacyjny: %(code)s")
DATE_LABEL = gettext_noop("Data wystawienia: %(date)s")
VERIFY_LABEL = gettext_noop("Weryfikacja: %(url)s")


def handles(certificate: Certificate) -> bool:
    """Czy ten dokument składa ten moduł (patrz docstring modułu). Bez zapytania poza relacjami
    dokumentu, które ``render_pdf`` i tak zaraz czyta."""
    if certificate.kind in KIND_AWARDS:
        return True
    if certificate.kind != PARTICIPATION_KIND or certificate.entry_id is None:
        return False
    return enabled(certificate.edition.competition)


# --- język ---------------------------------------------------------------------------------------


def student_language(user, competition) -> str:
    """Język ucznia: jego zapis, jeśli konkurs ten język oferuje, inaczej język domyślny konkursu.

    Ta sama reguła i te same funkcje, co ``apps.accounts.preferences.language_for`` (list do
    ucznia), tylko bez aktywowania języka – dokument tłumaczy się sam, w ``translation.override``.
    """
    from apps.accounts.preferences import competition_language, competition_languages, stored_preference

    preference = stored_preference(user)
    language = getattr(preference, "language", "") or ""
    if language not in competition_languages(competition):
        language = competition_language(competition) or settings.LANGUAGE_CODE
    return language


def certificate_language(certificate: Certificate) -> str:
    """Język zapisany przy wystawieniu albo – bez wiersza – język ucznia z tej chwili."""
    stored = (
        CertificateLanguage.objects.filter(certificate=certificate).values_list("language", flat=True).first()
    )
    if stored:
        return stored
    return student_language(certificate.entry.participant.user, certificate.edition.competition)


def remember_language(certificate: Certificate, language: str) -> None:
    """Zamraża język dokumentu – tylko przy pierwszym wystawieniu (powtórne „Wystaw” go nie zmienia)."""
    CertificateLanguage.objects.get_or_create(certificate=certificate, defaults={"language": language})


# --- treść ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LocalizedContent:
    """Wszystko, co stoi na papierze – już w języku dokumentu."""

    language: str
    rtl: bool
    organiser: str
    edition: str
    title: str
    recipient: str
    school: str
    statement: str
    signature_line: str
    number_line: str
    code_line: str
    date_line: str
    verify_line: str
    verification_url: str
    author: str
    subject: str
    number: str
    logo: bytes | None = None


def _country_of(participant) -> str:
    """Kraj ucznia: kraj delegacji, a bez niej region profilu (nazwa ze słownika konkursu)."""
    delegation = getattr(participant, "delegation", None)
    if delegation is not None:
        return delegation.country.name
    region = getattr(participant, "region", None)
    return region.name if region is not None else ""


def _template_texts(certificate: Certificate, competition, language: str) -> dict | None:
    """Tekst organizatora z ``tenancy.DocumentTemplate`` – wyłącznie w języku domyślnym konkursu.

    Szablon tekstu jest jednojęzyczny (organizator pisze go w swoim języku), więc dla pozostałych
    języków obowiązują tłumaczenia wbudowane – inaczej uczeń z Indii dostałby dyplom „w języku
    ucznia”, którego jedyne zdanie jest po angielsku.
    """
    from apps.accounts.preferences import competition_language
    from apps.tenancy.documents import render_document

    if language != (competition_language(competition) or settings.LANGUAGE_CODE):
        return None
    document = render_document(
        competition,
        certificate.kind,
        version=certificate.template_version,
        fallback=None,
        recipient=_recipient(certificate),
        edition=certificate.edition.year_label,
        number=certificate.number,
        code=certificate.code,
        participant_code=certificate.entry.participant.public_code,
    )
    if not document.version:
        return None
    return {
        "title": document.title,
        "statement": document.statement,
        "signature_line": document.signature_line,
    }


def _recipient(certificate: Certificate) -> str:
    user = certificate.entry.participant.user
    return user.get_full_name() or user.email


def localized_content(certificate: Certificate, language: str | None = None) -> LocalizedContent:
    """Treść dokumentu w języku ucznia (albo w ``language``), z odwrotem na angielski."""
    from apps.tenancy.branding import competition_name

    competition = certificate.edition.competition
    requested = language or certificate_language(certificate)
    language = typesetting.renderable_language(requested)
    participant = certificate.entry.participant
    name = competition_name(competition)
    school = ", ".join(
        part for part in ((participant.school or "").strip(), _country_of(participant)) if part
    )
    issued_on = timezone.localtime(certificate.issued_at).strftime("%d.%m.%Y")
    url = verification_url(certificate.code, competition)
    custom = _template_texts(certificate, competition, language)
    with translation.override(language):
        title = custom["title"] if custom else _(TITLES.get(certificate.kind, TITLES[PARTICIPATION_KIND]))
        statement = (
            custom["statement"]
            if custom
            else _(STATEMENTS.get(certificate.kind, STATEMENTS[PARTICIPATION_KIND])) % {"competition": name}
        )
        signature = custom["signature_line"] if custom else _(SIGNATURE_LINE)
        rtl = translation.get_language_bidi()
        return LocalizedContent(
            language=language,
            rtl=rtl,
            organiser=name.upper()
            if typesetting.LANGUAGE_SCRIPTS.get(language) == typesetting.LATIN
            else name,
            edition=_(EDITION_LINE) % {"edition": certificate.edition.year_label},
            title=title,
            recipient=_recipient(certificate),
            school=school,
            statement=statement,
            signature_line=signature,
            number_line=_(NUMBER_LABEL) % {"number": certificate.number},
            code_line=_(CODE_LABEL) % {"code": certificate.code},
            date_line=_(DATE_LABEL) % {"date": issued_on},
            # LRM za adresem: w akapicie RTL końcowy „/” przeskoczyłby inaczej na lewy brzeg adresu.
            verify_line=_(VERIFY_LABEL) % {"url": url + (LRM if rtl else "")},
            verification_url=url,
            author=name,
            subject=f"{title} – {name} {certificate.edition.year_label}",
            number=certificate.number,
            logo=competition_logo(competition),
        )


# --- skład ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _QrContent:
    """Minimum, którego ``results.certificates._draw_qr`` potrzebuje od treści: adres weryfikacji."""

    verification_url: str


def compose_localized(content: LocalizedContent, template=None) -> bytes:
    """Bajty dokumentu: ten sam układ (``CertificateTemplate.layout``), co dyplom Olimpiady
    Kwantowej – tło, logo, podpisy i QR z ``apps.results.certificates`` – a tekst przez
    ``typesetting``, czyli w każdym piśmie i w każdym kierunku."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.pdfgen import canvas as pdf_canvas

    layout = template.layout if template is not None else {}
    buffer = BytesIO()
    width, height = landscape(A4)
    canvas = pdf_canvas.Canvas(buffer, pagesize=landscape(A4))
    canvas.setTitle(f"{content.title} {content.number}")
    canvas.setAuthor(content.author)
    canvas.setSubject(content.subject)
    canvas.setCreator("olimpiada – apps.medals")

    _draw_background(canvas, template, width=width, height=height)
    logo = block(layout, "logo")
    if is_visible(logo):
        picture = _image_bytes(template.logo) if template is not None and template.logo else None
        _draw_picture(
            canvas,
            picture or content.logo,
            x=float(logo.get("x", 60)),
            y=height - float(logo.get("y", 40)) - float(logo.get("height", 60)),
            width=float(logo.get("width", 140)),
            height=float(logo.get("height", 60)),
        )
    missing: list[str] = []
    organiser = block(layout, "organiser")
    # Nagłówek układu domyślnego to „OLIMPIADA KWANTOWA” – w dokumencie innego konkursu stoi jego
    # nazwa, chyba że szablon graficzny wpisał własny napis (wtedy to decyzja organizatora).
    organiser_text = organiser.get("text")
    if organiser_text == DEFAULT_LAYOUT["organiser"]["text"]:
        organiser_text = None
    for text, name in (
        (organiser_text or content.organiser, "organiser"),
        (content.edition, "edition"),
        (content.title, "title"),
        (content.recipient, "recipient"),
        (content.school, "school"),
        (content.statement, "statement"),
    ):
        missing += _block_text(canvas, text, block(layout, name), width=width, height=height, rtl=content.rtl)
    missing += _draw_signatures(canvas, template, content, layout=layout, width=width, height=height)
    missing += _draw_footer(canvas, content, layout=layout, width=width, height=height)
    _draw_qr(canvas, _QrContent(content.verification_url), layout=layout, height=height)
    canvas.showPage()
    canvas.save()
    if missing:
        logger.warning("Dokument %s: znaki bez kroju (%s).", content.number, "".join(sorted(set(missing))))
    return _merge_background(buffer.getvalue(), template)


#: Margines, którego długi napis nie przekracza – nazwisko ze stu znaków zmniejsza stopień pisma,
#: zamiast wyjść poza kartkę.
SIDE_MARGIN = 50


def _block_text(canvas, text: str, settings: dict, *, width: float, height: float, rtl: bool) -> list[str]:
    """Jeden blok układu (``x`` pominięte = wyśrodkowanie) – odpowiednik ``results.certificates._text``."""
    if not text or not is_visible(settings):
        return []
    y = height - float(settings.get("y", 0))
    x = settings.get("x")
    line = typesetting.draw_text(
        canvas,
        text,
        x=width / 2 if x is None else float(x),
        y=y,
        size=float(settings.get("size", 12)),
        bold=bool(settings.get("bold")),
        align="center" if x is None else ("right" if rtl else "left"),
        rtl_hint=rtl,
        max_width=width - 2 * SIDE_MARGIN,
    )
    return list(line.missing)


def _draw_signatures(
    canvas, template, content: LocalizedContent, *, layout: dict, width: float, height: float
) -> list[str]:
    """Bloki podpisu jak w ``results.certificates._draw_signatures`` – napisy przez ``typesetting``."""
    settings = block(layout, "signatures")
    if not is_visible(settings):
        return []
    blocks = template.signatures() if template is not None else []
    if not blocks:
        blocks = [{"image": None, "name": "", "title": content.signature_line}]
    line_width = float(settings.get("width", 240))
    size = float(settings.get("size", 10))
    baseline = height - float(settings.get("y", 445))
    slot = width / (len(blocks) + 1)
    canvas.setLineWidth(0.7)
    missing: list[str] = []
    for index, signature in enumerate(blocks, start=1):
        center = slot * index
        _draw_picture(
            canvas,
            _image_bytes(signature["image"]),
            x=center - line_width / 2,
            y=baseline + 6,
            width=line_width,
            height=46,
        )
        canvas.line(center - line_width / 2, baseline, center + line_width / 2, baseline)
        offset = baseline - size - 7
        for text in (signature["name"], signature["title"]):
            if not text:
                continue
            line = typesetting.draw_text(
                canvas, text, x=center, y=offset, size=size, rtl_hint=content.rtl, max_width=slot - 10
            )
            missing += line.missing
            offset -= size + 3
    return missing


def _draw_footer(
    canvas, content: LocalizedContent, *, layout: dict, width: float, height: float
) -> list[str]:
    """Stopka: numer i kod, data i adres weryfikacji. W dokumencie RTL kolumny zamieniają się stronami."""
    from apps.results.certificates import QR_FOOTER_GAP, _qr_box, margin_of

    settings = block(layout, "footer")
    if not is_visible(settings):
        return []
    size = float(settings.get("size", 9))
    margin = margin_of(layout)
    top = height - float(settings.get("y", 525))
    right_edge = width - margin
    box = _qr_box(_QrContent(content.verification_url), layout=layout, height=height)
    if box is not None:
        qr_x, qr_bottom, qr_size = box
        if qr_bottom < top + size and qr_bottom + qr_size > top - size - 7 and qr_x < right_edge:
            right_edge = qr_x - QR_FOOTER_GAP
    start, end = (right_edge, margin) if content.rtl else (margin, right_edge)
    start_align, end_align = ("right", "left") if content.rtl else ("left", "right")
    missing: list[str] = []
    for text, x, y, align in (
        (content.number_line, start, top, start_align),
        (content.code_line, start, top - size - 5, start_align),
        (content.date_line, end, top, end_align),
        (content.verify_line, end, top - size - 5, end_align),
    ):
        line = typesetting.draw_text(canvas, text, x=x, y=y, size=size, align=align, rtl_hint=content.rtl)
        missing += line.missing
    return missing


def compose_certificate(certificate: Certificate) -> bytes:
    """Wejście z ``results.certificates.render_pdf``: treść w języku ucznia i skład. Bez pieczęci –
    pieczętuje wołający, wspólnie dla wszystkich dokumentów."""
    content = localized_content(certificate)
    template = resolve_template(certificate.kind, certificate.edition)
    return compose_localized(content, template)


def language_overview() -> list[dict]:
    """Stan potoku składu dla każdego języka interfejsu – do ekranu medali (MED-01 § 2.1)."""
    rows = []
    for code, label in settings.LANGUAGES:
        support = typesetting.language_support(code)
        rows.append(
            {
                "code": code,
                "label": label,
                "script": support.script,
                "ok": support.ok,
                "reason": support.reason,
                "fallback": "" if support.ok else typesetting.FALLBACK_LANGUAGE,
            }
        )
    return rows
