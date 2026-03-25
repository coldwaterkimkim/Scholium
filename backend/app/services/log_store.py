from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import PROJECT_ROOT, AppSettings, get_settings
from app.models.logs import InteractionEventType, InteractionLogRecord, InteractionLogRequest


class LogStore:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = (PROJECT_ROOT / self.settings.document_db_path).resolve()

    def init_store(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        event_type_values = ", ".join(f"'{member.value}'" for member in InteractionEventType)

        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS interaction_logs (
                    event_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    anchor_id TEXT NULL,
                    event_type TEXT NOT NULL CHECK (event_type IN ({event_type_values})),
                    timestamp TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_interaction_logs_document_page_time
                ON interaction_logs(document_id, page_number, timestamp DESC);
                """
            )

    def append_log(self, payload: InteractionLogRequest) -> InteractionLogRecord:
        self.init_store()

        record = InteractionLogRecord(
            event_id=f"evt_{uuid4().hex}",
            document_id=payload.document_id,
            page_number=payload.page_number,
            anchor_id=payload.anchor_id,
            event_type=payload.event_type,
            timestamp=datetime.now(timezone.utc),
        )

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interaction_logs (
                    event_id,
                    document_id,
                    page_number,
                    anchor_id,
                    event_type,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.document_id,
                    record.page_number,
                    record.anchor_id,
                    record.event_type.value,
                    record.timestamp.isoformat(),
                ),
            )

        return record

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def get_log_store() -> LogStore:
    return LogStore()


def init_log_store() -> None:
    get_log_store().init_store()
