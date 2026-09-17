"""Each integration case gets an isolated PostgreSQL schema, never a SQLite file."""
import os
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg
from psycopg import sql
import pytest


@pytest.fixture
def db_url():
    base = os.environ.get('TEST_DATABASE_URL')
    if not base:
        pytest.fail('Set TEST_DATABASE_URL to a dedicated PostgreSQL test database')
    name = 'test_' + uuid.uuid4().hex
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    url = urlsplit(base)
    query = urlencode([*parse_qsl(url.query), ('options', '-csearch_path=' + name)])
    yield urlunsplit((url.scheme, url.netloc, url.path, query, url.fragment))
    with psycopg.connect(base, autocommit=True) as db:
        db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
