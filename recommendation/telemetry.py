"""Safe production metadata and protected opt-in database debug payloads."""
import contextvars
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from psycopg.types.json import Jsonb

current_job = contextvars.ContextVar('ai_job', default=None)
logger = logging.getLogger('locus.ai')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, database, request_id, user_id, purpose):
        self.database, self.id, self.user_id, self.purpose = database, request_id, user_id, purpose
        self.events = queue.Queue()
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.last_check = 0
        self.timings = {}
        self.registered = False

    def register(self):
        with self.database._connect() as db:
            row = db.execute('''INSERT INTO ai_requests(id,user_id,purpose) VALUES (%s,%s,%s)
                ON CONFLICT(id) DO NOTHING RETURNING id''', (self.id,self.user_id,self.purpose)).fetchone()
            if row is None:
                raise Cancelled('duplicate_or_cancelled')
            db.execute("DELETE FROM ai_requests WHERE started_at < now() - interval '7 days'")
        self.registered = True
        self.emit('preparing')

    def check(self):
        if self.stop.is_set():
            raise Cancelled('cancelled')
        now = time.monotonic()
        if now - self.last_check >= 0.5:
            self.last_check = now
            with self.database._connect() as db:
                row = db.execute('SELECT cancelled FROM ai_requests WHERE id=%s', (self.id,)).fetchone()
            if row and row['cancelled']:
                self.stop.set()
                raise Cancelled('cancelled')

    def emit(self, stage, **metrics):
        elapsed = round((time.monotonic() - self.started) * 1000, 2)
        data = {'requestId': self.id, 'purpose': self.purpose, 'stage': stage,
                'timestamp': datetime.now(timezone.utc).isoformat(), 'elapsedMs': elapsed, **metrics}
        # Only explicitly constructed metrics reach this logger; never request bodies/headers.
        logger.info(json.dumps(data, ensure_ascii=False))
        self.timings[stage] = elapsed
        self.events.put({'type': 'stage', **data})
        if self.registered:
            with self.database._connect() as db:
                db.execute('UPDATE ai_requests SET stage=%s,metrics=%s WHERE id=%s',
                           (stage,Jsonb(self.timings),self.id))

    def debug(self, kind, payload):
        if os.getenv('APP_ENV') != 'development' or os.getenv('AI_DEBUG_PAYLOADS') != 'true':
            return
        def scrub(value):
            if isinstance(value, dict):
                return {k: '[REDACTED]' if any(word in k.lower() for word in ('password','token','cookie','authorization','api_key','api_gemma')) else scrub(v)
                        for k,v in value.items()}
            if isinstance(value, list): return [scrub(v) for v in value]
            return value
        with self.database._connect() as db:
            db.execute('INSERT INTO ai_debug_payloads(request_id,kind,payload) VALUES (%s,%s,%s)',
                       (self.id,kind,Jsonb(scrub(payload))))

    def finish(self, status, **metrics):
        self.emit(status, **metrics)
        if not self.registered: return
        with self.database._connect() as db:
            db.execute('UPDATE ai_requests SET stage=%s, ended_at=now(),metrics=%s WHERE id=%s',
                       (status,Jsonb(self.timings),self.id))


def emit(stage, **metrics):
    job = current_job.get()
    if job: job.emit(stage, **metrics)


def check():
    job = current_job.get()
    if job: job.check()
