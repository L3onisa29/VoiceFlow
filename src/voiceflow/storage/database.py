from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from voiceflow.config import HistoryConfig

SCHEMA_VERSION = 1
MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE dictionary_entries (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('preferred_term', 'replacement')),
            spoken_form TEXT NOT NULL,
            written_form TEXT NOT NULL,
            language TEXT,
            app_bundle_id TEXT,
            priority INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            use_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """,
        """
        CREATE INDEX dictionary_scope
        ON dictionary_entries(enabled, language, app_bundle_id, priority)
        """,
        """
        CREATE TABLE transcriptions (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            raw_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            final_text TEXT NOT NULL,
            corrected_text TEXT,
            language TEXT,
            route TEXT,
            reason_code TEXT,
            fallback_code TEXT,
            confidence_proxy REAL,
            audio_seconds REAL NOT NULL,
            stt_seconds REAL,
            normalization_seconds REAL,
            postprocess_seconds REAL,
            total_seconds REAL,
            delivery_status TEXT NOT NULL,
            error_code TEXT,
            app_bundle_id TEXT
        )
        """,
        "CREATE INDEX transcription_created ON transcriptions(created_at DESC)",
    )
}


class VoiceBudStorage:
    def __init__(
        self,
        path: Path,
        *,
        history: HistoryConfig,
        clock: Callable[[], float] = time.time,
        migrations: dict[int, tuple[str, ...]] | None = None,
    ) -> None:
        self.path = path.expanduser()
        self.history = history
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._migrations = MIGRATIONS if migrations is None else migrations
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._connection = sqlite3.connect(
            self.path,
            timeout=5,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._restrict_files()
            self._configure()
            self._migrate()
            self.prune_history()
            self._restrict_files()
        except Exception:
            self._connection.close()
            self._restrict_files()
            raise

    def _configure(self) -> None:
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA journal_mode = WAL")

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {current} is newer than supported {SCHEMA_VERSION}"
                )
            for version in range(current + 1, SCHEMA_VERSION + 1):
                statements = self._migrations.get(version)
                if not statements:
                    raise RuntimeError(f"Missing database migration {version}")
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in statements:
                        self._connection.execute(statement)
                    self._connection.execute(f"PRAGMA user_version = {version}")
                except Exception:
                    self._connection.execute("ROLLBACK")
                    raise
                else:
                    self._connection.execute("COMMIT")

    def _restrict_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            if candidate.exists():
                os.chmod(candidate, 0o600)

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            if self._closed:
                raise RuntimeError("Storage is closed")
            cursor = self._connection.execute(sql, parameters)
            self._restrict_files()
            return cursor

    def query(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Storage is closed")
            return list(self._connection.execute(sql, parameters))

    def add_dictionary_entry(self, values: Sequence[Any]) -> None:
        self.execute(
            """
            INSERT INTO dictionary_entries (
                id, kind, spoken_form, written_form, language, app_bundle_id,
                priority, enabled, use_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )

    def dictionary_rows(self) -> list[sqlite3.Row]:
        return self.query(
            """
            SELECT id, kind, spoken_form, written_form, language, app_bundle_id,
                   priority, enabled, use_count, created_at, updated_at
            FROM dictionary_entries
            ORDER BY created_at, id
            """
        )

    def increment_dictionary_usage(self, identifiers: Iterable[str]) -> None:
        ids = tuple(dict.fromkeys(identifiers))
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        self.execute(
            f"""
            UPDATE dictionary_entries
            SET use_count = use_count + 1, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (self._clock(), *ids),
        )

    def save_transcription(self, values: Sequence[Any]) -> None:
        self.execute(
            """
            INSERT INTO transcriptions (
                id, created_at, raw_text, normalized_text, final_text, corrected_text,
                language, route, reason_code, fallback_code, confidence_proxy,
                audio_seconds, stt_seconds, normalization_seconds, postprocess_seconds,
                total_seconds, delivery_status, error_code, app_bundle_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        self.prune_history()

    def prune_history(self) -> None:
        cutoff = self._clock() - self.history.retention_hours * 3600
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "DELETE FROM transcriptions WHERE created_at < ?", (cutoff,)
                )
                self._connection.execute(
                    """
                    DELETE FROM transcriptions
                    WHERE id NOT IN (
                        SELECT id FROM transcriptions
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                    )
                    """,
                    (self.history.max_entries,),
                )
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    def save_correction(self, identifier: str, corrected_text: str) -> bool:
        cursor = self.execute(
            "UPDATE transcriptions SET corrected_text = ? WHERE id = ?",
            (corrected_text, identifier),
        )
        return cursor.rowcount > 0

    def clear_history(self) -> None:
        self.execute("DELETE FROM transcriptions")

    def transcription_count(self) -> int:
        return int(self.query("SELECT COUNT(*) AS count FROM transcriptions")[0]["count"])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()
