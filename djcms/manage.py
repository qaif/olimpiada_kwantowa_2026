#!/usr/bin/env python
import os
import sys


def main() -> None:
    # Domyślnie produkcja, jak w backendzie: dev i testy ustawiają moduł jawnie (compose, pytest).
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
