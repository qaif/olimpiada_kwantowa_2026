"""Logowanie dwuskładnikowe personelu (SEC-01): polityka per konkurs, okres przejściowy, odzyskiwanie.

Sam protokół (TOTP, kody zapasowe, poczekalnia sesji, token API) mieszka od v0.19.0
w ``apps.accounts.twofactor`` i tam zostaje. Ta aplikacja dokłada to, czego tamten moduł nie wiedział:
**od kogo** drugi składnik jest wymagany w danym konkursie, **od kiedy** (okres przejściowy), jak
zapamiętać zaufane urządzenie i kogo zawiadomić. Docs: ``docs/tasks/SEC-01.md``.
"""
