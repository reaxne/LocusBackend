"""SQLite storage for users, sessions, and surveys."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class UsernameAlreadyRegistered(Exception):
    """The requested username is already in use."""


class Database:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("DATABASE_PATH", "data/auth.db"))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS surveys (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    answers_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS programs (
                    id TEXT NOT NULL,
                    admission_year INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    is_demo INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY (id, admission_year)
                );
                CREATE INDEX IF NOT EXISTS programs_cycle ON programs(admission_year, active, is_demo);
            """)

    def create_user(self, username: str, password_hash: str) -> dict:
        try:
            with self._connect() as db:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, password_hash),
                )
                return {"id": cursor.lastrowid, "username": username}
        except sqlite3.IntegrityError as exc:
            raise UsernameAlreadyRegistered(username) from exc

    def get_user(self, username: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
        return dict(row) if row is not None else None

    def get_session(self, token_hash: str, now: int) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT users.id, users.username, sessions.token_hash
                   FROM sessions JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (token_hash, now),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_session(self, token_hash: str, user_id: int, expires_at: int, now: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            db.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def save_survey(self, user_id: int, answers: dict) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO surveys (user_id, answers_json) VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET answers_json = excluded.answers_json""",
                (user_id, json.dumps(answers, ensure_ascii=False)),
            )

    def get_survey(self, user_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT answers_json FROM surveys WHERE user_id = ?", (user_id,)
            ).fetchone()
        return json.loads(row["answers_json"]) if row is not None else None

    def save_program_records(self, records: list[dict]) -> None:
        with self._connect() as db:
            db.executemany(
                """INSERT INTO programs (id, admission_year, active, is_demo, data_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id, admission_year) DO UPDATE SET
                   active = excluded.active, is_demo = excluded.is_demo, data_json = excluded.data_json""",
                [(item["id"], item["admission_year"] or 0, item["active"], item["is_demo"],
                  json.dumps(item, ensure_ascii=False)) for item in records],
            )

    def get_program_records(self, year: int) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT data_json FROM programs WHERE admission_year IN (?, 0)
                   AND active = 1 AND is_demo = 0
                   AND (admission_year = ? OR NOT EXISTS (
                       SELECT 1 FROM programs AS specific
                       WHERE specific.id = programs.id AND specific.admission_year = ?
                   )) ORDER BY id, admission_year DESC""", (year, year, year),
            ).fetchall()
        # Prefer cycle-specific data over an unknown-cycle record for the same program.
        records = {}
        for row in rows:
            item = json.loads(row["data_json"])
            records.setdefault(item["id"], item)
        return list(records.values())

    def get_program_record(self, program_id: str, year: int) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT data_json FROM programs WHERE id = ? AND admission_year IN (?, 0)
                   AND active = 1 AND is_demo = 0
                   AND (admission_year = ? OR NOT EXISTS (
                       SELECT 1 FROM programs AS specific
                       WHERE specific.id = programs.id AND specific.admission_year = ?
                   )) ORDER BY admission_year DESC LIMIT 1""",
                (program_id, year, year, year),
            ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None
