"""Katalog badanych ekranów: identyfikator (klucz w baseline.json), rola, adres, przygotowanie.

Adres może zawierać pola z ``state/fixture.json`` (``{main[elim]}``, ``{iqo[letter_code]}``…) –
identyfikatorów i kodów nie da się znać z góry, bo powstają w e2e/a11y/seed.py.

Prefiks identyfikatora mówi, w którym konkursie i motywie stoi ekran: ``oki-`` – Olimpiada Kwantowa
(motyw klasyczny), ``iqo-`` – IQO (motyw IQO Quantum), ``iqo-ar-`` – IQO po arabsku (RTL), ``hc-`` –
tryb wysokiego kontrastu.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Screen:
    id: str
    path: str
    role: str = "anon"
    #: Czynność przed audytem: ``submit-empty`` (formularz z błędami), ``bad-login``, ``open-details``.
    action: str = ""
    cookies: dict = field(default_factory=dict)


SCREENS: tuple[Screen, ...] = (
    # --- Olimpiada Kwantowa, motyw klasyczny -------------------------------------------------------
    Screen("oki-home", "/"),
    Screen("oki-news", "/aktualnosci/"),
    Screen("oki-news-detail", "/aktualnosci/ruszyla-rejestracja/"),
    Screen("oki-problems", "/zadania/"),
    Screen("oki-results-table", "/results/{main[district]}/"),
    Screen("oki-statistics", "/statystyki/"),
    Screen("oki-documents", "/dokumenty/"),
    Screen("oki-statement", "/dokumenty/deklaracja-dostepnosci/"),
    Screen("oki-register", "/register/"),
    Screen("oki-register-errors", "/register/", action="submit-empty"),
    Screen("oki-login", "/login/"),
    Screen("oki-login-errors", "/login/", action="bad-login"),
    Screen("oki-2fa-verify", "/login/", action="twofa"),
    Screen("oki-2fa-verify-errors", "/login/", action="twofa-bad"),
    Screen("oki-password-reset", "/password-reset/"),
    Screen("oki-password-reset-errors", "/password-reset/", action="submit-empty"),
    Screen("oki-password-reset-confirm", "{reset_path}"),
    Screen("oki-me", "/me/", "participant"),
    Screen("oki-me-upload", "/me/?tab=zadania", "participant"),
    Screen("oki-me-results", "/me/?tab=wyniki", "participant"),
    Screen("oki-me-consents", "/me/?tab=zgody", "participant"),
    Screen("oki-password-change", "/account/password/", "participant"),
    Screen("oki-password-change-errors", "/account/password/", "participant", action="submit-empty"),
    Screen("oki-2fa-setup", "/account/2fa/", "participant"),
    Screen("oki-2fa-setup-errors", "/account/2fa/", "participant", action="submit-empty"),
    Screen("oki-2fa-codes", "/account/2fa/", "participant2", action="twofa-codes"),
    Screen("oki-quiz-start", "/me/stages/{main[training]}/test/", "participant"),
    Screen("oki-quiz-attempt", "/me/test/{main[quiz_attempt]}/", "participant"),
    Screen("oki-chat-inbox", "/me/messages/", "participant"),
    Screen("oki-chat-thread", "/me/messages/{main[conversation]}/", "participant"),
    Screen("oki-coordinator", "/coordinator/", "coordinator"),
    Screen("oki-coordinator-accounts", "/coordinator/accounts/", "coordinator"),
    Screen("oki-coordinator-participants", "/coordinator/accounts/?role=participant", "coordinator"),
    Screen("oki-coordinator-committee", "/coordinator/committee/", "coordinator"),
    Screen("oki-coordinator-assignments", "/coordinator/stages/{main[elim]}/assignments/", "coordinator"),
    Screen("oki-coordinator-results", "/coordinator/stages/{main[elim]}/results/", "coordinator"),
    Screen("oki-coordinator-quiz", "/coordinator/stages/{main[training]}/quiz/", "coordinator"),
    Screen("oki-coordinator-chat", "/coordinator/chat/", "coordinator"),
    Screen("oki-coordinator-chat-thread", "/coordinator/chat/{main[conversation]}/", "coordinator"),
    Screen("oki-reviewer", "/review/", "reviewer"),
    Screen("oki-reviewer-detail", "/review/{main[review]}/", "reviewer"),
    # --- IQO, motyw IQO Quantum --------------------------------------------------------------------
    Screen("iqo-home", "/iqo/"),
    Screen("iqo-news", "/iqo/aktualnosci/"),
    Screen("iqo-statement", "/iqo/dokumenty/deklaracja-dostepnosci/"),
    Screen("iqo-register", "/iqo/register/"),
    Screen("iqo-login", "/iqo/login/"),
    Screen("iqo-login-errors", "/iqo/login/", action="bad-login"),
    Screen("iqo-password-reset", "/iqo/password-reset/"),
    Screen("iqo-visa-verify", "/iqo/visa/verify/"),
    Screen("iqo-visa-verify-result", "/iqo/visa/verify/{iqo[letter_code]}/"),
    Screen("iqo-visa-verify-unknown", "/iqo/visa/verify/ZZZZ-ZZZZ-ZZZZ/"),
    Screen("iqo-leader", "/iqo/delegation/", "leader"),
    Screen("iqo-leader-add-student", "/iqo/delegation/students/add/", "leader"),
    Screen("iqo-leader-add-student-errors", "/iqo/delegation/students/add/", "leader", action="submit-empty"),
    Screen("iqo-leader-edit-student", "/iqo/delegation/students/{iqo[student_pk]}/edit/", "leader"),
    Screen("iqo-leader-logistics", "/iqo/delegation/logistics/", "leader"),
    Screen("iqo-leader-letters", "/iqo/delegation/logistics/letters/", "leader"),
    Screen("iqo-leader-member", "/iqo/delegation/logistics/members/{iqo[member_pk]}/", "leader"),
    Screen("iqo-student-me", "/iqo/me/", "student"),
    Screen("iqo-coordinator-delegations", "/iqo/coordinator/delegations/", "coordinator"),
    # --- IQO po arabsku (RTL) -----------------------------------------------------------------------
    Screen("iqo-ar-home", "/iqo/", cookies={"django_language": "ar"}),
    Screen("iqo-ar-login", "/iqo/login/", cookies={"django_language": "ar"}),
    Screen("iqo-ar-visa-verify", "/iqo/visa/verify/", cookies={"django_language": "ar"}),
    # --- Tryb wysokiego kontrastu ---------------------------------------------------------------------
    Screen("hc-oki-home", "/", action="high-contrast"),
    Screen("hc-oki-login", "/login/", action="high-contrast"),
    Screen("hc-iqo-home", "/iqo/", action="high-contrast"),
    Screen("hc-iqo-login", "/iqo/login/", action="high-contrast"),
)
