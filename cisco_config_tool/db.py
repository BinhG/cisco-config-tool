from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'cisco_ios',
                    connection_type TEXT NOT NULL CHECK (connection_type IN ('ssh', 'serial')),
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    username TEXT NOT NULL DEFAULT '',
                    password TEXT NOT NULL DEFAULT '',
                    secret TEXT NOT NULL DEFAULT '',
                    serial_port TEXT NOT NULL DEFAULT '',
                    baud_rate INTEGER NOT NULL DEFAULT 9600,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
                    payload TEXT NOT NULL DEFAULT '{}',
                    result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    level TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER NOT NULL,
                    job_id INTEGER,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (device_id) REFERENCES devices(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
                );

                CREATE TRIGGER IF NOT EXISTS devices_updated_at
                AFTER UPDATE ON devices
                BEGIN
                    UPDATE devices SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END;
                """
            )

    def recover_open_jobs(self) -> None:
        self.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error = 'Application restarted before this job finished.',
                finished_at = CURRENT_TIMESTAMP
            WHERE status IN ('queued', 'running')
            """
        )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(sql, tuple(params))
                conn.commit()
                return int(cursor.lastrowid or 0)

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(sql, tuple(params)).fetchone()
                return dict(row) if row is not None else None

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
                return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
