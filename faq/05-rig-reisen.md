---
zielgruppe: öffentlich
app: rig
zuletzt_geprueft: 2026-11-22
---
# Reisen, RigNavi & offline fahren

## Wie plane ich eine Reise mit Etappen?

Zwei Wege:

- **Routenplaner:** An den Übernachtungen „Nacht“ setzen.
- **GPX:** Mehrere Tagesetappen als eine GPX exportieren, je Tag ein Track. Viele Planer machen das von selbst.

Daraus wird ein Fahrplan mit Startdatum, Abfahrt, Ankunft und Nächten am Ziel. Jede Etappe wird einzeln geprüft.

## Kann ich eine fertige GPX-Tour mit meinem Fahrzeug neu planen?

Ja. So geht es:

1. GPX ablegen, zum Beispiel aus Garmin Drive oder TomTom MyDrive. Die Tour wird wie gewohnt geprüft.
2. Im Routenplaner den Import-Knopf antippen:
   - **📥 Eckdaten aus Import übernehmen**, wenn ihr noch nichts geplant habt.
   - **📥 Import in Planung einfügen**, wenn ihr schon eine eigene Planung habt. Euer Start und Ziel bleiben, die Punkte der Tour kommen an passender Stelle dazwischen. **alles ersetzen** nimmt stattdessen nur die Punkte der Tour.
   - **Punkte auswählen …** zeigt alle Punkte der Tour als Liste. Hakt nur an, was ihr wollt, dann **Ausgewählte einfügen**.
3. Die Punkte prüfen und bei Bedarf verschieben oder löschen. Passt es nicht, holt **↩ Rückgängig** die vorherigen Punkte zurück.
4. **Route planen**, danach **Analyse starten**.

| Was wird übernommen? | Woher |
|---|---|
| Start und Ziel | Anfang und Ende der Tour |
| Nacht-Punkte | Ende jeder Etappe, wenn die GPX mehrere Tagesetappen hat |
| Zwischenpunkte | Wegpunkte aus der GPX; fehlen sie, etwa alle 50 bis 100 km ein Ort an der Strecke, mit seinem Namen |
| Name | Name der Tour aus der GPX |

Höchstens 25 Punkte. Die importierte Tour bleibt gestrichelt auf der Karte. Unter **🗂 Ebenen** blendet ihr „Importierte Strecke“ und „Geplante Route“ einzeln aus und ein. Im Ergebnis steht ein Vergleich: Länge, Fahrzeit, bergauf und Engstellen, beides mit eurem Fahrzeug bewertet. **Alle löschen** im Planer zeigt wieder die importierte Tour.

## Kann ich an einem Zwischenpunkt Pause machen?

Ja. Neben jedem Zwischenpunkt steht ⏱. Dort wählt ihr, wie lange ihr bleibt – von 15 Minuten bis 6 Stunden. Der Fahrplan verschiebt dann Ankunft, Wetter und Tankzeiten, und der Übernachtungsvorschlag rechnet den Aufenthalt mit.

Fahrpausen stellt ihr im Fahrplan ein: geschätzt je nach Länge, 20 Minuten alle 2 Stunden oder ohne. Die Wahl wird mit der Tour gespeichert.

## Zeigt das Navi meine Aufenthalte?

Ja, ab RigNavi 0.23.0 (Android) und 0.5.0 (iPad). Am nächsten Zwischenziel steht, wann ihr ankommt und wann es weitergeht („📍 Moustiers an 11:40 · weiter 12:25“). Die Ankunft am Ziel rechnet die Aufenthalte und die Fahrpausen der Tour mit ein. Für Reisen, die ihr vorher geschickt habt, einmal neu **📱 Ans Navi schicken**.

## Meine Strecke ist länger als 1.000 km. Geht das?

Ja. Eine einzelne lange Strecke teilen wir automatisch in Tagesetappen – wo möglich an einem Ort. Oben im Fahrplan steht, wie geteilt wurde. Nächte am Etappenziel stellt ihr dort wie gewohnt ein.

## Kann ich mehrere Touren zu einer Reise verbinden?

Ja, auf zwei Wegen:

| Weg | So geht's |
|---|---|
| Aus mehreren GPX-Dateien | Erste Datei in den Planer holen, dann die nächste ablegen und **hinten anhängen** (oder **vorne anfügen**). |
| Aus gespeicherten Touren | Eine Tour öffnen, unter **📂 Meine Touren** bei der anderen **➕ Anhängen**. |

Wo die Teile zusammenstoßen, endet eine Etappe. Liegt das Ende der einen Tour nicht am Start der anderen, plant der Planer das Stück dazwischen. Insgesamt gehen bis zu 25 Punkte.

## Woher weiß RigAnalyzer, ob mein Gespann auf einen Stellplatz passt?

In den Kartendaten steht die erlaubte Länge fast nie. Deshalb prüft RigAnalyzer die Plätze an eurer Route selbst:

- **Beschreibung und Website** des Platzes: Steht dort z. B. „Wohnmobile bis 10 m“, ist die Länge **bestätigt**.
- **Luftbild**: Sonst misst RigAnalyzer im Luftbild nach: die längste freie Bahn auf dem Platz und die freie Fläche zum Wenden. Bäume und Schatten zählen nicht mit. Das ist **wahrscheinlich**, nicht sicher.
- **Zufahrt**: Längen- und Breitenlimits oder Engstellen auf den Straßen davor stehen mit dabei.

In der Liste heißt Hellgrün „passt wahrscheinlich“, Orange „wohl zu kurz“. Im Platz-Popup steht, woher die Angabe kommt, und ihr könnt das gemessene Luftbild ansehen. Neue Plätze brauchen beim ersten Mal ein paar Minuten. Der Filter **📏 Länge geprüft** zeigt nur Plätze, die laut Angabe oder Luftbild passen. Schilder vor Ort gehen immer vor.

## Kann ich zwischen mehreren Routen wählen?

Ja. Nach **Route planen** stehen unter der Planung bis zu drei **Varianten** – mit Länge, Fahrzeit und dem Unterschied zu eurer Planung. Auf der Karte sind sie grau gestrichelt.

| Variante | Wann |
|---|---|
| Alternative 1 / 2 | nur bei Start → Ziel ohne Zwischenpunkte, wenn es eine echte andere Strecke gibt |
| Ohne Autobahn / Mit Autobahn | immer – das Gegenteil eurer Autobahn-Einstellung |

Antippt eine Variante (in der Liste oder auf der Karte), dann wird sie übernommen und neu geplant. Bei einer Alternative steht danach ein Zwischenpunkt in der Liste – so bleibt genau diese Strecke beim Speichern und in RigNavi erhalten.

## Kann ich Maut oder Fähren meiden?

Ja. Nach **Route planen** stehen neben „Autobahn“ und „Umweltzonen meiden“ zwei Schalter:

| Schalter | Wirkung |
|---|---|
| 💶 Maut meiden | Mautstrecken so weit wie möglich umfahren (Vignetten-Länder je nach Kartendaten nicht immer erkannt) |
| ⛴ Fähren meiden | Fähren so weit wie möglich umfahren |

Sie gelten für die ganze Tour und werden mit ihr gespeichert. Nach dem Umschalten auf **🔄 Route neu planen** drücken. Geht es nicht anders, steht im Ergebnis zum Beispiel „Fähre ließ sich nicht ganz vermeiden“. Schickt ihr die Reise ans Navi, nimmt RigNavi eure Wahl mit. In RigNavi findet ihr dieselben Schalter unter ⚙.

Unbefestigte Wege meidet der Planer immer – dafür gibt es keinen Schalter.

## Wie rechnet ihr die Maut aus?

Im Block **Maut, Vignette & Tempo** steht je Land ein Betrag für euer Fahrzeugprofil, oben die Summe. Sie steht auch in der Kachel neben dem Diesel.

| Art | So rechnen wir |
|---|---|
| Vignette, Pauschale (Schweiz, Österreich) | amtlicher Preis, in der Schweiz auch für den Anhänger |
| Streckenmaut (Frankreich, Italien, Spanien, Portugal) | mautpflichtige km × Satz eurer Klasse – eine Schätzung („ca.“) |
| Tunnel und Brücken mit eigenem Tarif | Preis des Betreibers für eure Klasse, einfache Fahrt |

Erkannt werden zum Beispiel Viaduc de Millau, Mont-Blanc- und Fréjus-Tunnel, Grosser St. Bernhard sowie Brenner, Tauern, Arlberg, Karawanken und Pyhrn in Österreich. Bei Millau zählt der Sommerpreis (15.06.–15.09.), wenn die Etappe laut Fahrplan in den Sommer fällt. Ohne Startdatum nehmen wir den Sommerpreis.

Die Klasse kommt aus Höhe, Gewicht und Anhänger im Fahrzeugprofil. Zu jedem Tunnel steht ein Link auf den Tarif. Beträge ohne Gewähr – vor der Reise prüfen.

## Wie plane ich ohne Autobahn?

Im Routenplaner steht oben der Schalter **Autobahn**: grün „mit“, rot „ohne“ (Schild durchgestrichen). Er gilt für alle Abschnitte. Zwischen zwei Punkten sitzt außerdem je ein eigener Schalter für diesen Abschnitt. Weichen die Abschnitte voneinander ab, zeigt der große Schalter „gemischt“. Ist die Route schon geplant, zeigt der Knopf danach **🔄 Route neu planen** – neu gerechnet wird erst, wenn ihr ihn drückt. So könnt ihr mehrere Einstellungen ändern und dann einmal planen.

## Wo stelle ich Zoom und Klick-Zoom der Karte ein?

Über der Karte unter **⚙ Karte**: wie schnell das Mausrad zoomt, wie nah ein Klick auf einen Listeneintrag heranzoomt, und „Ergebnis-Kacheln zurücksetzen“ für die Standard-Anordnung. Die Zoom-Einstellungen gelten auch für RigNavi.

## Wo finde ich meine gespeicherten Touren?

Menü oben rechts → **👤 Mein Konto** → **📂 Meine Touren**. Dort seht ihr alle Touren mit Strecke, Etappen und dem Stand des Navi-Pakets. Von dort könnt ihr eine Tour im Planer öffnen, kopieren, frühere Stände holen oder löschen.

## Wie kommt die Reise aufs Handy?

1. **RigNavi installieren:** im Footer unter **📱 Android-App** – mit Schritt-für-Schritt-Anleitung. Nötig ist Android 10 oder neuer mit Google-Diensten. Angemeldet wird mit demselben Login wie hier.
2. **Schicken:** im Routenplaner **📱 Ans Navi schicken**. Das Reisepaket ist nach ein paar Minuten fertig.
3. **Laden:** in RigNavi oben auf 🧳 → **Meine Reisen** → **Laden**. Ein Paket ist je nach Strecke 1 bis 4 GB groß und lädt nur im WLAN (Häkchen „auch mobil“ für Ausnahmen). Bricht der Download ab, geht er an derselben Stelle weiter.
4. **Fahren:** **Etappe starten** setzt Ziel und Zwischenziele.

Für iPhone und iPad gibt es RigNavi nicht öffentlich. Der RigAnalyzer im Browser geht überall.

## Wie weit ist mein Reisepaket?

Nach **📱 Ans Navi schicken** zeigt ein Balken, wie weit der Bau ist. Darunter stehen die Schritte: Route, Straßendaten, Karte, Gespann-Prüfung, Abschluss. Erledigte haben ein Häkchen. Daneben seht ihr, wie lange es schon läuft und grob, wie lange noch. Dasselbe steht unter **👤 Mein Konto** → **📂 Meine Touren** und läuft dort von selbst mit.

Wartet das Paket, steht dabei, wie viele Pakete vor euch dran sind. Schlägt der Bau fehl, seht ihr den Grund und den Schritt. Mit **↻ Neu bauen** startet ihr ihn noch einmal.

## Geht das wirklich ohne Netz?

Ja, mit Reisepaket. Im Reisegebiet – rund 20 km um die Route – rechnet das Handy selbst, auch wenn kein Netz da ist. Unten links steht dann 📦, sonst ☁ (Server).

Verlasst ihr das Reisegebiet ohne Netz, bleibt die alte Route stehen. Folgt ihr zurück ins Reisegebiet, dann rechnet das Handy wieder selbst.

## Wie lösche ich Reisepakete?

| Wo | Was passiert |
|---|---|
| RigNavi → Meine Reisen | Reise vom Handy löschen – gibt den Speicher auf dem Handy frei. |
| RigNavi → „Server-Paket löschen“ | Die Kopie auf dem Server verschwindet. |
| Routenplaner oder „Mein Konto“ → Meine Touren | Paket einzeln vom Server löschen. |

Die Reise selbst bleibt gespeichert, neu schicken geht jederzeit. Während ein Paket gebaut wird, lässt es sich nicht löschen – kurz warten.

## Was speichert RigAnalyzer zusätzlich?

Neben Einstellungen und Vorlieben:

- eure **Fahrzeugprofile**,
- **gespeicherte Touren** mit früheren Ständen,
- **Navi-Pakete**, bis ihr sie löscht,
- eure **Platz-Bewertungen**.

Gezählt werden zusätzlich die geladene Datenmenge und der belegte Speicher – nur als Zahlen, ohne Ziele oder Strecken.

## Wer sieht, wo ich gerade bin?

Nur wer zustimmt. In RigNavi unter ⚙ könnt ihr **Position teilen** einschalten. Dann sieht der Admin eure ungefähre Position:

- auf etwa 1 km gerundet,
- während der Fahrt etwa alle 2 Minuten, sonst (App offen, ohne Ziel) etwa alle 5 Minuten,
- nach 24 Stunden gelöscht.

Ausschalten unter ⚙ nimmt den Punkt sofort weg.

## RigNavi sagt nichts an. Was tun?

Lautstärke-Knopf in der App prüfen. In den Android-Einstellungen unter „Text-in-Sprache“ Google als Sprachausgabe mit Deutsch wählen und die deutsche Stimme herunterladen. Mehr Hilfe steht auf der Seite **📱 Android-App** unter „Wenn etwas nicht klappt“.
