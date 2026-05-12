# Demo-Anleitung – Altklausuren-App

Diese Datei erklärt, wie du die App startest, Rollen wechselst und die wichtigsten Features vorführst.

---

## Server starten

```bash
cd /Users/lukas184/Altklausuren/Altklausuren-1
./start.sh
```

Die App läuft dann auf:
- **Lokal:** http://127.0.0.1:5001
- **Im LAN (Handy, andere Rechner):** http://172.17.11.139:5001 *(IP kann sich nach Netzwechsel ändern – steht beim Start in der Konsole)*

Die echten Daten liegen in `/Users/lukas184/Altklausuren/Altklausuren/data/`.

---

## Rollen wechseln (für Demo)

Ohne Flag startet die App **ohne Login** und du bist direkt Admin (voller Zugriff, kein Login-Screen).

Um einen eingeloggten Nutzer zu simulieren, Server mit Flag starten:

```bash
./start.sh --viewer    # Nur lesen, keine Bearbeitungsmöglichkeiten
./start.sh --editor    # Klausuren hochladen, Sessions verwalten
./start.sh --admin     # Wie editor, aber auch Fächer anlegen/löschen, Einstellungen
./start.sh             # Kein Auth – direkt voller Admin-Zugriff
```

Rolle wechseln = Server stoppen (`Ctrl+C`) und mit anderem Flag neu starten.

**Was jede Rolle sieht/kann:**

| Aktion | Viewer | Editor | Admin |
|--------|--------|--------|-------|
| Klausursammlung ansehen & downloaden | ja | ja | ja |
| Klausur hochladen | nein | ja | ja |
| Protokoll-Session anlegen | nein | ja | ja |
| Fach anlegen / umbenennen / loeschen | nein | nein | ja |
| Drive-Sync verwalten | nein | nein | ja |

---

## Features vorführen

### 1. Klausursammlung ansehen (Viewer-Demo)

```bash
./start.sh --viewer
```

- Startseite zeigt alle Fächer mit ihren PDFs
- Auf ein Fach klicken → Übersicht der Einträge (Semester, Art, Loesung)
- PDF-Vorschau und Download funktionieren
- Kein „Hochladen"-Button sichtbar

---

### 2. Klausur hochladen (Editor-Demo)

```bash
./start.sh --editor
```

- Auf ein Fach klicken → „Neue Klausur hinzufügen"
- PDF hochladen, Metadaten (Semester, Art, Lösung) ausfüllen
- Nach dem Upload wird die Sammlung automatisch neu gebaut

---

### 3. Gedächtnisprotokoll-Session (Kern-Feature)

Das ist das interaktivste Feature. Ablauf:

**Teil 1 – Session anlegen (Editor-Ansicht, Desktop):**

```bash
./start.sh --editor
```

1. Fach öffnen (z.B. Arbeitsrecht)
2. Abschnitt „Protokoll-Sessions" → „Neue Session anlegen", Semester eingeben
3. Es erscheint ein Teilnahme-Link + QR-Code

**Teil 2 – Beitrag einreichen (Studierenden-Ansicht, Handy):**

- QR-Code scannen oder Link auf dem Handy öffnen
- Freitext eingeben → „Beitrag abschicken"
- Danke-Bildschirm erscheint mit QR-Code zum Weiterteilen
- Unter dem Eingabefeld gibt es auch einen „Teilen"-Button (öffnet QR-Modal)

**Teil 3 – Moderieren (Editor-Ansicht):**

1. In der Session-Übersicht erscheinen alle eingereichten Beiträge
2. „Session schliessen" → Eingabe wird für Studierende gesperrt
3. „Zum Editor" → Beiträge zusammenfügen, formatieren
4. „Vorschau PDF" → PDF-Vorschau im Browser
5. „Freigeben" → PDF wird zur Klausursammlung des Fachs hinzugefügt

---

## Projektstruktur (für Claude)

```
Altklausuren-1/          ← Git-Repo, hier liegt der Code
  app.py                 ← Alle Flask-Routen
  storage.py             ← SQLite Catalog-Klasse (alle DB-Operationen)
  pdf_workflow.py        ← PDF-Generierung (Coverpage, Splitting, Proto-PDF)
  auth.py                ← Forward-Auth / Google OAuth Logik
  templates/             ← Jinja2-Templates
    proto_session.html         ← Studierenden-Seite (kein Login nötig)
    proto_session_moderation.html  ← Moderations-Ansicht
    proto_session_editor.html      ← Freitext-Editor + PDF-Preview
  start.sh               ← Server starten mit Rollenauswahl (s.o.)

Altklausuren/data/       ← Echte Daten (NICHT im Git-Repo)
  altklausuren.sqlite3   ← Datenbank
  subjects/<fach-id>/    ← PDFs pro Fach
```

**Server immer so starten (damit echte Daten geladen werden):**

```bash
ALTKLAUSUREN_DATA_DIR=/Users/lukas184/Altklausuren/Altklausuren/data python app.py
```

oder einfach `./start.sh`.

---

## Technische Details für Claude

- **Stack:** Flask (Python), SQLite, ReportLab (PDF), qrcode (QR-PNG)
- **Port:** 5001, gebunden an 0.0.0.0 (LAN-Zugriff)
- **Debug-Modus:** aus → nach jeder Code-Änderung Server neu starten
- **DB-Schema:** wird automatisch migriert beim Start (`_ensure_schema` in storage.py)
- **Kein Autocommit:** nach jedem Write explizit `db.commit()` nötig
- **Proto-Session-Status:** `open` → `closed` → `released` (nicht rückgängig nach release)
- **Contributor-Cookie:** `proto_contributor`, httponly, SameSite=Lax, 1 Jahr
- **Rollen-Reihenfolge:** viewer (0) < editor (1) < admin (2), geprüft via `can(min_role)`
- **Flask `session`-Proxy** darf in proto-Routen nicht durch lokale Variable `session` überschattet werden → lokale Variable heisst `proto_sess`

---

## Bekannte offene Punkte

- **CSRF-Schutz fehlt** auf allen POST-Formularen (ausser `/session/<token>/contribute`). Noch nicht implementiert.
- **SECRET_KEY** sollte in `.env` gesetzt werden (aktuell nur Dev-Default, Warnung beim Start).
