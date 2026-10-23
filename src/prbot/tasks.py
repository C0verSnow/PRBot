"""Durable replies independent of Telegram delivery attempts."""


class TaskStore:
    def __init__(self, state):
        self.db = state.db
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS saved_replies (update_id INTEGER PRIMARY KEY, text TEXT)"
        )

    def reply(self, uid):
        row = self.db.execute("SELECT text FROM saved_replies WHERE update_id=?", (uid,)).fetchone()
        return row[0] if row else None

    def save_reply(self, uid, reply):
        self.db.execute("INSERT OR IGNORE INTO saved_replies VALUES (?,?)", (uid, reply))
