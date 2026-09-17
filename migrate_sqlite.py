"""One-time read-only import of the previous SQLite database into an EMPTY PostgreSQL database."""
import argparse
import json
from pathlib import Path
import sqlite3
from psycopg.types.json import Jsonb
from database import Database


def migrate(source: Path):
    if not source.is_file():
        raise ValueError('Source database does not exist')
    target = Database()
    target.initialize()
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as old, target._connect() as db:
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ['users','sessions','surveys','programs']:
            if db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
                raise ValueError('Target must be empty; existing PostgreSQL rows will not be overwritten')
        for row in old.execute('SELECT id,username,password_hash FROM users'):
            db.execute('INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)',row)
        if 'sessions' in tables:
            for row in old.execute('SELECT token_hash,user_id,expires_at FROM sessions'):
                db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)',row)
        if 'surveys' in tables:
            for user_id, answers in old.execute('SELECT user_id,answers_json FROM surveys'):
                db.execute('INSERT INTO surveys(user_id,answers_json) VALUES (%s,%s)',(user_id,Jsonb(json.loads(answers))))
        if 'programs' in tables:
            for key, year, active, demo, data in old.execute('SELECT id,admission_year,active,is_demo,data_json FROM programs'):
                db.execute('INSERT INTO programs(id,admission_year,active,is_demo,data_json) VALUES (%s,%s,%s,%s,%s)',
                           (key,year,active,demo,Jsonb(json.loads(data))))
        db.execute("SELECT setval(pg_get_serial_sequence('users','id'), COALESCE(MAX(id),1), COUNT(*)>0) FROM users")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args = parser.parse_args()
    migrate(args.source)
    print('Imported existing records into PostgreSQL. Source database was not modified.')
