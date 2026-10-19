import sqlite3
import threading
from pathlib import Path


class ChatDB:
    """Tier 1 memory: raw conversation history in SQLite."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role       TEXT NOT NULL,
                        content    TEXT NOT NULL,
                        mode       TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_messages_session "
                    "ON messages(session_id, id)"
                )
                conn.commit()
            finally:
                conn.close()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        mode: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, mode) "
                    "VALUES (?, ?, ?, ?)",
                    (session_id, role, content, mode),
                )
                conn.commit()
            finally:
                conn.close()

    def get_recent(self, session_id: str, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT role, content, mode, created_at
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
                return list(reversed(rows))
            finally:
                conn.close()

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM messages WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
            finally:
                conn.close()