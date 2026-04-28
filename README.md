# ELD Trip Planner — Backend

Django + DRF API for the FMCSA HOS-compliant trip planner. Pairs with the
sibling frontend repo.

- **Live URL:** _TBD_
- **Frontend repo:** _TBD_

## Stack

- Python 3.12, Django 5.x, Django REST Framework
- PostgreSQL (Supabase) via `psycopg[binary]`
- `uv` for dependency management
- `pytest` + `pytest-django` for tests
- Deployed via gunicorn + Whitenoise

## Local setup

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# edit .env — at minimum set DJANGO_SECRET_KEY and DATABASE_URL

# 3. Apply migrations
uv run python manage.py migrate

# 4. Run the dev server
uv run python manage.py runserver
```

## Environment variables

See [.env.example](.env.example) for the full list. Required for any
non-default deployment:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django secret key |
| `DEBUG` | `True`/`False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames |
| `DATABASE_URL` | Postgres connection string |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins |
| `OSRM_BASE_URL` | OSRM routing host (default public demo server) |
| `ORS_API_KEY` | OpenRouteService API key (fallback routing) |
| `NOMINATIM_USER_AGENT` | Required by Nominatim TOS |

## Project layout

```
config/         # Django project (settings, urls, wsgi, asgi)
manage.py
pyproject.toml
uv.lock
```

App modules will be added in subsequent phases.
