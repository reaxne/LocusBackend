"""Load project configuration regardless of the IDE's working directory."""
import os
from pathlib import Path
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().with_name('.env')


def load_environment():
    load_dotenv(ENV_FILE, override=False)


def database_url(explicit=None):
    load_environment()
    value = explicit or os.getenv('DATABASE_URL')
    if not value or not value.strip():
        raise RuntimeError('PostgreSQL is not configured. Run .venv/Scripts/python setup_local.py '
                           'for local development, or set DATABASE_URL in LocusBackend/.env.')
    value = value.strip()
    if not value.startswith(('postgresql://', 'postgres://')):
        raise ValueError('DATABASE_URL must be a postgresql:// or postgres:// connection URL')
    return value
