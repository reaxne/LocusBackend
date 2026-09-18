"""PostgreSQL persistence; no SQLite or file fallback."""
import os
from contextlib import contextmanager
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from settings import database_url


class UsernameAlreadyRegistered(Exception):
    pass


class SurveyConflict(Exception):
    pass


class RoadmapConflict(Exception):
    pass


class Database:
    def __init__(self, url: str | None = None):
        self.url = database_url(url)

    @contextmanager
    def _connect(self):
        if not self.url:
            raise RuntimeError('Set DATABASE_URL to a PostgreSQL connection URL before starting LocusBackend')
        with psycopg.connect(self.url, row_factory=dict_row) as connection:
            yield connection

    def initialize(self):
        with self._connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(78004321)')
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)')
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version = 1').fetchone():
                db.execute((Path(__file__).parent / 'migrations/001_core.sql').read_text(encoding='utf-8'))
                db.execute('INSERT INTO schema_migrations(version) VALUES (1)')
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version = 2').fetchone():
                db.execute((Path(__file__).parent / 'migrations/002_ai.sql').read_text(encoding='utf-8'))
                db.execute('INSERT INTO schema_migrations(version) VALUES (2)')
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version = 3').fetchone():
                db.execute((Path(__file__).parent / 'migrations/003_ai_purposes.sql').read_text(encoding='utf-8'))
                db.execute('INSERT INTO schema_migrations(version) VALUES (3)')
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version = 4').fetchone():
                db.execute((Path(__file__).parent / 'migrations/004_ai_requests.sql').read_text(encoding='utf-8'))
                db.execute('INSERT INTO schema_migrations(version) VALUES (4)')
            if not db.execute('SELECT 1 FROM schema_migrations WHERE version = 5').fetchone():
                db.execute((Path(__file__).parent / 'migrations/005_roadmap_plans.sql').read_text(encoding='utf-8'))
                db.execute('INSERT INTO schema_migrations(version) VALUES (5)')

    @contextmanager
    def ai_request(self, user_id):
        # Cross-process lock prevents concurrent generations for the same account.
        with self._connect() as db:
            locked = db.execute('SELECT pg_try_advisory_xact_lock(78004322, hashtext(%s)) AS locked',
                                (str(user_id),)).fetchone()['locked']
            yield db if locked else None


    def create_user(self, username, password_hash):
        try:
            with self._connect() as db:
                return db.execute('INSERT INTO users(username,password_hash) VALUES (%s,%s) RETURNING id,username',
                                  (username, password_hash)).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            raise UsernameAlreadyRegistered(username) from exc

    def get_user(self, username):
        with self._connect() as db:
            return db.execute('SELECT * FROM users WHERE username=%s', (username,)).fetchone()

    def get_session(self, token_hash, now):
        with self._connect() as db:
            return db.execute('''SELECT users.id, users.username, sessions.token_hash FROM sessions
                JOIN users ON users.id=sessions.user_id WHERE token_hash=%s AND expires_at>%s''',
                              (token_hash, now)).fetchone()

    def create_session(self, token_hash, user_id, expires_at, now):
        with self._connect() as db:
            db.execute('DELETE FROM sessions WHERE expires_at<=%s', (now,))
            db.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (%s,%s,%s)', (token_hash,user_id,expires_at))

    def delete_session(self, token_hash):
        with self._connect() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=%s', (token_hash,))

    def save_survey(self, user_id, answers, state=None, expected_revision=None):
        with self._connect() as db:
            # Lock the owner, including when no survey row exists yet.
            db.execute('SELECT id FROM users WHERE id=%s FOR UPDATE', (user_id,))
            previous = db.execute('SELECT revision FROM surveys WHERE user_id=%s', (user_id,)).fetchone()
            revision = previous['revision'] if previous else 0
            if expected_revision is not None and expected_revision != revision:
                raise SurveyConflict('Survey changed in another tab')
            revision += 1
            if state is not None:
                state = {**state, 'revision': revision}
            db.execute('''INSERT INTO surveys(user_id,answers_json,state_json,revision) VALUES (%s,%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET answers_json=excluded.answers_json,
                state_json=excluded.state_json,revision=excluded.revision,updated_at=now()''',
                (user_id,Jsonb(answers),Jsonb(state) if state is not None else None,revision))
            return state

    def get_survey_record(self, user_id):
        with self._connect() as db:
            return db.execute('SELECT answers_json,state_json,revision,updated_at FROM surveys WHERE user_id=%s', (user_id,)).fetchone()

    def get_survey(self, user_id):
        row = self.get_survey_record(user_id)
        return row['answers_json'] if row else None

    def save_program_records(self, records):
        with self._connect() as db:
            with db.cursor() as cursor:
                cursor.executemany('''INSERT INTO programs(id,admission_year,active,is_demo,data_json) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(id,admission_year) DO UPDATE SET active=excluded.active,is_demo=excluded.is_demo,data_json=excluded.data_json''',
                    [(item['id'],item['admission_year'] or 0,int(item['active']),int(item['is_demo']),Jsonb(item)) for item in records])

    def get_program_records(self, year):
        with self._connect() as db:
            rows = db.execute('''SELECT data_json FROM programs WHERE admission_year IN (%s,0) AND active=1 AND is_demo=0
                AND (admission_year=%s OR NOT EXISTS (SELECT 1 FROM programs specific WHERE specific.id=programs.id
                AND specific.admission_year=%s)) ORDER BY id,admission_year DESC''', (year,year,year)).fetchall()
        return [row['data_json'] for row in rows]

    def get_program_record(self, program_id, year):
        return next((row for row in self.get_program_records(year) if row['id'] == program_id), None)

    def get_roadmap_preferences(self, user_id):
        with self._connect() as db:
            return db.execute('''SELECT timezone,available_hours_per_week,achievements
                FROM roadmap_preferences WHERE user_id=%s''', (user_id,)).fetchone()

    def save_roadmap_preferences(self, user_id, value):
        with self._connect() as db:
            return db.execute('''INSERT INTO roadmap_preferences
                (user_id,timezone,available_hours_per_week,achievements) VALUES (%s,%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET timezone=excluded.timezone,
                available_hours_per_week=excluded.available_hours_per_week,
                achievements=excluded.achievements,updated_at=now()
                RETURNING timezone,available_hours_per_week,achievements,updated_at''',
                (user_id,value['timezone'],value.get('available_hours_per_week'),
                 Jsonb(value.get('achievements', [])))).fetchone()

    def get_roadmap_plan(self, user_id):
        with self._connect() as db:
            return db.execute('''SELECT revision,request_id,input_hash,plan_json,generated_at,updated_at
                FROM roadmap_plans WHERE user_id=%s''', (user_id,)).fetchone()

    def get_roadmap_progress(self, user_id, db=None):
        if db is not None:
            rows = db.execute('''SELECT step_id,status,completed_at FROM roadmap_step_progress
                WHERE user_id=%s''', (user_id,)).fetchall()
            return {row['step_id']: row for row in rows}
        with self._connect() as connection:
            rows = connection.execute('''SELECT step_id,status,completed_at FROM roadmap_step_progress
                WHERE user_id=%s''', (user_id,)).fetchall()
        return {row['step_id']: row for row in rows}

    def save_roadmap_plan(self, db, user_id, request_id, input_hash, plan):
        active_ids = [step['id'] for step in plan['steps']]
        db.execute('''DELETE FROM roadmap_step_progress WHERE user_id=%s
            AND NOT (step_id = ANY(%s))''', (user_id,active_ids))
        return db.execute('''INSERT INTO roadmap_plans
            (user_id,request_id,input_hash,plan_json,generated_at) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT(user_id) DO UPDATE SET revision=roadmap_plans.revision+1,
            request_id=excluded.request_id,input_hash=excluded.input_hash,
            plan_json=excluded.plan_json,generated_at=excluded.generated_at,updated_at=now()
            RETURNING revision''',
            (user_id,request_id,input_hash,Jsonb(plan),plan['generatedAt'])).fetchone()['revision']

    def update_roadmap_step(self, user_id, expected_revision, plan, changes):
        with self._connect() as db:
            db.execute('SELECT pg_advisory_xact_lock(78004322, hashtext(%s))', (str(user_id),))
            row = db.execute('SELECT revision FROM roadmap_plans WHERE user_id=%s FOR UPDATE',
                             (user_id,)).fetchone()
            if row is None:
                raise KeyError('roadmap')
            if row['revision'] != expected_revision:
                raise RoadmapConflict('Roadmap changed in another tab')
            revision = expected_revision + 1
            for step_id, change in changes.items():
                db.execute('''INSERT INTO roadmap_step_progress(user_id,step_id,status,completed_at)
                    VALUES (%s,%s,%s,%s) ON CONFLICT(user_id,step_id) DO UPDATE
                    SET status=excluded.status,completed_at=excluded.completed_at,updated_at=now()''',
                    (user_id,step_id,change['status'],change['completed_at']))
            db.execute('''UPDATE roadmap_plans SET revision=%s,plan_json=%s,updated_at=now()
                WHERE user_id=%s''', (revision,Jsonb(plan),user_id))
            return revision

    def get_ai_request(self, user_id, request_id):
        with self._connect() as db:
            return db.execute('''SELECT id,purpose,stage,cancelled,started_at,ended_at,metrics
                FROM ai_requests WHERE id=%s AND user_id=%s''', (request_id,user_id)).fetchone()
