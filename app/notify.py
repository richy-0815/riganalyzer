"""Waehlbare Mails (seit 0.50.0, Ticket #16): „Neue Versionen“ (taegliche Sammelmail) und Newsletter.

Je App (Ride/Rig laufen in getrennten Containern mit eigenen Logins) stellt jeder unter Mein Konto ein, was er
bekommen will – gespeichert in prefs.py, Bereich „mail“: {releases, newsletter, asked, at}. Standard: alles aus.
Jede dieser Mails traegt einen Abmeldelink (ohne Login, HMAC-Token) und List-Unsubscribe-Kopfzeilen (One-Click).
Pflicht-Mails (Registrierung, Passwort, Mailwechsel) laufen nicht hierueber und sind nicht abbestellbar.
"""

import base64
import datetime as dt
import hashlib
import hmac
import html as _html
import json
import logging
import os
import re
import threading
import time

import auth
import mailer
import prefs

log = logging.getLogger("notify")

KINDS = {"releases": "Neue Versionen", "newsletter": "Newsletter"}
DIGEST_HOUR = 19         # Sammelmail abends ab 19 Uhr, einmal am Tag
QUIET = "Kleinere Verbesserungen im Hintergrund"   # Wartungsversionen: nichts zu erzaehlen
SUBJECT_MAX, TEXT_MAX = 120, 20000
_lock = threading.Lock()


class Invalid(ValueError):
    """Abmeldelink ungueltig."""


class NewsError(ValueError):
    """Newsletter-Eingabe ungueltig (Text fuer die Seite)."""


def _data(name):
    return os.path.join(os.environ.get("DATA_DIR", "/data"), name)


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- Einstellungen
def settings(user):
    m = prefs.get(user).get("mail") or {}
    return {k: m.get(k) is True for k in (*KINDS, "asked")}


def wants(user, kind):
    return settings(user)[kind]


def set_kind(user, kind, on):
    """Eine Mailart an/aus (Abmeldelink); gilt als beantwortet (asked)."""
    cur = settings(user)
    cur[kind] = bool(on)
    cur["asked"] = True
    prefs.put(user, {"mail": {**cur, "at": int(time.time() * 1000)}})


def recipients(app, kind):
    """[(login, mail)] dieser App mit Mailadresse, nicht gesperrt/wartend, mit Haekchen bei kind."""
    out = []
    for name, u in sorted(auth.users().items()):
        if app not in (u.get("apps") or []) or not u.get("mail") or u.get("pending") or u.get("locked"):
            continue
        if wants(name, kind):
            out.append((name, u["mail"]))
    return out


# ---------------------------------------------------------------- Abmeldelink
def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def _sig(payload):
    return hmac.new(auth._key(), ("unsub|" + payload).encode(), hashlib.sha256).hexdigest()[:32]


def token(user, kind, app):
    payload = f"{user}|{kind}|{app}"
    return f"{_b64(payload)}.{_sig(payload)}"


def check(tok, app):
    """-> (login, kind); Invalid bei falschem/fremdem Token."""
    try:
        enc, sig = str(tok).split(".", 1)
        payload = _unb64(enc)
        user, kind, t_app = payload.split("|")
    except (ValueError, UnicodeDecodeError):
        raise Invalid("Link ungültig.") from None
    if not hmac.compare_digest(sig, _sig(payload)) or t_app != app or kind not in KINDS or user not in auth.users():
        raise Invalid("Link ungültig.")
    return user, kind


def _why(kind):
    return f"Du bekommst diese Mail, weil unter „Mein Konto“ „{KINDS[kind]}“ eingeschaltet ist."


def send(user, to, kind, subject, body, app, base_url, html_body=None):
    """Optionale Mail mit Fusszeile (warum, abbestellen) und One-Click-Kopfzeilen. True = angenommen.
    html_body (Newsletter): fertiges HTML des Inhalts – kommt mit Fusszeile in die HTML-Fassung."""
    link = f"{base_url}/abbestellen?t={token(user, kind, app)}"
    body = (body.rstrip() + f"\n\n-- \n{_why(kind)}\n"
            f"Abbestellen (ohne Anmeldung): {link}\nEinstellungen: {base_url}/konto\n")
    html = mail_html(app, subject, html_body, kind, link, base_url) if html_body is not None else None
    return mailer.send(to, subject, body, app, sender=mailer.NEWS_FROM, html=html,
                       headers={"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})


# ---------------------------------------------------------------- Newsletter-Text: Markdown-light -> HTML
_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"<b>\1</b>"),
    (re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])"), r"<i>\1</i>"),
]
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"(?<![\"=>])(https?://[^\s<]+[^\s<.,;:!?)])")


def _inline(text):
    t = _html.escape(text, quote=True)
    for rx, rep_ in _INLINE:
        t = rx.sub(rep_, t)
    links = []

    def keep(m):
        links.append(f'<a href="{m.group(2)}" style="color:#2f6fdf">{m.group(1)}</a>')
        return f"\x00{len(links) - 1}\x00"
    t = _LINK.sub(keep, t)
    t = _URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:#2f6fdf">{m.group(1)}</a>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], t)


def render(md):
    """Kleines, sicheres Markdown: # / ## Ueberschrift, - Liste, **fett**, *kursiv*, [Text](https://…), nackte Links.
    Alles andere wird als Text maskiert – kein eigenes HTML im Newsletter."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            items.clear()
    for line in str(md).replace("\r\n", "\n").split("\n"):
        st = line.strip()
        if not st:
            flush()
        elif st.startswith("## "):
            flush()
            out.append(f'<h3 style="margin:18px 0 6px">{_inline(st[3:])}</h3>')
        elif st.startswith("# "):
            flush()
            out.append(f'<h2 style="margin:20px 0 8px">{_inline(st[2:])}</h2>')
        elif st[:2] in ("- ", "* "):
            if para:
                flush()
            items.append(st[2:])
        else:
            if items:
                flush()
            para.append(st)
    flush()
    return "\n".join(out)


def mail_html(app, subject, inner, kind, link, base_url):
    name = _html.escape(mailer.APP_NAME.get(app, "RigAnalyzer"))
    return (f'<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{_html.escape(subject)}</title></head>'
            '<body style="margin:0;background:#f3f4f6;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2933">'
            '<div style="max-width:620px;margin:0 auto;padding:20px">'
            f'<div style="font-weight:700;font-size:18px;margin-bottom:12px">{name}</div>'
            '<div style="background:#fff;border-radius:12px;padding:18px 22px;line-height:1.5;font-size:15px">'
            f'{inner}</div>'
            f'<p style="font-size:12px;color:#6b7280;margin:14px 4px">{_html.escape(_why(kind))}<br>'
            f'<a href="{_html.escape(link)}" style="color:#6b7280">Abbestellen</a> · '
            f'<a href="{_html.escape(base_url)}/konto" style="color:#6b7280">Einstellungen</a></p>'
            '</div></body></html>')


# ---------------------------------------------------------------- Neue Versionen: taegliche Sammelmail
def _ver(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def release_items(releases, app, after):
    """Oeffentliche Versionen dieser App nach `after`, aelteste zuerst: [(version, datum, text)]."""
    out = []
    for r in releases:
        if not r.get("public") or _ver(r.get("version")) <= _ver(after) or r.get("app", "beide") not in (app, "beide"):
            continue
        text = (r.get(f"public_text_{app}") or r.get("public_text") or "").strip()
        if text and not text.startswith(QUIET):
            out.append((r["version"], r.get("date", ""), text))
    return sorted(out, key=lambda x: _ver(x[0]))


def digest_mail(app, items):
    name = mailer.APP_NAME[app]
    lines = [f"Neu im {name}:", ""]
    for v, d, text in items:
        when = f"{d[8:10]}.{d[5:7]}." if len(d) == 10 else ""
        lines += [f"• {v}{f' ({when})' if when else ''}: {text}", ""]
    subject = f"{name}: {'eine Neuerung' if len(items) == 1 else f'{len(items)} Neuerungen'}"
    return subject, "\n".join(lines)


def digest_tick(app, now, releases, version, base_url):
    """Einmal je Tag ab 19 Uhr: Sammelmail ueber alle oeffentlichen Versionen seit der letzten. -> Zahl der Mails.
    Erster Lauf merkt sich nur den Stand (keine Mail ueber die ganze Geschichte)."""
    path = _data(f"mail-digest-{app}.json")
    with _lock:
        st = _load(path, {})
        today = now.strftime("%Y-%m-%d")
        if "last" not in st:
            _save(path, {"last": version, "day": today})
            return 0
        if now.hour < DIGEST_HOUR or st.get("day") == today:
            return 0
        items = release_items(releases, app, st["last"])
        n = 0
        if items:
            subject, body = digest_mail(app, items)
            for user, to in recipients(app, "releases"):
                n += bool(send(user, to, "releases", subject, body, app, base_url))
        newest = max([version, st["last"]] + [v for v, _, _ in items], key=_ver)
        _save(path, {"last": newest, "day": today, "sent": n, "items": len(items)})
    if items:
        log.info("Sammelmail %s: %d Neuerungen an %d Empfänger", app, len(items), n)
    return n


def digest_loop(app, releases_fn, version, base_url):
    """Thread: alle 5 Minuten nachsehen (die Mail selbst geht hoechstens einmal am Tag)."""
    while True:
        try:
            digest_tick(app, dt.datetime.now(), releases_fn(), version, base_url)
        except Exception:   # darf die App nie stoeren
            log.exception("Sammelmail")
        time.sleep(300)


# ---------------------------------------------------------------- Newsletter (Admin)
def newsletter(app, subject, text, base_url, by, test_to=None):
    """An alle mit Haekchen (oder nur test_to=(login, mail) als Probe). -> {"sent", "total"}."""
    subject, text = str(subject or "").strip(), str(text or "").strip()
    if not 3 <= len(subject) <= SUBJECT_MAX:
        raise NewsError(f"Betreff 3 bis {SUBJECT_MAX} Zeichen.")
    if not 10 <= len(text) <= TEXT_MAX:
        raise NewsError(f"Text 10 bis {TEXT_MAX} Zeichen.")
    to = [test_to] if test_to else recipients(app, "newsletter")
    inner = render(text)
    sent = sum(bool(send(u, m, "newsletter", subject, text, app, base_url, html_body=inner)) for u, m in to)
    if not test_to:
        with _lock:
            path = _data(f"newsletter-log-{app}.json")
            log_ = _load(path, [])
            log_.insert(0, {"at": int(time.time()), "by": by, "subject": subject, "text": text, "total": len(to), "sent": sent})
            _save(path, log_[:50])
    return {"sent": sent, "total": len(to)}


def newsletter_log(app):
    return _load(_data(f"newsletter-log-{app}.json"), [])


def preview(app, subject, text, base_url):
    """Vorschau wie verschickt (HTML mit Fusszeile, Beispiel-Abmeldelink)."""
    inner = render(text)
    return {"html": mail_html(app, subject or "(ohne Betreff)", inner, "newsletter", f"{base_url}/abbestellen?t=…", base_url),
            "recipients": len(recipients(app, "newsletter"))}


def draft(app):
    return _load(_data(f"newsletter-draft-{app}.json"), {"subject": "", "text": "", "at": 0})


def save_draft(app, subject, text, by):
    subject, text = str(subject or "")[:SUBJECT_MAX], str(text or "")[:TEXT_MAX]
    d = {"subject": subject, "text": text, "by": by, "at": int(time.time())}
    with _lock:
        _save(_data(f"newsletter-draft-{app}.json"), d)
    return d


def clear_draft(app):
    try:
        os.remove(_data(f"newsletter-draft-{app}.json"))
    except FileNotFoundError:
        pass
