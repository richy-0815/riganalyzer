#!/usr/bin/env python3
"""Sperre vor dem Prod-Deploy (seit 0.52.0): Impressum/Datenschutz duerfen nicht mit Platzhaltern live gehen.

Prueft app/rechtliches.json auf [PLATZHALTER]. Exit 1 mit Hinweis, sonst 0. Laeuft in .forgejo/workflows/ci.yml
vor „Produktion ausrollen“; QA darf mit Platzhaltern laufen.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import rechtliches


def main():
    offen = rechtliches.offen()
    if offen:
        print(f"::error::app/rechtliches.json enthaelt noch Platzhalter ({', '.join(offen)}) – "
              "Name, Anschrift und Mail fuer Impressum/Datenschutz eintragen, dann erneut pushen.")
        return 1
    print("rechtliches.json: vollstaendig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
