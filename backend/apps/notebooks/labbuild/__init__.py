"""Budowa JupyterLite dla notatników kwantowych (QC-01 § 3.1) – bez Django, uruchamiana w obrazie.

``python -m apps.notebooks.labbuild --out /opt/notebook-lab`` (etap ``notebook-lab`` w
``backend/Dockerfile``) albo ``scripts/build_notebook_lab.sh`` w dev. Wszystko, co pobierane, ma
przypiętą wersję i skrót SHA-256: narzędzia z PyPI (``requirements.txt``, ``--require-hashes``),
rdzeń Pyodide z wydania GitHuba (``pyodide.json``), koła pakietów Pyodide ze skrótami z ich własnego
``pyodide-lock.json``. W przeglądarce nic nie przychodzi spoza naszego serwera.
"""
