"""Wynik ostatniego sprawdzenia DNS domeny nadawcy (``manage.py check_mail_dns``, MAIL-01 § 4.1).

Model instalacji, nie konkursu: rekordy DNS należą do domeny, a ta sama domena może być nadawcą
kilku konkursów. Koordynator nie widzi tej tabeli – widzi wyłącznie zdanie o domenie **swojego**
nadawcy (``services.sender_warnings``).

Po co wiersz, a nie zapytanie DNS w żądaniu: pulpit koordynatora nie może czekać sekund na DNS
ani zależeć od tego, czy resolwer odpowiada. Ostrzeżenie czyta wynik zapisany przez operatora.
"""

from __future__ import annotations

from django.db import models


class SenderDomain(models.Model):
    domain = models.CharField("domena", max_length=253, unique=True)
    verified = models.BooleanField("zweryfikowana", default=False)
    checked_at = models.DateTimeField("ostatnie sprawdzenie")
    #: Ostatnie **udane** sprawdzenie. Zostaje po nieudanym – „kiedy jeszcze działało” to pierwsze
    #: pytanie, gdy listy nagle zaczynają trafiać do spamu.
    verified_at = models.DateTimeField("ostatnia udana weryfikacja", null=True, blank=True)
    report = models.JSONField("raport", default=dict, blank=True)

    class Meta:
        verbose_name = "domena nadawcy"
        verbose_name_plural = "domeny nadawców"
        ordering = ["domain"]

    def __str__(self) -> str:
        return f"{self.domain} ({'OK' if self.verified else 'niezweryfikowana'})"
