#!/usr/bin/env bash
# Container entrypoint for Railway. Logs each step explicitly so deploy
# failures are diagnosable from the Railway log stream.
set -e

echo "[start] PORT=${PORT:-<unset>} RAILWAY_ENVIRONMENT=${RAILWAY_ENVIRONMENT:-<unset>}"
echo "[start] running migrations..."
python manage.py migrate --noinput
echo "[start] migrations done; launching gunicorn on 0.0.0.0:${PORT:-8000}"

exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --preload \
  --access-logfile - \
  --error-logfile - \
  --log-level info
