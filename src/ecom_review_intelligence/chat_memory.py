"""D3 chat memory: durable conversation history and operator preferences.

Today the chat lives only in process memory: restart the workbench and the
conversation is gone (a recorded handoff TODO). This module persists two
things to one local SQLite file: per-run chat turns and small operator
preferences (for example the selected model id).

Privacy line, unchanged: review texts never persist. The stored turn view is
the public one with every verbatim quote stripped, so a restored conversation
shows what was said but not the review wording; the model-side history entry
is the same compact summary the in-process session keeps. Preference keys
that look like secrets are refused outright -- API keys stay in the browser
and are never written to disk.

Pure storage: no network, no model calls. Each operation opens its own
read-only or short-lived connection, so no locking discipline is needed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

MEMORY_NOTE = "历史记录不含评论原文；逐字引用已去除。"
_HISTORY_TURN_LIMIT = 8
_PREFERENCE_KEY_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")
_PREFERENCE_VALUE_MAX = 200
_SECRETISH_KEY = re.compile(r"key|token|secret|password", re.IGNORECASE)


class ChatMemoryError(ValueError):
    """Raised when a preference key or value violates the closed schema."""


def redact_turn_for_memory(turn: Mapping[str, object]) -> dict[str, object]:
    """Public turn view with verbatim quotes stripped; review text never persists.

    The result must still validate as a ``ChatTurnView``, so the memory note
    lives on the history wrapper, not inside the turn.
    """

    redacted = dict(turn)
    points = []
    for point in turn.get("review_points") or ():
        if isinstance(point, Mapping):
            stripped = dict(point)
        else:
            continue
        stripped["quote"] = None
        points.append(stripped)
    redacted["review_points"] = points
    return redacted


class ChatMemoryStore:
    """SQLite-backed chat history and preferences; one file, no daemons."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_token TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    assistant_summary TEXT NOT NULL,
                    turn_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_turns_run ON chat_turns(run_token, id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def append_turn(
        self,
        run_token: str,
        *,
        user_message: str,
        assistant_summary: str,
        turn: Mapping[str, object],
    ) -> None:
        """Store one finished turn; the stored view carries no verbatim quotes."""

        record = redact_turn_for_memory(turn)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO chat_turns"
                " (run_token, user_message, assistant_summary, turn_json, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    run_token,
                    user_message,
                    assistant_summary,
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def load_history(self, run_token: str, *, limit: int = _HISTORY_TURN_LIMIT) -> list[dict[str, object]]:
        """The last ``limit`` turns for one run, oldest first."""

        connection = sqlite3.connect(f"file:{self._path.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT user_message, assistant_summary, turn_json, created_at"
                " FROM chat_turns WHERE run_token = ? ORDER BY id DESC LIMIT ?",
                (run_token, max(1, int(limit))),
            ).fetchall()
        finally:
            connection.close()
        history = []
        for user_message, assistant_summary, turn_json, created_at in reversed(rows):
            history.append(
                {
                    "user_message": user_message,
                    "assistant_summary": assistant_summary,
                    "turn": json.loads(turn_json),
                    "created_at": created_at,
                }
            )
        return history

    def count_turns(self, run_token: str) -> int:
        connection = sqlite3.connect(f"file:{self._path.as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM chat_turns WHERE run_token = ?", (run_token,)
            ).fetchone()
        finally:
            connection.close()
        return int(row[0]) if row else 0

    def clear_history(self, run_token: str) -> int:
        """Delete every stored turn for one run; returns how many were removed."""

        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM chat_turns WHERE run_token = ?", (run_token,)
            )
            return int(cursor.rowcount)

    def set_preference(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not _PREFERENCE_KEY_PATTERN.fullmatch(key):
            raise ChatMemoryError("preference key must match [a-z0-9_-]{1,64}")
        if _SECRETISH_KEY.search(key):
            raise ChatMemoryError("preference keys must never hold secrets")
        if not isinstance(value, str) or not value or len(value) > _PREFERENCE_VALUE_MAX:
            raise ChatMemoryError(f"preference value must be 1..{_PREFERENCE_VALUE_MAX} characters")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, value, datetime.now(UTC).isoformat(timespec="seconds")),
            )

    def preferences(self) -> dict[str, str]:
        connection = sqlite3.connect(f"file:{self._path.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT key, value FROM preferences ORDER BY key").fetchall()
        finally:
            connection.close()
        return {key: value for key, value in rows}
