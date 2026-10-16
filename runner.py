"""Führt eine Board-Aufgabe in einem eigenen Python-Prozess aus (Aufruf durch tasks.py)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tasks  # noqa: E402


def main():
    name, board_file, cfg = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    try:
        result = tasks.TASKS[name](board_file, cfg, print)
    except tasks.FidError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    if result:
        print("RESULT:" + result)


main()
