#!/bin/zsh
# Arranca el servidor desde la carpeta del proyecto (activa antes tu entorno Python).
cd "$(dirname "$0")"
[ -f .env ] && set -a && source .env && set +a
exec caffeinate -s uvicorn main:app --reload --port 8000
