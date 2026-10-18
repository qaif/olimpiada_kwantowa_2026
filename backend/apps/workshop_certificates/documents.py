"""Szablon graficzny wybrany w regule – wpięty w ``render_pdf`` przez rejestr składów wyników.

Rejestr (``apps.results.certificates.register_composer``), a nie warunek w ``render_pdf``: wyniki nie
mają wiedzieć o tej aplikacji, a dokument bez wybranego szablonu przechodzi dokładnie tą samą drogą,
co przed WS-CERT-02 (``resolve_template``). Skład jest ten sam (``compose_pdf``) – zmienia się tylko
to, **który** szablon dostaje.

Wybór obowiązuje każde zaświadczenie z warsztatów edycji, także wystawione ręcznie: to jest jeden
i ten sam dokument (jeden na osobę i edycję), więc dwa wyglądy zależne od tego, kto kliknął, byłyby
błędem. Szablon wyłączony po wyborze nie obowiązuje – wraca zwykłe dopasowanie.
"""

from __future__ import annotations

from apps.results.models import Certificate, CertificateKind

_CACHE_ATTR = "_workshop_policy_template"


def _chosen_template(certificate: Certificate):
    """Aktywny szablon wskazany w regule edycji dokumentu albo ``None``. Jedno zapytanie, raz na obiekt."""
    if _CACHE_ATTR not in certificate.__dict__:
        from .models import AutoIssuePolicy

        policy = (
            AutoIssuePolicy.objects.filter(
                edition_id=certificate.edition_id,
                template__isnull=False,
                template__is_active=True,
                # Szablon z cudzego konkursu nie może trafić na dokument nawet z bazy zapisanej z boku.
                template__competition_id=certificate.edition.competition_id,
            )
            .select_related("template")
            .first()
        )
        certificate.__dict__[_CACHE_ATTR] = policy.template if policy is not None else None
    return certificate.__dict__[_CACHE_ATTR]


def handles(certificate: Certificate) -> bool:
    """Zaświadczenie z warsztatów z edycji, której reguła wskazuje aktywny szablon."""
    if certificate.kind != CertificateKind.WARSZTATY or certificate.entry_id is None:
        return False
    return _chosen_template(certificate) is not None


def compose(certificate: Certificate) -> bytes:
    from apps.results.certificates import certificate_content, compose_pdf

    return compose_pdf(certificate_content(certificate), _chosen_template(certificate))
