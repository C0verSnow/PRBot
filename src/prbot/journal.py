"""Local data exists independently of credentials and network availability."""

import sqlite3
from contextlib import contextmanager
from threading import Lock

from prbot.paths import data_dir

_initialized = set()
_initialization_lock = Lock()


@contextmanager
def journal_db():
    path = data_dir() / "workbench.sqlite3"
    # Create with restricted permissions even when used outside the CLI entry point.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize_journal():
    # A fresh credential lookup need not repeat schema DDL on every UI poll.
    path = data_dir() / "workbench.sqlite3"
    with _initialization_lock:
        identity = (path, path.stat().st_ino) if path.exists() else None
        if identity in _initialized:
            return path
        _create_schema()
        _initialized.add((path, path.stat().st_ino))
    return path


def _create_schema():
    with journal_db() as db:
        db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, actor TEXT, source TEXT, mode TEXT,
                    conversation TEXT, status TEXT, payload TEXT, result TEXT,
                    pid INTEGER, created REAL, changed REAL);
                CREATE TABLE IF NOT EXISTS task_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
                    kind TEXT, text TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS replies (
                    id TEXT PRIMARY KEY, task_id TEXT, text TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    scope TEXT PRIMARY KEY, link TEXT, mode TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS pending_answers (
                    task_id TEXT PRIMARY KEY, reply_id TEXT UNIQUE, text TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS telegram_drafts (
                    scope TEXT PRIMARY KEY, task_id TEXT);
                CREATE TABLE IF NOT EXISTS telegram_updates (
                    id INTEGER PRIMARY KEY, scope TEXT, task_id TEXT, mode TEXT,
                    snapshot TEXT, content TEXT, created REAL, reply TEXT);
                CREATE TABLE IF NOT EXISTS telegram_notices (
                    task_id TEXT, round INTEGER, status TEXT, message_id INTEGER,
                    attempts INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0,
                    retry_at REAL DEFAULT 0,
                    PRIMARY KEY(task_id, round, status));

            CREATE TABLE IF NOT EXISTS history_messages (
                scope TEXT, conversation_id TEXT, message_id TEXT, payload TEXT,
                PRIMARY KEY(scope, conversation_id, message_id));
            CREATE TABLE IF NOT EXISTS history_settings (
                key TEXT PRIMARY KEY, value TEXT);
        """)
