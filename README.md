# Altklausuren

Lokale Web-App fuer das Referat Altklausuren. Der erste Workflow ist umgesetzt:

- Fach/Sammlung anlegen
- PDF per Drag-and-drop hochladen
- PDF einem Fach zuordnen
- optional das Deckblatt der hochgeladenen PDF entfernen
- bestehende Sammlung ohne altes Deckblatt uebernehmen
- neues Deckblatt mit aktuellem Stand erzeugen
- aktualisierte Sammlung als `current.pdf` speichern und downloadbar machen
- Drive-aehnliche Uebersicht ueber alle Sammlungen
- Suche nach Fach, Modul, Semester, Datum und Notiz
- PDF-Vorschau fuer den aktuellen druckbaren Stand

## Starten

Die PDF-Abhaengigkeiten liegen lokal in `.vendor`. Falls sie fehlen:

```bash
python3 -m pip install --target .vendor pypdf reportlab
```

Dann die App starten. Auf diesem Rechner sollte die Anaconda-Python-Version verwendet werden, weil `.vendor` damit installiert wurde:

```bash
/opt/anaconda3/bin/python3 app.py
```

Danach im Browser oeffnen:

```text
http://127.0.0.1:5001
```

Optional kann das Datenverzeichnis gesetzt werden:

```bash
ALTKLAUSUREN_DATA_DIR=/pfad/zum/data /opt/anaconda3/bin/python3 app.py
```

## Google Drive synchronisieren

Die App kann einen Drive-Ordner rekursiv lesen, alle PDFs herunterladen und daraus die lokale Klausurenstand-Uebersicht aufbauen.

### Variante A: ohne Google Cloud

Das ist fuer den Prototyp der einfachste Weg:

1. Google Drive for Desktop installieren.
2. Mit `lukas.heinz@forum-wi.de` anmelden.
3. Den Altklausuren-Ordner lokal synchronisieren oder streamen lassen.
4. Den lokalen Ordnerpfad in der App bei `Lokaler Drive-Ordner` eintragen.
5. `Lokalen Drive importieren` klicken.

Typische macOS-Pfade sehen so aus:

```text
/Users/<name>/Library/CloudStorage/GoogleDrive-lukas.heinz@forum-wi.de/...
```

Oder per Terminal:

```bash
python3 drive_tools.py local-sync "/Users/<name>/Library/CloudStorage/GoogleDrive-lukas.heinz@forum-wi.de/..."
```

### Variante B: Google Drive API

Aktueller Zielordner:

```text
https://drive.google.com/drive/u/1/folders/0AOnFniEMTZ8bUk9PVA
```

Einmaliges Setup:

1. In der Google Cloud Console einen OAuth-Client fuer eine Desktop-App anlegen.
2. Die heruntergeladene Datei als `data/credentials/client_secret.json` speichern.
3. Lokal autorisieren:

```bash
python3 drive_tools.py authorize
```

Danach entweder in der App auf `API-Sync starten` klicken oder per Terminal starten:

```bash
python3 drive_tools.py sync "https://drive.google.com/drive/u/1/folders/0AOnFniEMTZ8bUk9PVA"
```

Der Sync schreibt:

- `data/drive_config.json`: Drive-Quelle und letzter Sync-Stand
- `data/drive_cache/`: heruntergeladene Drive-PDFs
- `data/subjects/<fach>/current.pdf`: aktuelle druckbare PDF je Sammlung

Wenn der Sync meldet, dass der Ordner nicht gelesen werden kann, ist fast immer der falsche Google-Account autorisiert oder der Drive-Ordner ist nicht mit diesem Account geteilt.

### Dauerhafter Drive-Sync

Fuer den Serverbetrieb kann statt OAuth ein Servicekonto verwendet werden. Lege die Servicekonto-Datei ab als:

```text
data/credentials/service_account.json
```

Alternativ kann der Pfad gesetzt werden:

```bash
SERVICE_ACCOUNT_FILE=/pfad/zur/service_account.json
```

Nach einem initialen API-Import merkt sich jedes Fach seine Drive-Datei. Wenn in der App eine neue Sammlung erzeugt wird, prueft die App zuerst, ob die Drive-Datei noch dem bekannten Stand entspricht. Nur dann wird die Drive-Datei archiviert und durch die neue App-Version ersetzt. Bei einer direkten Drive-Aenderung wird nicht blind ueberschrieben, sondern der Status `conflict` gesetzt.

Worker fuer regelmaessige Drive-Pruefung:

```bash
python3 drive_tools.py poll
```

Credentials und Ordnerzugriff pruefen:

```bash
python3 drive_tools.py check "https://drive.google.com/drive/u/1/folders/0AOnFniEMTZ8bUk9PVA"
```

Der Befehl nutzt dieselbe `.env` wie die Web-App. Fuer lokale Tests sollte `ALTKLAUSUREN_DATA_DIR` auf das Projekt-`data/` zeigen oder beim Befehl ueberschrieben werden:

```bash
ALTKLAUSUREN_DATA_DIR=/Users/lukas184/Altklausuren/Altklausuren/data python3 drive_tools.py check "https://drive.google.com/drive/u/1/folders/0AOnFniEMTZ8bUk9PVA"
```

Weitere Werkzeuge:

```bash
python3 drive_tools.py push <fach-id>
python3 drive_tools.py push <fach-id> --force
python3 drive_tools.py accept-drive <fach-id>
```

Statuswerte in der Uebersicht:

- `synced`: App und Drive sind synchron
- `uploading`: Drive-Upload laeuft bzw. wurde gestartet
- `drive_new`: eine Drive-Version wurde lokal uebernommen
- `conflict`: Drive hat sich geaendert und wurde nicht ueberschrieben
- `error`: letzter Drive-Vorgang ist fehlgeschlagen
- `unmapped`: Fach ist noch keiner Drive-Datei zugeordnet

## Zugriffsschutz fuer den Serverbetrieb

Lokal ist der Login standardmaessig deaktiviert. Fuer `altklausuren.forum-wi.de` wird er per Umgebungsvariablen aktiviert:

```bash
AUTH_ENABLED=true
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://altklausuren.forum-wi.de/auth/callback
DRIVE_ROOT_FOLDER_ID=0AOnFniEMTZ8bUk9PVA
ALLOWED_GOOGLE_DOMAIN=forum-wi.de
ADMIN_EMAILS=lukas.heinz@forum-wi.de
SECRET_KEY=<lange-zufaellige-session-secret>
TRUST_PROXY_HEADERS=true
ALTKLAUSUREN_DATA_DIR=/var/lib/altklausuren
```

Der Login prueft dann:

- Google-Konto ist verifiziert
- E-Mail endet auf `@forum-wi.de`
- dieses Konto kann den konfigurierten Altklausuren-Drive-Ordner lesen

Wenn die Drive-Pruefung fehlschlaegt, wird der Zugriff auf die App verweigert. Personen mit Schreibfaehigkeiten im Drive werden in der Session als `editor` markiert, reine Leser:innen als `viewer`.
E-Mails aus `ADMIN_EMAILS` bekommen zusaetzlich Admin-Rechte fuer Drive-Konfiguration, Import und Konfliktaktionen.

## Deployment

Produktionsvorlagen liegen in `deploy/`:

- `deploy/altklausuren.env.example`: benoetigte Umgebungsvariablen
- `deploy/altklausuren.service`: systemd-Service fuer gunicorn
- `deploy/altklausuren-drive-poll.service`: einmaliger Drive-Poll
- `deploy/altklausuren-drive-poll.timer`: Drive-Poll alle 5 Minuten
- `deploy/nginx-altklausuren.conf`: nginx-Reverse-Proxy

Der WSGI-Einstieg fuer gunicorn ist:

```bash
gunicorn --workers 2 --threads 4 --bind 127.0.0.1:8001 wsgi:app
```

Healthcheck:

```text
/healthz
```

Die detaillierte Server-Checkliste steht in `deploy/README.md`.

## Tests

```bash
python3 -m unittest discover tests
```

## Datenstruktur

Die App schreibt nach `data/`:

- `data/catalog.json`: Faecher und Eintraege
- `data/altklausuren.sqlite3`: dauerhafte SQLite-Datenbank
- `data/subjects/<fach>/current.pdf`: aktuelle Sammlung
- `data/subjects/<fach>/incoming/`: hochgeladene Originale
- `data/subjects/<fach>/archive/`: Backups alter Sammlungen
- `data/subjects/<fach>/exports/`: erzeugte Versionen

Der Drive-Sync ist so gebaut, dass Drive-Aenderungen nicht hart geloescht werden: vor einem App-Upload wird die bisherige Drive-Datei in einen Archivordner kopiert, sofern Drive-Schreibrechte vorhanden sind.
