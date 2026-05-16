from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import create_agent_proposal
from .commands import find_interactive_hints, find_risky_commands, split_config_commands
from .connections import list_serial_ports
from .db import Database
from .jobs import JobService
from .schemas import AgentRequest, DeviceCreate, PushConfigRequest
from .security import SecretBox
from .settings import get_settings


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

settings = get_settings()
db = Database(settings.resolved_database_path)
secrets = SecretBox.from_file(settings.resolved_secret_key_file)
jobs = JobService(db, secrets, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_session_log_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()
    db.recover_open_jobs()
    jobs.start()
    try:
        yield
    finally:
        jobs.stop()


app = FastAPI(title="Cisco Config Tool", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/serial-ports")
def serial_ports() -> list[dict[str, str]]:
    return list_serial_ports()


@app.get("/api/devices")
def list_devices() -> list[dict[str, Any]]:
    rows = db.query_all("SELECT * FROM devices ORDER BY name COLLATE NOCASE")
    return [_public_device(row) for row in rows]


@app.post("/api/devices", status_code=201)
def create_device(payload: DeviceCreate) -> dict[str, Any]:
    device_id = db.execute(
        """
        INSERT INTO devices (
            name, host, platform, connection_type, ssh_port, username,
            password, secret, serial_port, baud_rate, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.host,
            payload.platform,
            payload.connection_type,
            payload.ssh_port,
            payload.username,
            secrets.encrypt(payload.password),
            secrets.encrypt(payload.secret),
            payload.serial_port,
            payload.baud_rate,
            payload.notes,
        ),
    )
    return _get_public_device(device_id)


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int) -> dict[str, bool]:
    _require_device(device_id)
    db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    return {"ok": True}


@app.post("/api/devices/{device_id}/jobs/test")
def test_connection(device_id: int) -> dict[str, Any]:
    _require_device(device_id)
    job_id = jobs.enqueue(device_id, "test_connection")
    return _get_job(job_id)


@app.post("/api/devices/{device_id}/jobs/backup")
def backup_running_config(device_id: int) -> dict[str, Any]:
    _require_device(device_id)
    job_id = jobs.enqueue(device_id, "backup_running_config")
    return _get_job(job_id)


@app.post("/api/devices/{device_id}/jobs/push")
def push_config(device_id: int, payload: PushConfigRequest) -> dict[str, Any]:
    _require_device(device_id)
    commands = split_config_commands(payload.config)
    if not commands:
        raise HTTPException(status_code=422, detail="No config commands to send.")

    risky = find_risky_commands(commands)
    if risky and not payload.allow_risky:
        raise HTTPException(status_code=422, detail=f"Risky command blocked: {', '.join(risky)}")

    job_id = jobs.enqueue(
        device_id,
        "push_config",
        {
            "config": payload.config,
            "save": payload.save,
            "backup_first": payload.backup_first,
            "allow_risky": payload.allow_risky,
            "name": payload.name,
        },
    )
    job = _get_job(job_id)
    job["warnings"] = {"interactive_hints": find_interactive_hints(commands)}
    return job


@app.post("/api/config/validate")
def validate_config(payload: PushConfigRequest) -> dict[str, Any]:
    commands = split_config_commands(payload.config)
    return {
        "command_count": len(commands),
        "risky_commands": find_risky_commands(commands),
        "interactive_hints": find_interactive_hints(commands),
    }


@app.post("/api/agent/propose")
def propose_config(payload: AgentRequest) -> dict[str, Any]:
    devices = _agent_devices(payload.device_ids)
    proposal = create_agent_proposal(
        settings=settings,
        intent=payload.intent,
        devices=devices,
        topology_notes=payload.topology_notes,
        prefer_offline=payload.prefer_offline,
    )
    return proposal.model_dump()


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    rows = db.query_all(
        """
        SELECT jobs.*, devices.name AS device_name
        FROM jobs
        LEFT JOIN devices ON devices.id = jobs.device_id
        ORDER BY jobs.id DESC
        LIMIT 80
        """
    )
    return [_public_job(row) for row in rows]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict[str, Any]:
    job = _get_job(job_id)
    job["logs"] = db.query_all(
        "SELECT id, level, message, created_at FROM job_logs WHERE job_id = ? ORDER BY id",
        (job_id,),
    )
    return job


@app.get("/api/backups")
def list_backups() -> list[dict[str, Any]]:
    return db.query_all(
        """
        SELECT backups.id, backups.device_id, backups.job_id, backups.name,
               backups.created_at, length(backups.content) AS chars,
               devices.name AS device_name
        FROM backups
        LEFT JOIN devices ON devices.id = backups.device_id
        ORDER BY backups.id DESC
        LIMIT 80
        """
    )


@app.get("/api/backups/{backup_id}")
def get_backup(backup_id: int) -> dict[str, Any]:
    backup = db.query_one(
        """
        SELECT backups.*, devices.name AS device_name
        FROM backups
        LEFT JOIN devices ON devices.id = backups.device_id
        WHERE backups.id = ?
        """,
        (backup_id,),
    )
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found.")
    return backup


def _require_device(device_id: int) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    return row


def _get_public_device(device_id: int) -> dict[str, Any]:
    return _public_device(_require_device(device_id))


def _public_device(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "host": row["host"],
        "platform": row["platform"],
        "connection_type": row["connection_type"],
        "ssh_port": row["ssh_port"],
        "username": row["username"],
        "has_password": bool(row["password"]),
        "has_secret": bool(row["secret"]),
        "serial_port": row["serial_port"],
        "baud_rate": row["baud_rate"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _agent_devices(device_ids: list[int]) -> list[dict[str, Any]]:
    if not device_ids:
        return []
    placeholders = ",".join("?" for _ in device_ids)
    rows = db.query_all(f"SELECT * FROM devices WHERE id IN ({placeholders})", device_ids)
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "host": row["host"],
            "platform": row["platform"],
            "connection_type": row["connection_type"],
            "ssh_port": row["ssh_port"],
            "serial_port": row["serial_port"],
            "baud_rate": row["baud_rate"],
            "notes": row["notes"],
        }
        for row in rows
    ]


def _get_job(job_id: int) -> dict[str, Any]:
    row = db.query_one(
        """
        SELECT jobs.*, devices.name AS device_name
        FROM jobs
        LEFT JOIN devices ON devices.id = jobs.device_id
        WHERE jobs.id = ?
        """,
        (job_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _public_job(row)


def _public_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "device_name": row.get("device_name"),
        "type": row["type"],
        "status": row["status"],
        "payload": _json_object(row["payload"]),
        "result": _json_object(row["result"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> None:
    import uvicorn

    uvicorn.run("cisco_config_tool.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
