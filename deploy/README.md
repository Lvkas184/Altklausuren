# Deployment

Diese Vorlagen sind fuer einen kleinen Linux-Server mit nginx, systemd und gunicorn gedacht.

## Google OAuth

In der Google Cloud Console einen OAuth-Client vom Typ `Web application` anlegen.

Erlaubte Redirect-URI:

```text
https://altklausuren.forum-wi.de/auth/callback
```

Die Werte danach in `/etc/altklausuren/altklausuren.env` eintragen:

```text
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://altklausuren.forum-wi.de/auth/callback
DRIVE_ROOT_FOLDER_ID=0AOnFniEMTZ8bUk9PVA
ALLOWED_GOOGLE_DOMAIN=forum-wi.de
ADMIN_EMAILS=lukas.heinz@forum-wi.de
```

Der Login erlaubt nur verifizierte `@forum-wi.de`-Konten, die den Altklausuren-Drive-Ordner lesen koennen. Drive-Schreibrechte werden als `editor` erkannt. `ADMIN_EMAILS` bekommen zusaetzlich Admin-Rechte in der App.

## Server-Dateien

Empfohlene Pfade:

```text
/opt/altklausuren                         App-Code
/opt/altklausuren/.venv                   Python-Umgebung
/var/lib/altklausuren                     persistente Daten
/var/lib/altklausuren/credentials         Servicekonto-Credentials
/etc/altklausuren/altklausuren.env        Umgebungsvariablen
```

`/var/lib/altklausuren` muss fuer den Linux-User `altklausuren` schreibbar sein.

## Start

```bash
python3 -m venv /opt/altklausuren/.venv
/opt/altklausuren/.venv/bin/pip install -r /opt/altklausuren/requirements.txt
```

Dann die Vorlagen kopieren:

```bash
cp deploy/altklausuren.service /etc/systemd/system/altklausuren.service
cp deploy/altklausuren-drive-poll.service /etc/systemd/system/altklausuren-drive-poll.service
cp deploy/altklausuren-drive-poll.timer /etc/systemd/system/altklausuren-drive-poll.timer
cp deploy/nginx-altklausuren.conf /etc/nginx/sites-available/altklausuren.conf
ln -s /etc/nginx/sites-available/altklausuren.conf /etc/nginx/sites-enabled/altklausuren.conf
```

Aktivieren:

```bash
systemctl daemon-reload
systemctl enable --now altklausuren.service
systemctl enable --now altklausuren-drive-poll.timer
nginx -t
systemctl reload nginx
```

HTTPS sollte danach per certbot oder vorhandener Infrastruktur fuer `altklausuren.forum-wi.de` aktiviert werden. Bis HTTPS aktiv ist, funktioniert Google OAuth fuer die Produktions-Redirect-URI nicht sinnvoll.

## Healthcheck

```text
https://altklausuren.forum-wi.de/healthz
```

## Tägliches Datenbank-Backup

Das Skript `backup.py` sichert die SQLite-Datenbank täglich:
- Lokale Kopie in `$ALTKLAUSUREN_DATA_DIR/db-backups/` (7 Tage Rotation)
- Optionaler Upload nach Google Drive (via `BACKUP_DRIVE_FOLDER_ID`)

Für den Drive-Upload einen Ordner auf drive.google.com anlegen, die Ordner-ID aus der URL kopieren und in `/etc/altklausuren/altklausuren.env` eintragen:

```text
BACKUP_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOpQrStUvWx
```

Backup-Service und Timer installieren:

```bash
cp deploy/altklausuren-backup.service /etc/systemd/system/altklausuren-backup.service
cp deploy/altklausuren-backup.timer   /etc/systemd/system/altklausuren-backup.timer
systemctl daemon-reload
systemctl enable --now altklausuren-backup.timer
```

Status prüfen:

```bash
systemctl status altklausuren-backup.timer   # Timer-Status
journalctl -u altklausuren-backup.service    # Backup-Logs
```

Manuell ausführen (z.B. zum Testen):

```bash
systemctl start altklausuren-backup.service
```

## Server einrichten (Schritt für Schritt)

Die App läuft am besten auf einem kleinen Linux-Server (VPS), unabhängig vom eigenen Laptop.
Empfehlung: **Hetzner Cloud CX22** (~4 €/Monat, 2 vCPU, 4 GB RAM, 40 GB SSD).

### 1. Server bestellen

1. Account anlegen auf [hetzner.com/cloud](https://www.hetzner.com/cloud/)
2. Neues Projekt anlegen → „Add Server"
   - Location: Nürnberg oder Falkenstein
   - Image: **Ubuntu 24.04**
   - Type: CX22 (4 €/Monat reicht)
   - SSH-Key hinterlegen (eigenen Public Key einfügen)
3. Server starten → IP-Adresse notieren

### 2. Domain zeigen lassen

Im DNS-Anbieter von `forum-wi.de` einen A-Record anlegen:

```
altklausuren.forum-wi.de  →  <IP-Adresse des Servers>
```

### 3. Server einrichten

```bash
# Als root einloggen
ssh root@<IP>

# System aktualisieren
apt update && apt upgrade -y

# Benötigte Pakete
apt install -y python3 python3-venv nginx certbot python3-certbot-nginx

# App-User anlegen
useradd -r -m -d /opt/altklausuren -s /bin/bash altklausuren

# Daten-Verzeichnis
mkdir -p /var/lib/altklausuren
chown altklausuren:altklausuren /var/lib/altklausuren

# Konfigurationsverzeichnis
mkdir -p /etc/altklausuren
```

### 4. App deployen

```bash
# Code auf den Server kopieren (vom eigenen Mac aus)
rsync -av --exclude='.git' --exclude='__pycache__' --exclude='data/' \
  /Users/lukas184/Altklausuren/Altklausuren/ root@<IP>:/opt/altklausuren/

# Auf dem Server: Python-Umgebung einrichten
su - altklausuren
cd /opt/altklausuren
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
exit
```

### 5. Konfiguration anlegen

```bash
cp /opt/altklausuren/deploy/altklausuren.env.example /etc/altklausuren/altklausuren.env
# Datei mit echten Werten befüllen (SECRET_KEY, Google-OAuth, Drive-Ordner-IDs)
nano /etc/altklausuren/altklausuren.env
```

### 6. Services aktivieren

```bash
cp /opt/altklausuren/deploy/altklausuren.service         /etc/systemd/system/
cp /opt/altklausuren/deploy/altklausuren-drive-poll.service /etc/systemd/system/
cp /opt/altklausuren/deploy/altklausuren-drive-poll.timer   /etc/systemd/system/
cp /opt/altklausuren/deploy/altklausuren-backup.service  /etc/systemd/system/
cp /opt/altklausuren/deploy/altklausuren-backup.timer    /etc/systemd/system/
cp /opt/altklausuren/deploy/nginx-altklausuren.conf      /etc/nginx/sites-available/altklausuren.conf
ln -s /etc/nginx/sites-available/altklausuren.conf /etc/nginx/sites-enabled/

systemctl daemon-reload
systemctl enable --now altklausuren.service
systemctl enable --now altklausuren-drive-poll.timer
systemctl enable --now altklausuren-backup.timer
nginx -t && systemctl reload nginx
```

### 7. HTTPS einrichten

```bash
certbot --nginx -d altklausuren.forum-wi.de
```

Danach ist die App unter `https://altklausuren.forum-wi.de` erreichbar — unabhängig vom Mac.
