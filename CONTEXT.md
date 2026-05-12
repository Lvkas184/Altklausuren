# Altklausuren — Domain-Kontext

## Kontext

**Forum Wirtschaftsinformatik** ist die studentische Fachschaft am KIT für den Studiengang Wirtschaftsinformatik. Das **Referat Altklausuren** ist die zuständige Untergruppe, die Klausuren sammelt und pflegt.

In **Sprechstunden** geben Forum-Mitglieder physische Ausdrucke der Sammlungen an Studierende aus. Studierende selbst nutzen die App nicht — sie bekommen die PDFs ausgedruckt.

### Rollen

- **viewer** — Sprechstunden-Mitglieder: können Sammlungen ansehen, herunterladen und drucken, aber keine Einträge anlegen oder bearbeiten
- **editor** — Referat Altklausuren: pflegt Einträge (hochladen, bearbeiten, löschen)
- **admin** — voller Zugriff: zusätzlich Drive-Konfiguration, Importe, Konflikte lösen

Lokal (ohne `AUTH_ENABLED`) ist man immer Admin.

## Glossar

### Fach
Eine Lehrveranstaltung (z.B. "Mathematik I"), für die Altklausuren gesammelt werden. Hat einen Titel und einen optionalen Kürzel-Code. Enthält eine oder mehrere Einreichungen.

**Synonyme die vermieden werden sollten:** Modul (wird im Alltag verwendet, meint aber dasselbe).

### Deckblatt
Die erste Seite der Sammlung — wird automatisch neu generiert, sobald ein Eintrag hinzugefügt oder die Sammlung neu erzeugt wird. Enthält die Forum-WI-Kopfzeile, den Fachnamen und eine Tabelle aller Einträge (Prüfungsdatum, Dozent, Lösung), sortiert nach Datum (neueste zuerst).

Der Nutzer muss das Deckblatt nie manuell anfassen — es ist immer aktuell.

### Sammlung
Die druckfertige PDF eines Fachs — zusammengesetzt aus allen Einträgen plus Deckblatt. Im Dateisystem: `current.pdf`. Wenn jemand sagt "das Fach aktualisieren", meint er konkret: die Sammlung neu erzeugen.

**Nicht zu verwechseln mit:** dem Fach selbst (das Fach ist der Behälter, die Sammlung ist das aktuelle PDF-Ergebnis).

### Lösung
Gibt an, ob die hochgeladene PDF eine Musterlösung enthält. Die Lösung ist nicht separat — sie ist in derselben PDF wie die Klausur. Wird auf dem Deckblatt als Spalte angezeigt (Ja / Nein / unbekannt).

Interner Code-Name: `solution`.

### Protokoll-Session
Ein zeitlich begrenzter Sammelraum für Gedächtnisprotokolle zu einer bestimmten Klausur (Fach + Semester). Das Referat schickt nach einer Klausur einen Link per WhatsApp an die Studierenden.

**Ablauf:**
1. Referat öffnet eine Session für z.B. "BGB für Anfänger WiSe 26/27"
2. Link geht per WhatsApp an Studierende
3. Studierende tippen auf dem Handy direkt nach der Klausur, was sie noch wissen — der Text wird automatisch gespeichert (kein Absenden nötig, Tab kann einfach geschlossen werden)
4. Studierende können alle Beiträge der anderen lesen
5. Referat schließt die Session, wertet die Beiträge aus, formatiert sie und fügt das Ergebnis als Eintrag zur Sammlung hinzu

**Designentscheidungen:**
- Keine Accounts für Studierende
- Kein Löschen durch Studierende (sie kommen nach dem Tippen nicht mehr zurück)
- Auto-Speichern ist Pflicht — Studierende sind am Handy und schließen den Tab einfach
- Nur das Referat kann Beiträge löschen/moderieren

### Beitrag (einer Protokoll-Session)
Ein einzelner Freitext-Eintrag eines Studierenden in einer Protokoll-Session. Wird laufend auto-gespeichert. Kann von anderen Studierenden gelesen werden. Nur das Referat kann Beiträge löschen.

### Typ (eines Eintrags)
Die Herkunft einer hochgeladenen Klausur. Taucht nicht auf dem Deckblatt auf, nur intern.

- **Gedächtnisprotokoll** — von Studierenden aus der Erinnerung rekonstruiert (Standard)
- **Altklausur** — offiziell veröffentlicht und von dort heruntergeladen
- **Lösungsskizze** — wird in der Praxis nicht verwendet

Interner Code-Name: `kind`.

### Drive-Sync
Hält die App und den Google-Drive-Ordner des Forums synchron, damit man auch ohne App direkt über Drive drucken kann. Drive ist kein reines Backup — es ist ein gleichwertiger Zugriffspfad.

**Richtung App → Drive:** Nach jedem Upload wird die neue Sammlung automatisch nach Drive hochgeladen. Vorher wird die alte Drive-Datei in einen `Archiv`-Unterordner gesichert. Was hochgeladen wird: `single.pdf` wenn vorhanden, sonst `current.pdf`.

**Richtung Drive → App:** Ein Poll-Mechanismus erkennt externe Drive-Änderungen und lädt sie herunter (Status: `drive_new`).

**Konflikterkennung:** Hat sich Drive seit dem letzten Sync verändert, blockiert die App den Upload und setzt Status `conflict` statt blind zu überschreiben.

### Sync-Status
Der aktuelle Synchronisationszustand eines Fachs mit Drive:

- `synced` — App und Drive sind identisch
- `uploading` — Upload läuft
- `drive_new` — Drive wurde extern aktualisiert und lokal übernommen
- `conflict` — Drive hat sich verändert, lokale Änderung wartet auf Auflösung
- `error` — letzter Sync-Versuch ist fehlgeschlagen
- `unmapped` — Fach ist noch keiner Drive-Datei zugeordnet

### Eintrag
Ein Aktualisierungsschritt einer Sammlung — z.B. "eine neue Klausur wurde hinzugefügt" oder "eine bestehende Altsammlung wurde importiert". Jedes Fach hat eine chronologische Liste von Einträgen. Die aktuelle Sammlung (current.pdf) ergibt sich aus allen Einträgen zusammen.

Interner Code-Name: `Submission`.

**Zwei Unterarten:**
- **Einzelklausur** — eine neu hochgeladene Klausur-PDF (`collection_import: False`)
- **Sammlungsimport** — eine bereits zusammengestellte Altsammlung, die als Ganzes übernommen wurde (`collection_import: True`)
