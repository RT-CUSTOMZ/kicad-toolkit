# RT Toolkit

KiCad-Plugin für den Makerspace-Ablauf (KiCad 9 und 10, pcbnew-Python-API). Fasst die früheren Einzelplugins
(Viaschablone, Fräs-/Bohrplotter, Belichter-Upload) in einem Dialog zusammen. Jede Funktion ist einzeln wählbar.

## Funktionen

| Bereich | Was passiert | Ausgabe |
|---|---|---|
| Schaltplan-PDF | Exportiert den Schaltplan per `kicad-cli`, optional schwarz-weiß | `<Platine>_Schaltplan.pdf` |
| Viaschablone | Vias, Außenkonturen von NPTH-Befestigungslöchern, Umriss und optional Passermarken, als rote 0,01-mm-Kontur für den Lasercutter | `Viaschablone/<Platine>-LaserStencil.svg` und `.gbr` |
| Fräs-/Bohrplotter | PDF (CuTop gespiegelt, CuBottom), Gerber (Cu, Edge.Cuts, Passermarken), Bohrdaten und Drill-Report | `LPKF/` |
| Platinenbelichter (UV) | Erzeugt Top-/Bottom-Gerber und lädt sie optional zum Belichter hoch | `UV-Belichter/` |
| Vorab-Prüfung | Prüft die Platine und erzeugt bei Problemen eine Risikokarte | `<Platine>_Risikokarte.pdf` |
| Standardwerte | Setzt Leiterbahnbreiten und Vias im offenen Board auf die Standardwerte | direkt im Board |

Alle Ausgaben liegen im Projektordner unter `RT-production/`. Einzelne Dateien (Schaltplan, Risikokarte) direkt dort, Bereiche mit mehreren Dateien in Unterordnern.

Der Fräs-/Bohrplotter benötigt genau 3 Passermarken (Footprints mit Referenz `FID...`).

## Vorab-Prüfung

Ein Klick auf "Platine prüfen" zeigt sechs Tests mit ✔ / ⚠ / ✘:

- **Größe:** passt die Platine ins Ätzbad (A4, 210 × 297 mm, auch gedreht)? Der LPKF wird nicht geprüft.
- **Passermarken:** genau 3 vorhanden? (immer ein Fehler, wenn nicht)
- **Breiten/Abstände:** über `kicad-cli` mit den Standardwerten als Regeln: Leiterbahnbreite, Via-Durchmesser und Bohrung, Restring, Abstand zwischen Kupferobjekten (Standard 0,3 mm), Kupfer zum Rand, Bohrungen, Kurzschlüsse.
- **Via-Abstände:** Rand-zu-Rand-Abstand aller Via-Paare, damit Drahtvias von Hand lötbar bleiben (Standard 0,6 mm).
- **Beidseitig löten:** Durchsteck-Pins (THT), die direkt Kupfer (Leiterbahn oder Zone) auf Top **und** Bottom haben. Ohne Galvanik müssen sie von beiden Seiten gelötet werden. Die Pins stehen in der Risikokarte und im Textfeld (die ersten 10).
- **Zugänglichkeit:** Durchsteck-Pins mit Kupfer auf der **Bauteilseite**, die unter dem Gehäuse liegen (Pad innerhalb des Fab-Umrisses des Footprints, z. B. Stiftleisten, Elkos, Relais, Spulen, Wannenstecker). Dort ist Löten schwer; besser die Bahn auf die Gegenseite legen. Die Prüfung ist eine Heuristik und kann Fehlalarme liefern, z. B. bei Bauteilen mit Beinen, die den Zugang erlauben (TO-220), oder bei Footprints ohne brauchbaren Fab-Umriss.

Gibt es Problemstellen, wird die **Risikokarte** als PDF erzeugt und geöffnet: auf A4-Breite skaliert, mit Leiterbahnen (vorne rot, hinten blau), Vias, Bauteilumrissen (betroffene Bauteile dunkelgrau mit Referenz, die übrigen hellgrau) und magenta markierten Problemstellen. Pin-Treffer eines Bauteils haben eine gemeinsame Nummer, die Legende nennt je Bauteil die Pins und die Art des Problems (bis 40 Einträge).

## Standardwerte

Vorbelegt (im Dialog änderbar):

| | VCC/GND (`GND\|VCC`) | Signal |
|---|---|---|
| Leiterbahn | 0,4 mm | 0,3 mm |
| Via-Bohrung | 0,8 mm | 0,6 mm |
| Via-Durchmesser | 1,4 mm | 1,2 mm |

"Standardwerte auf Platine anwenden" setzt alle Leiterbahnen und Durchgangs-Vias im **geöffneten** Board auf diese Werte. Die VCC/GND-Netze werden über ein Regex auf den Netznamen erkannt. Danach selbst prüfen und speichern, der Export nutzt die gespeicherte Datei.

## Belichter-Status

In der Belichter-Karte zeigt ein Punkt, ob der Server erreichbar ist (grün) oder nicht (rot, "eingeschaltet?"). Die Prüfung läuft beim Öffnen des Dialogs, sofern der Belichter aktiviert ist, und über "Status prüfen".

## Dialog

- Karten pro Bereich mit Haken, Optionen und Tooltips; Statusanzeige (läuft / ✔ OK / ✘ Fehler) pro Aufgabe
- Fortschrittsbalken und Textfeld für Meldungen und Fehler
- "Ausgabeordner öffnen"
- Auswahl und Einstellungen werden gespeichert

## Installation

Ordner `kicad_rt_toolkit` nach `Documents/KiCad/<9.0|10.0>/scripting/plugins/` kopieren und KiCad neu starten
(oder im PCB-Editor "Tools > External Plugins > Refresh Plugins").

Aufruf: PCB-Editor > Tools > External Plugins > "RT Toolkit".

## Hinweise

- Die Platine muss gespeichert sein; Exporte und Prüfungen verwenden die gespeicherte Datei.
- Alle Board-Aufgaben laufen in einem eigenen Python-Prozess auf einer temporären Kopie, das geöffnete Board bleibt unberührt (Ausnahme: "Standardwerte anwenden").
- Die Einstellungen liegen in `~/.kicad_rt_toolkit.json`.
- `kicad-cli` (Teil von KiCad) wird für Schaltplan-PDF und Breiten-/Abstandsprüfung benötigt.
- Meldungen wie `swig/python detected a memory leak` im KiCad-Terminal sind harmlos.

## Dateien

| Datei | Inhalt |
|---|---|
| `__init__.py` | Plugin-Registrierung |
| `dialog.py` | Oberfläche |
| `tasks.py` | Exporte, Vorab-Prüfung, Standardwerte, Statusabfrage |
| `runner.py` | Startet Aufgaben im eigenen Prozess |
| `common.py` | Einstellungen und Plot-Hilfen |
