import fcntl
import hashlib
import sqlite3
import time
from contextlib import contextmanager

from prbot.paths import data_dir


@contextmanager
def instance_lock():
    with open(data_dir() / "service.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有 PRBot 实例运行，请先停止服务。") from None
        yield


class State:
    def __init__(self, token):
        namespace = hashlib.sha256(token.encode()).hexdigest()[:24]
        self.db = sqlite3.connect(data_dir() / f"updates-{namespace}.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY, value INTEGER)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS updates (id INTEGER PRIMARY KEY, status TEXT, "
            "attempts INTEGER DEFAULT 0, reason TEXT, changed REAL)"
        )
        self.db.commit()

    @property
    def offset(self):
        row = self.db.execute("SELECT value FROM progress WHERE id=1").fetchone()
        return row[0] if row else 0

    def record(self, update_id):
        return self.db.execute(
            "SELECT status, attempts FROM updates WHERE id=?", (update_id,)
        ).fetchone()

    def mark(self, update_id, status, reason=None, attempt=False, advance=False):
        with self.db:
            self.db.execute(
                "INSERT INTO updates VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "status=excluded.status, attempts=updates.attempts+excluded.attempts, "
                "reason=excluded.reason, changed=excluded.changed",
                (update_id, status, int(attempt), reason, time.time()),
            )
            if advance:
                self.db.execute(
                    "INSERT INTO progress VALUES (1, ?) ON CONFLICT(id) DO UPDATE "
                    "SET value=MAX(value, excluded.value)",
                    (update_id + 1,),
                )
                self.db.execute(
                    "DELETE FROM updates WHERE changed < ? AND status IN ('delivered','ignored')",
                    (time.time() - 30 * 86400,),
                )

    def close(self):
        self.db.close()
