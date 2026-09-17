"""Explicit, repeatable local PostgreSQL setup. Never replaces an existing .env or cluster."""
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import psycopg
from psycopg import sql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data' / 'postgres-local'
PORT = 54328


def binaries():
    if os.getenv('PG_BIN'):
        return Path(os.environ['PG_BIN'])
    found = shutil.which('pg_ctl')
    if found:
        return Path(found).parent
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'PostgreSQL'
    candidates = sorted(base.glob('*/bin/pg_ctl.exe'), reverse=True)
    if candidates:
        return candidates[0].parent
    raise RuntimeError('Install PostgreSQL or set PG_BIN to its bin folder.')


def start(pg):
    status = subprocess.run([str(pg/'pg_ctl'),'-D',str(DATA),'status'],capture_output=True)
    if status.returncode:
        subprocess.run([str(pg/'pg_ctl'),'-D',str(DATA),'-l',str(DATA.parent/'postgres-local.log'),
                        '-o',f'-h 127.0.0.1 -p {PORT}','-w','start'],check=True)


def ensure_local():
    env_file = ROOT / '.env'
    existing = dotenv_values(env_file) if env_file.exists() else {}
    configured = os.getenv('DATABASE_URL') or existing.get('DATABASE_URL')
    if configured:
        if existing.get('LOCUS_LOCAL_POSTGRES') == 'true' and configured == existing.get('DATABASE_URL'):
            start(binaries())
        with psycopg.connect(configured,connect_timeout=5) as db:
            db.execute('SELECT 1')
        print('Configured PostgreSQL connection is ready.')
        return
    if env_file.exists() or DATA.exists():
        raise RuntimeError('Existing .env or local cluster found; refusing to overwrite. Configure DATABASE_URL explicitly.')
    pg = binaries()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',PORT))
    DATA.parent.mkdir(parents=True,exist_ok=True)
    admin_password = secrets.token_urlsafe(32)
    password = secrets.token_urlsafe(32)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=DATA.parent,delete=False) as secret_file:
        secret_file.write(admin_password + '\n')
        secret_path = Path(secret_file.name)
    try:
        subprocess.run([str(pg/'initdb'),'-D',str(DATA),'-U','locus_admin',
            '--auth=scram-sha-256','--encoding=UTF8','--no-locale','--pwfile='+str(secret_path)],
            check=True,stdout=subprocess.DEVNULL)
    finally:
        secret_path.unlink(missing_ok=True)
    start(pg)
    with psycopg.connect(host='127.0.0.1',port=PORT,user='locus_admin',password=admin_password,
                         dbname='postgres',autocommit=True) as db:
        db.execute(sql.SQL('CREATE ROLE locus LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(password)))
        db.execute('CREATE DATABASE locus OWNER locus')
    # Configuration secrets only; student data is stored in PostgreSQL.
    with env_file.open('x',encoding='utf-8') as config:
        config.write(f'DATABASE_URL=postgresql://locus:{password}@127.0.0.1:{PORT}/locus\n'
            'COOKIE_SECURE=false\nLOCUS_LOCAL_POSTGRES=true\n'
            f'LOCAL_POSTGRES_ADMIN_PASSWORD={admin_password}\n'
            'ALLOWED_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173","http://127.0.0.1:3000","http://localhost:3000"]\n')
    print(f'Local PostgreSQL is ready on 127.0.0.1:{PORT}; configuration saved to ignored .env.')


if __name__ == '__main__':
    ensure_local()
