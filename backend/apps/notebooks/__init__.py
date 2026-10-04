"""Notatniki kwantowe w przeglądarce (JupyterLite) i automatyczne sprawdzanie zadań (QC-01).

Ten plik celowo nie importuje niczego: podpakiet ``runner`` uruchamia się w kontenerze piaskownicy
**bez Django** (``python -m apps.notebooks.runner.daemon``), więc import ``apps.notebooks`` nie
może pociągać ustawień ani modeli.
"""
