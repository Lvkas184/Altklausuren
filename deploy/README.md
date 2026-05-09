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
