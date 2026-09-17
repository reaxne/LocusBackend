import json
import sqlite3
import pytest
from database import Database
from migrate_sqlite import migrate


def test_import_preserves_existing_users_and_refuses_overwrite(db_url, tmp_path, monkeypatch):
    source = tmp_path / 'legacy.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE users(id INTEGER,username TEXT,password_hash TEXT)')
        db.execute("INSERT INTO users VALUES (7,'existing','salt:hash')")
        db.execute('CREATE TABLE surveys(user_id INTEGER,answers_json TEXT)')
        db.execute('INSERT INTO surveys VALUES (?,?)',(7,json.dumps({'grade':11})))
    monkeypatch.setenv('DATABASE_URL',db_url)
    migrate(source)
    target = Database(db_url)
    assert target.get_user('existing')['password_hash'] == 'salt:hash'
    assert target.get_survey(7) == {'grade':11}
    assert target.create_user('next','hash')['id'] == 8
    with pytest.raises(ValueError,match='empty'):
        migrate(source)
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
