"""Impressum und Datenschutzerklaerung (seit 0.52.0), oeffentlich ohne Anmeldung.

Die Angaben zum Verantwortlichen stehen in rechtliches.json. Solange dort Platzhalter in [ECKIGEN KLAMMERN] stehen,
bricht werkzeuge/check_rechtliches.py den Prod-Deploy ab. Der Text beschreibt, was der Code tatsaechlich tut –
aendert sich eine Frist oder ein Dienst, hier mitziehen (tests/test_rechtliches.py prueft die Fristen gegen den Code).
"""
import json
import os
import re
from html import escape as e

HERE = os.path.dirname(os.path.abspath(__file__))
PLATZHALTER = re.compile(r"\[[A-ZÄÖÜ ]+\]")


def kontakt():
    with open(os.path.join(HERE, "rechtliches.json"), encoding="utf-8") as f:
        return json.load(f)


def offen(k=None):
    """Felder, in denen noch ein Platzhalter steht."""
    k = k or kontakt()
    return [f for f in ("name", "anschrift", "mail") if PLATZHALTER.search(json.dumps(k.get(f), ensure_ascii=False))]


def _stand(k):
    y, m, d = k.get("stand", "").split("-")
    return f"{d}.{m}.{y}"


def _verdeckt(s):
    """Jedes Zeichen als HTML-Entity: der Browser zeigt die Adresse normal, einfache Adress-Sammler finden kein a@b.c."""
    return "".join(f"&#{ord(c)};" for c in s)


def _mail(addr):
    """Mail-Link gegen Adress-Sammler verschleiert (seit 0.52.0), fuer Besucher normal anklickbar und kopierbar."""
    return f'<a href="{_verdeckt("mailto:" + addr)}">{_verdeckt(addr)}</a>'


def _wer(k):
    adr = "<br>".join(e(z) for z in k["anschrift"])
    return f'<p>{e(k["name"])}<br>{adr}<br>Mail: {_mail(k["mail"])}</p>'


def impressum(rig):
    k = kontakt()
    return (
        '<div class="legal">'
        "<h2>Impressum</h2>"
        "<h3>Angaben gemäß § 5 DDG</h3>" + _wer(k) +
        "<h3>Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV</h3>"
        f"<p>{e(k['name'])}, Anschrift wie oben.</p>"
        "<h3>Hinweis</h3>"
        f"<p>{'RigAnalyzer' if rig else 'RideAnalyzer'} ist ein privates Hobbyprojekt in der Alpha-Phase, kostenlos und ohne Werbung. "
        "Alle Angaben ohne Gewähr – prüft vor der Fahrt selbst, ob Strecke und Beschilderung passen.</p>"
        "<p>Kartendaten © OpenStreetMap-Mitwirkende (ODbL).</p>"
        '<p class="lg-foot"><a href="/datenschutz">Datenschutz</a> · <a href="/login">Zur Anmeldung</a></p>'
        "</div>")


def datenschutz(rig):
    k = kontakt()
    name = "RigAnalyzer" if rig else "RideAnalyzer"
    s = []

    def h(t):
        s.append(f"<h3>{t}</h3>")

    def p(t):
        s.append(f"<p>{t}</p>")

    def ul(*items):
        s.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")

    s.append(f"<h2>Datenschutz</h2><p class=\"legal-stand\">Stand {_stand(k)}</p>")
    p(f"{name} ist ein privates Hobbyprojekt. Wir sammeln nur, was für den Betrieb nötig ist: keine Werbung, "
      "keine Tracker, kein Verkauf von Daten.")

    h("1. Wer ist verantwortlich?")
    s.append(_wer(k))

    h("2. Wo laufen die Daten?")
    p("Auf einem eigenen Server in Deutschland, den wir selbst betreiben. Die Daten dieser App liegen getrennt von "
      "anderen Anwendungen.")

    h("3. Beim Aufruf der Seite")
    p("Jeder Aufruf landet im Server-Protokoll: IP-Adresse, Zeit, aufgerufene Adresse, Antwort und Browser-Kennung. "
      "Das brauchen wir für Sicherheit und Fehlersuche (Art. 6 Abs. 1 lit. f DSGVO). Das Protokoll wird laufend "
      "überschrieben und ist in der Regel nach wenigen Tagen weg.")
    p("Adressen, die Passwörter durchprobieren oder nach Sicherheitslücken suchen, sperren wir automatisch. "
      "Diese Sperrliste mit der IP-Adresse halten wir bis zu 400 Tage.")

    h("4. Zugang beantragen und anmelden")
    p("Für den Zugang speichern wir Benutzernamen, Mailadresse, das Passwort nur als nicht umkehrbaren Hash und "
      "eure Notiz zum Antrag. Bei Admins kommt der Schlüssel für den zweiten Faktor dazu. Über einen neuen Antrag "
      "bekommen die Admins eine Mail mit Benutzernamen, Mailadresse, Notiz und IP-Adresse. "
      "Grundlage ist die Nutzung der App (Art. 6 Abs. 1 lit. b DSGVO). Die Daten bleiben, bis der Zugang gelöscht wird.")

    h("5. Was ihr in der App macht")
    ul("<b>Gespeichertes</b> – " + ("Touren, Fahrzeug-Profile, Bewertungen und Freigaben an andere Logins"
                                     if rig else "Touren, Profile und Bewertungen") +
       " – bleibt, bis ihr es löscht. Frühere Stände einer Tour heben wir als Versionen mit auf.",
       "<b>Hochgeladene GPX ohne „Speichern“</b> werden im Arbeitsspeicher analysiert. Das Ergebnis liegt bis zu "
       "7 Tage ohne Bezug zu eurem Login im Zwischenspeicher, damit dieselbe Datei nicht neu gerechnet werden muss.",
       "<b>Einstellungen</b> (z. B. Kartenansicht, Benachrichtigungen) hängen an eurem Login.")
    p("Grundlage ist die Nutzung der App (Art. 6 Abs. 1 lit. b DSGVO).")

    h("6. Protokolle zur Nutzung")
    ul("<b>Anmeldungen</b> (Zeit, IP-Adresse, Gerät, Erfolg): die letzten 20, höchstens 30 Tage. Ihr seht sie unter "
       "Mein Konto → Meine Daten, die Admins im Admin-Bereich.",
       "<b>Gerade laufende Tätigkeit</b> (welche Seite, welches Gerät): sehen die Admins, solange ihr aktiv seid.",
       "<b>Ereignisprotokoll</b> für Tagesbericht und Fehlersuche, 90 Tage: Anmeldungen mit IP-Adresse, Analysen mit Dateiname "
       "und Länge, Planungen" + (", Navi-Ziele aus RigNavi" if rig else "") + " und Bewertungen.",
       "<b>Zahlen zur Nutzung</b> je Login und Monat (Analysen, Planungen, Datenmenge, Speicher) – ohne Ziele und "
       "Strecken. So sehen wir, was der Betrieb kostet.")
    p("Grundlage ist unser Interesse an einem sicheren, bezahlbaren Betrieb (Art. 6 Abs. 1 lit. f DSGVO).")

    if rig:
        h("7. Standort aus RigNavi")
        p("Nur wenn ihr in RigNavi zustimmt: Die App meldet ihre Position auf etwa 1 km gerundet. Wir halten nur den "
          "letzten Punkt, nach 24 Stunden ist er weg. Die Zustimmung lässt sich in der App jederzeit zurücknehmen "
          "(Art. 6 Abs. 1 lit. a DSGVO).")
    n = 8 if rig else 7

    h(f"{n}. Mails")
    p("Mails (Zugang, Passwort vergessen, Hinweise) verschicken wir über den Mail-Dienst Gandi SAS (Frankreich), "
      "der sie in unserem Auftrag zustellt. Neue Versionen und Newsletter bekommt ihr nur, wenn ihr das wollt "
      "(Art. 6 Abs. 1 lit. a DSGVO); jede dieser Mails hat einen Link zum Abbestellen.")

    h(f"{n + 1}. Rückmeldungen")
    p("Was ihr über „Rückmeldung“ schickt, speichern wir mit eurem Login und der Seite, bis es erledigt ist. "
      "Wir übernehmen es in unser eigenes Ticket-System. Beim Umsetzen hilft uns Claude, eine KI von Anthropic PBC "
      "(USA). Claude kann dabei den Text der Rückmeldung und euren Benutzernamen lesen – nicht eure Mailadresse. "
      "Anthropic nutzt diese Daten nicht zum Training. Grundlage ist Art. 6 Abs. 1 lit. f DSGVO; für die "
      "Übermittlung in die USA gelten Standardvertragsklauseln der EU-Kommission.")

    h(f"{n + 2}. Hinweise an uns per Telegram")
    p("Über neue Rückmeldungen und Anmeldungen informieren wir uns selbst im Messenger Telegram (Telegram FZ-LLC, "
      "Vereinigte Arabische Emirate). Darin stehen Benutzername, Zeit und Gerät, bei Rückmeldungen auch ihr Text "
      "und die Antwortadresse – keine IP-Adressen. Grundlage ist Art. 6 Abs. 1 lit. f DSGVO.")

    h(f"{n + 3}. Dienste, die euer Browser direkt lädt")
    p("Einige Karten und Bilder lädt euer Browser" + (" bzw. RigNavi" if rig else "") + " direkt von anderen Anbietern. Die sehen dabei eure IP-Adresse "
      "und den gezeigten Kartenausschnitt:")
    ul("<b>OpenStreetMap Foundation</b> (Großbritannien) – Kartenkacheln",
       "<b>RainViewer</b> – Regenradar, nur wenn ihr es einschaltet",
       *(("<b>OpenFreeMap</b> – die Karte in RigNavi, solange kein Reisepaket geladen ist",) if rig else ()))
    p("Grundlage ist unser Interesse an einer brauchbaren Karte (Art. 6 Abs. 1 lit. f DSGVO).")

    h(f"{n + 4}. Dienste, die unser Server fragt")
    p("Für Ortssuche, Kartendaten, Wetter, Verkehrsmeldungen und Spritpreise fragt unser Server öffentliche Dienste "
      "(z. B. OpenStreetMap, Deutscher Wetterdienst, Tankerkönig). Auch das Luftbild in der Planer-Karte (Esri, USA)"
      + (" und die Fotos von Einfahrten (Mapillary, Meta Platforms, USA)" if rig else "") +
      " holt unser Server und reicht es an euch weiter. Dabei gehen nur Suchbegriffe, Koordinaten und Kartenausschnitte "
      "hinaus – nicht eure IP-Adresse und nicht euer Login.")
    if rig:
        p("Für die Länge von Stellplätzen liest unser Server die Websites der Plätze und Luftbilder öffentlicher Stellen "
          "(z. B. IGN Frankreich, swisstopo) und misst sie selbst aus. Dabei gehen nur Adressen und Koordinaten der "
          "Plätze hinaus – nichts über euch, eure Route oder euer Login.")

    h(f"{n + 5}. Cookies und Speicher im Browser")
    p("Wir setzen nur technisch nötige Cookies: die Anmeldung (bis zum Schließen des Browsers oder 30 Tage, "
      "wenn ihr „Angemeldet bleiben“ wählt), einen Schutz gegen gefälschte Anfragen und bei Admins kurzzeitig den "
      "zweiten Faktor. Einstellungen wie Design, Ansicht und Entwürfe liegen im Speicher eures Browsers und gehen "
      "nicht an uns (§ 25 Abs. 2 TDDDG).")

    h(f"{n + 6}. Eure Rechte")
    p("Ihr habt das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung, Datenübertragbarkeit und Widerspruch "
      "(Art. 15–21 DSGVO). Eine Einwilligung könnt ihr jederzeit widerrufen.")
    ul("<b>Auskunft und Download</b>: Mein Konto → Meine Daten → Alles herunterladen (JSON).",
       "<b>Löschen</b>: Schreibt uns über „Rückmeldung“ oder per Mail, dann löschen wir den Login samt eurer Daten.",
       f'<b>Alles andere</b>: per Mail an {_mail(k["mail"])}.')
    p("Ihr könnt euch außerdem bei einer Datenschutz-Aufsichtsbehörde beschweren, etwa der an eurem Wohnort.")
    p("Es gibt keine automatisierten Entscheidungen über euch.")

    s.append('<p class="lg-foot"><a href="/impressum">Impressum</a> · <a href="/login">Zur Anmeldung</a></p>')
    return '<div class="legal">' + "".join(s) + "</div>"
