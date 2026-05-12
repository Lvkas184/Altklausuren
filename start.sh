#!/bin/bash
# Verwendung:
#   ./start.sh            → Admin (kein Login, voller Zugriff)
#   ./start.sh --viewer   → Simuliert eingeloggten Viewer (nur Ansicht)
#   ./start.sh --editor   → Simuliert eingeloggten Editor (Klausuren + Sessions verwalten)
#   ./start.sh --admin    → Simuliert eingeloggten Admin mit Auth-Login-Anzeige

BASE_ENV="ALTKLAUSUREN_DATA_DIR=/Users/lukas184/Altklausuren/Altklausuren/data"

if [ "$1" = "--viewer" ]; then
  exec env \
    $BASE_ENV \
    SECRET_KEY=dev-secret-key-local \
    AUTH_ENABLED=1 \
    FORWARD_AUTH_ENABLED=1 \
    FORWARD_AUTH_DEV_ENABLED=1 \
    FORWARD_AUTH_DEV_EMAIL=viewer@forum-wi.de \
    FORWARD_AUTH_DEV_NAME="Test Viewer" \
    FORWARD_AUTH_DEV_GROUPS=altklausuren-viewer \
    python app.py
elif [ "$1" = "--editor" ]; then
  exec env \
    $BASE_ENV \
    SECRET_KEY=dev-secret-key-local \
    AUTH_ENABLED=1 \
    FORWARD_AUTH_ENABLED=1 \
    FORWARD_AUTH_DEV_ENABLED=1 \
    FORWARD_AUTH_DEV_EMAIL=editor@forum-wi.de \
    FORWARD_AUTH_DEV_NAME="Test Editor" \
    FORWARD_AUTH_DEV_GROUPS=altklausuren-editor \
    python app.py
elif [ "$1" = "--admin" ]; then
  exec env \
    $BASE_ENV \
    SECRET_KEY=dev-secret-key-local \
    AUTH_ENABLED=1 \
    FORWARD_AUTH_ENABLED=1 \
    FORWARD_AUTH_DEV_ENABLED=1 \
    FORWARD_AUTH_DEV_EMAIL=admin@forum-wi.de \
    FORWARD_AUTH_DEV_NAME="Test Admin" \
    FORWARD_AUTH_DEV_GROUPS=altklausuren-admin \
    python app.py
else
  # Kein Auth – direkt als Admin, kein Login-Screen
  exec env $BASE_ENV python app.py
fi
