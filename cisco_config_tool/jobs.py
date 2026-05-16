from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .commands import find_interactive_hints, find_risky_commands, split_config_commands
from .connections import CiscoConnection
from .db import Database
from .security import SecretBox
from .settings import Settings


class JobService:
    def __init__(self, db: Database, secrets: SecretBox, settings: Settings) -> None:
        self.db = db
        self.secrets = secrets
        self.settings = settings
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="cisco-job-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def enqueue(self, device_id: int, job_type: str, payload: dict[str, Any] | None = None) -> int:
        job_id = self.db.execute(
            """
            INSERT INTO jobs (device_id, type, status, payload)
            VALUES (?, ?, 'queued', ?)
            """,
            (device_id, job_type, json.dumps(payload or {}, ensure_ascii=False)),
        )
        self._queue.put(job_id)
        return job_id

    def _worker(self) -> None:
        while not self._stop.is_set():
            job_id = self._queue.get()
            if job_id is None:
                return
            try:
                self._run_job(job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: int) -> None:
        job = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None:
            return

        self._update_job(job_id, status="running", started_at="CURRENT_TIMESTAMP")
        self._log(job_id, "Job started.", "info")

        try:
            device = self.db.query_one("SELECT * FROM devices WHERE id = ?", (job["device_id"],))
            if device is None:
                raise RuntimeError("Device no longer exists.")

            payload = json.loads(job["payload"] or "{}")
            result = self._execute(job_id, job["type"], device, payload)
            self._update_job(
                job_id,
                status="succeeded",
                result=json.dumps(result, ensure_ascii=False),
                error="",
                finished_at="CURRENT_TIMESTAMP",
            )
            self._log(job_id, "Job succeeded.", "info")
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                error=str(exc),
                finished_at="CURRENT_TIMESTAMP",
            )
            self._log(job_id, f"Job failed: {exc}", "error")

    def _execute(
        self,
        job_id: int,
        job_type: str,
        device: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        password = self.secrets.decrypt(device.get("password"))
        secret = self.secrets.decrypt(device.get("secret"))
        session_log_path = self._session_log_path(job_id)

        def log(message: str, level: str = "info") -> None:
            self._log(job_id, message, level)

        with CiscoConnection(device, password, secret, session_log_path, log) as conn:
            if job_type == "test_connection":
                result = conn.test()
                result["session_log"] = conn.session_log
                return result

            if job_type == "backup_running_config":
                content = conn.backup_running_config()
                backup_id = self._store_backup(device["id"], job_id, "running-config", content)
                return {
                    "backup_id": backup_id,
                    "bytes": len(content.encode("utf-8")),
                    "session_log": conn.session_log,
                }

            if job_type == "push_config":
                return self._push_config(job_id, device, payload, conn)

        raise RuntimeError(f"Unsupported job type: {job_type}")

    def _push_config(
        self,
        job_id: int,
        device: dict[str, Any],
        payload: dict[str, Any],
        conn: CiscoConnection,
    ) -> dict[str, Any]:
        commands = split_config_commands(payload.get("config", ""))
        if not commands:
            raise RuntimeError("No config commands to send.")

        risky = find_risky_commands(commands)
        if risky and not payload.get("allow_risky"):
            raise RuntimeError(f"Risky command blocked: {', '.join(risky)}")

        backup_id = None
        if payload.get("backup_first", True):
            backup_content = conn.backup_running_config()
            backup_id = self._store_backup(device["id"], job_id, "pre-change-running-config", backup_content)

        push_result = conn.push_config(commands, save=bool(payload.get("save", False)))
        return {
            **push_result,
            "backup_id": backup_id,
            "command_count": len(commands),
            "interactive_hints": find_interactive_hints(commands),
            "session_log": conn.session_log,
        }

    def _store_backup(self, device_id: int, job_id: int, kind: str, content: str) -> int:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        name = f"{kind}-{timestamp}"
        self._log(job_id, f"Stored backup: {name}.", "info")
        return self.db.execute(
            """
            INSERT INTO backups (device_id, job_id, name, content)
            VALUES (?, ?, ?, ?)
            """,
            (device_id, job_id, name, content),
        )

    def _session_log_path(self, job_id: int) -> Path:
        return self.settings.resolved_session_log_dir / f"job_{job_id:06d}.log"

    def _log(self, job_id: int, message: str, level: str = "info") -> None:
        self.db.execute(
            "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
            (job_id, level, message),
        )

    def _update_job(self, job_id: int, **fields: Any) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            if value == "CURRENT_TIMESTAMP":
                assignments.append(f"{key} = CURRENT_TIMESTAMP")
            else:
                assignments.append(f"{key} = ?")
                values.append(value)
        values.append(job_id)
        self.db.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?", values)
