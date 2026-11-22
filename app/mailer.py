"""Mails ueber den Postfix auf dem Server (seit v0.9.2): Passwort vergessen, Zugangsdaten.
Ohne Konto/TLS wie pricewatch – der Container liegt in einem festen Netz, das Postfix einliefern laesst."""

import logging
import os

import qapost
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger("mailer")

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")   # der Betrieb setzt SMTP_HOST immer ausdruecklich
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
MAIL_FROM = os.environ.get("MAIL_FROM", "alarm@example.org")
FROM_NAME = os.environ.get("MAIL_FROM_NAME", "RigAnalyzer")
# Absender der waehlbaren Mails (Sammelmail, Newsletter): noreply der gleichen Domain
NEWS_FROM = os.environ.get("MAIL_FROM_NEWS") or "noreply@" + MAIL_FROM.split("@")[-1]
APP_NAME = {"ride": "RideAnalyzer", "rig": "RigAnalyzer"}


def _smtp_send(msg):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
        s.send_message(msg)


def send(to, subject, body, app=None, sender=None, headers=None, html=None):
    """True, wenn Postfix die Mail angenommen hat; Fehler werden geloggt, nie geworfen.
    sender/headers (0.50.0): anderer Absender (noreply) und Zusatz-Kopfzeilen (List-Unsubscribe);
    html (0.50.0, Newsletter): zusaetzlich eine HTML-Fassung (multipart/alternative), body bleibt die Textfassung."""
    if os.environ.get("MAIL_OFF") == "1":   # QA: nie echte Mails verschicken
        # nur mit STAGE=qa ins QA-Postfach (/qa/postfach), sonst nur das Log
        kept = qapost.store(to, subject, body, app)
        log.info("Mail unterdrückt (MAIL_OFF) an %s: %s%s", to, subject, " – im QA-Postfach" if kept else "")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr((APP_NAME.get(app, FROM_NAME), sender or MAIL_FROM))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=MAIL_FROM.split("@")[-1])
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        _smtp_send(msg)
        log.info("Mail an %s: %s", to, subject)
        return True
    except (OSError, smtplib.SMTPException) as e:
        log.warning("Mail an %s fehlgeschlagen: %s", to, e)
        return False
