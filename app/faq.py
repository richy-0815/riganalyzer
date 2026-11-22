"""Häufige Fragen (seit 0.37.0): faq/*.md -> /faq.html, je App gefiltert.

Quelle ist der Ordner faq/ im Repo (Front Matter zielgruppe/app/zuletzt_geprueft). Beim Bau des Images wird er
nach app/faq/ kopiert (Dockerfile bzw. Rolle).
Ordner: Umgebungsvariable FAQ_DIR, sonst app/faq/ (Container), sonst faq/ im Repo (lokal, Tests).

Gezeigt wird eine Datei, wenn zielgruppe „öffentlich“ ist und app passt: Ride sieht ride+beide, Rig rig+beide.
Reihenfolge = Dateiname (01-…, 02-…). „# Titel“ wird zur Karte, „## Frage“ zur Frage im Inhaltsverzeichnis.
{{name}} wird zum Namen der App.

Sicherheit (Defense in depth, auch wenn der Inhalt aus dem eigenen Repo kommt): rohes HTML im Markdown wird nie
durchgereicht, sondern als Text escaped; Links nur http(s)/mailto/relativ, Attribute nur aus einer Positivliste.
"""
import logging
import os
import re

import markdown
from markdown.extensions import Extension
from markdown.extensions.toc import TocExtension, slugify_unicode
from markdown.treeprocessors import Treeprocessor

log = logging.getLogger("rideanalyzer.faq")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_FAQ = os.path.normpath(os.path.join(HERE, "..", "faq"))
APPS = {"ride": ("ride", "beide"), "rig": ("rig", "beide")}


def faq_dir():
    env = os.environ.get("FAQ_DIR")
    if env:
        return env
    local = os.path.join(HERE, "faq")
    return local if os.path.isdir(local) else REPO_FAQ


# ---------------------------------------------------------------- Front Matter
def front_matter(text):
    """(dict, Rest) – nur einfache „feld: wert“-Zeilen (mehr braucht die FAQ nicht); ohne Kopf: (None, text)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, text
    data = {}
    for ln in lines[1:end]:
        if ":" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split(":", 1)
            data[k.strip()] = v.strip().strip("\"'")
    return data, "\n".join(lines[end + 1:])


# ---------------------------------------------------------------- Bereinigen
_SAFE_URL = re.compile(r"^(?:https?://|mailto:|/(?![/\\])|#)", re.I)   # /\host gilt im Browser wie //host
_ALIGN = re.compile(r"^text-align: (?:left|right|center);$")
_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "a", "b", "strong", "i", "em", "code", "pre",
         "blockquote", "table", "thead", "tbody", "tr", "th", "td", "br", "hr", "div", "span", "del", "sup", "sub"}
_ATTRS = {"a": {"href", "title"}, "th": {"style"}, "td": {"style"}, "ol": {"start"},
          **{f"h{i}": {"id"} for i in range(1, 7)}}


class _Sanitize(Treeprocessor):
    """Laeuft nach allen anderen Schritten: unbekannte Tags raus (Text bleibt), Attribute nur aus der Liste."""

    def run(self, root):
        for parent in list(root.iter()):
            for el in list(parent):
                if el.tag not in _TAGS:      # z. B. img: als Text „Bild“ stehen lassen, nie als Tag
                    idx = list(parent).index(el)
                    alt = el.get("alt") or ""
                    parent.remove(el)
                    txt = alt + (el.tail or "")
                    if idx:
                        prev = parent[idx - 1]
                        prev.tail = (prev.tail or "") + txt
                    else:
                        parent.text = (parent.text or "") + txt
                    continue
                allowed = _ATTRS.get(el.tag, set())
                for k in list(el.attrib):
                    v = el.attrib[k]
                    if k not in allowed or (k == "href" and not _SAFE_URL.match(v.strip())) \
                            or (k == "style" and not _ALIGN.match(v)):
                        del el.attrib[k]
        return None


class _NoRawHtml(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.deregister("html_block")     # Block-HTML -> normaler Absatz, Text wird escaped
        md.inlinePatterns.deregister("html")          # Inline-HTML -> Text wird escaped
        md.treeprocessors.register(_Sanitize(md), "faq_sanitize", 0)


def render(body, slug_prefix, name):
    """(html, toc_tokens) – Ueberschriften eine Ebene tiefer (# -> h2, ## -> h3), Anker mit Prefix je Datei."""
    md = markdown.Markdown(extensions=[
        "tables", "sane_lists", _NoRawHtml(),
        TocExtension(baselevel=2, slugify=lambda v, sep: f"{slug_prefix}-" + slugify_unicode(v, sep)),
    ])
    html = md.convert(body.replace("{{name}}", name))
    return html, md.toc_tokens


# ---------------------------------------------------------------- Laden je App
_cache = {}


def sections(app, name):
    """Liste von {id, title, html, questions: [(id, title)]} fuer app (ride|rig)."""
    d = faq_dir()
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    except OSError:
        log.warning("FAQ-Ordner %s fehlt", d)
        return []
    key = (d, app, name, tuple((f, os.path.getmtime(os.path.join(d, f))) for f in files))
    if _cache.get(app, (None,))[0] == key:
        return _cache[app][1]
    out = []
    for f in files:
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            meta, body = front_matter(fh.read())
        if not meta or meta.get("zielgruppe") != "öffentlich" or meta.get("app") not in ("ride", "rig", "beide"):
            log.warning("FAQ %s: Front Matter fehlt/ungueltig – nicht angezeigt", f)
            continue
        if meta["app"] not in APPS[app]:
            continue
        stem = re.sub(r"^\d+[-_]", "", f[:-3])
        html, toc = render(body, stem, name)
        top = toc[0] if toc and toc[0]["level"] == 2 else None
        out.append({"id": top["id"] if top else stem, "title": top["name"] if top else stem,
                    "html": html, "questions": [(q["id"], q["name"]) for q in (top["children"] if top else toc)]})
    _cache[app] = (key, out)
    return out
