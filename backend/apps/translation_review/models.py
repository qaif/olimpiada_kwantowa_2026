"""Przegląd tłumaczeń interfejsu przez rodzimych użytkowników języka (zadanie L10N-01).

Katalogi gettext (``.po``) są źródłem prawdy i zostają w repozytorium. Tu stoi wyłącznie to, czego
katalog nie umie: **kto** może poprawiać który język, **propozycje** z głosami, zatwierdzone
**nakładki** działające od razu (zanim poprawka wróci do repozytorium komendą
``export_translations``) i **zgłoszenia** ze stopki.

Decyzje, na których stoją te modele:

- **rola jest platformowa, a nie konkursowa.** Napisy interfejsu są wspólne dla wszystkich
  konkursów instalacji, więc „tłumacz hiszpańskiego” nie ma ``competition`` – ma język. Dlatego
  osobna tabela, a nie wartość ``CompetitionRole`` (tamta lista opisuje role w jednym konkursie
  i ma odpowiednik w grupach Django),
- **napis identyfikuje klucz, a nie wiersz katalogu.** Ten sam ``msgid`` stoi w kilku plikach
  (katalog projektu i katalogi aplikacji), a numer linii zmienia się z każdym ``makemessages``.
  Klucz to SHA-256 z ``(msgctxt, msgid, forma mnoga)`` – stały, indeksowalny i bez ``TextField``
  w indeksie; sam ``msgid`` i ``msgctxt`` stoją obok, bo eksport musi je odnaleźć w pliku,
- **nakładka jest jedna na (język, klucz)** – to ona trafia do gettext w czasie działania. Historia
  decyzji jest w propozycjach i w audycie, nie w kolejnych wersjach nakładki,
- **autor znika, propozycja zostaje** (``SET_NULL``): tekst tłumaczenia nie jest daną osobową,
  a usunięcie konta wolontariusza nie może cofać poprawek, które ktoś już zatwierdził.
"""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.db import models
from django.utils import timezone


def string_key(msgctxt: str | None, msgid: str, plural_index: int | None = None) -> str:
    """Stały identyfikator napisu: SHA-256 z kontekstu, ``msgid`` i numeru formy mnogiej.

    ``None`` i pusty kontekst to ten sam napis – gettext je rozróżnia, ale w naszych katalogach
    pusty ``msgctxt`` nie występuje, a dwa klucze jednego napisu byłyby dwiema nakładkami.
    """
    raw = f"{msgctxt or ''}\x04{msgid}\x00{'' if plural_index is None else plural_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GrantLevel(models.TextChoices):
    """Dwa poziomy jednej roli. Recenzent umie wszystko, co tłumacz, i zatwierdza."""

    TRANSLATOR = "translator", "tłumacz"
    REVIEWER = "reviewer", "recenzent tłumaczeń"


class TranslatorGrant(models.Model):
    """Uprawnienie jednej osoby do jednego języka interfejsu."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="translator_grants"
    )
    language = models.CharField("język", max_length=16)
    level = models.CharField(
        "poziom", max_length=16, choices=GrantLevel.choices, default=GrantLevel.TRANSLATOR
    )
    #: Kto nadał. ``SET_NULL`` z tego samego powodu, co ``Membership.granted_by``: odejście
    #: koordynatora nie odbiera ról, które on nadał.
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="translator_grants_given",
        verbose_name="nadał",
    )
    granted_at = models.DateTimeField("nadane", default=timezone.now)

    class Meta:
        verbose_name = "uprawnienie tłumacza"
        verbose_name_plural = "uprawnienia tłumaczy"
        ordering = ("language", "user")
        constraints = [
            models.UniqueConstraint(fields=["user", "language"], name="translation_review_grant_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.language} ({self.level})"


class SuggestionStatus(models.TextChoices):
    PENDING = "pending", "czeka"
    APPROVED = "approved", "zatwierdzona"
    REJECTED = "rejected", "odrzucona"
    #: Inna propozycja dla tego napisu została zatwierdzona – ta nie jest już pytaniem.
    SUPERSEDED = "superseded", "zastąpiona"


class StringRef(models.Model):
    """Wspólne kolumny „który napis” – propozycja i nakładka wskazują napis tak samo."""

    language = models.CharField("język", max_length=16)
    key = models.CharField("klucz napisu", max_length=64)
    msgctxt = models.TextField("kontekst (msgctxt)", blank=True)
    msgid = models.TextField("napis źródłowy (msgid)")
    #: Numer formy mnogiej (``msgstr[n]``); ``None`` = napis bez liczby mnogiej.
    plural_index = models.PositiveSmallIntegerField("forma mnoga", null=True, blank=True)

    class Meta:
        abstract = True


class TranslationSuggestion(StringRef):
    """Propozycja poprawki jednego napisu. Zatwierdzona staje się :class:`TranslationOverride`."""

    text = models.TextField("proponowane tłumaczenie")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="translation_suggestions",
        verbose_name="autor",
    )
    status = models.CharField(
        "stan", max_length=16, choices=SuggestionStatus.choices, default=SuggestionStatus.PENDING
    )
    created_at = models.DateTimeField("zgłoszona", default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="translation_decisions",
        verbose_name="rozstrzygnął",
    )
    decided_at = models.DateTimeField("rozstrzygnięta", null=True, blank=True)

    class Meta:
        verbose_name = "propozycja tłumaczenia"
        verbose_name_plural = "propozycje tłumaczeń"
        ordering = ("-created_at", "-id")
        indexes = [
            # Ekran napisu i filtr „czeka propozycja” pytają dokładnie o tę parę.
            models.Index(fields=["language", "key", "status"], name="translation_review_sugg_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.language}:{self.key[:8]} ({self.status})"


class TranslationVote(models.Model):
    """Głos „to tłumaczenie jest dobre”. Jeden na osobę i propozycję; na własną się nie głosuje."""

    suggestion = models.ForeignKey(TranslationSuggestion, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="translation_votes"
    )
    created_at = models.DateTimeField("oddany", default=timezone.now)

    class Meta:
        verbose_name = "głos na tłumaczenie"
        verbose_name_plural = "głosy na tłumaczenia"
        constraints = [
            models.UniqueConstraint(fields=["suggestion", "user"], name="translation_review_vote_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} → {self.suggestion_id}"


class TranslationOverride(StringRef):
    """Zatwierdzone tłumaczenie, które gettext oddaje **zamiast** tekstu z katalogu.

    Żyje do chwili, w której ta sama treść trafi do ``.po`` (``export_translations`` → PR →
    wdrożenie → ``export_translations --prune``). Tekst przeszedł walidację
    (``apps.translation_review.validation``) – to jest jedyna droga do tej tabeli.
    """

    text = models.TextField("tłumaczenie")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="translation_overrides",
        verbose_name="zatwierdził",
    )
    approved_at = models.DateTimeField("zatwierdzone", default=timezone.now)
    suggestion = models.ForeignKey(
        TranslationSuggestion, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "zatwierdzone tłumaczenie"
        verbose_name_plural = "zatwierdzone tłumaczenia"
        ordering = ("language", "msgid")
        constraints = [
            models.UniqueConstraint(fields=["language", "key"], name="translation_review_override_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.language}:{self.key[:8]}"


class ReportStatus(models.TextChoices):
    OPEN = "open", "otwarte"
    CLOSED = "closed", "zamknięte"


class TranslationReport(models.Model):
    """„Ten napis na tej stronie jest źle przetłumaczony” – zgłoszenie ze stopki.

    Dokładne wskazanie ``msgid`` z poziomu strony wymagałoby oznaczania każdego napisu w HTML-u;
    zgłoszenie niesie więc ścieżkę strony (bez parametrów – te bywają tokenami) i frazę, którą
    człowiek widział, a recenzent szuka jej na liście napisów.
    """

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="translation_reports"
    )
    language = models.CharField("język", max_length=16)
    page = models.CharField("strona", max_length=300, blank=True)
    phrase = models.CharField("napis na stronie", max_length=300, blank=True)
    comment = models.TextField("uwaga", max_length=2000)
    status = models.CharField("stan", max_length=16, choices=ReportStatus.choices, default=ReportStatus.OPEN)
    created_at = models.DateTimeField("zgłoszone", default=timezone.now)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="translation_reports_closed",
        verbose_name="zamknął",
    )
    closed_at = models.DateTimeField("zamknięte", null=True, blank=True)

    class Meta:
        verbose_name = "zgłoszenie tłumaczenia"
        verbose_name_plural = "zgłoszenia tłumaczeń"
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=["language", "status"], name="translation_review_report_idx")]

    def __str__(self) -> str:
        return f"{self.language} #{self.pk} ({self.status})"
