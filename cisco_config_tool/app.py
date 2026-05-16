from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .agent import create_agent_proposal
from .commands import find_interactive_hints, find_risky_commands, split_config_commands, terminal_script_from_config
from .connections import list_serial_ports
from .db import Database
from .device_context import collect_device_context, summarize_context_for_ai
from .jobs import JobService
from .schemas import AdvisorMessageCreate, AdvisorSessionCreate, AgentRequest, DeviceCreate, PushConfigRequest
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


@app.post("/api/advisor/sessions", status_code=201)
def create_advisor_session(payload: AdvisorSessionCreate) -> dict[str, Any]:
    if payload.device_id is not None:
        _require_device(payload.device_id)
    session_id = db.execute(
        """
        INSERT INTO advisor_sessions (device_id, title, topology_notes)
        VALUES (?, ?, ?)
        """,
        (payload.device_id, payload.title or "Advisor session", payload.topology_notes),
    )
    return _get_advisor_session(session_id)


@app.get("/api/advisor/sessions")
def list_advisor_sessions() -> list[dict[str, Any]]:
    rows = db.query_all(
        """
        SELECT advisor_sessions.*, devices.name AS device_name
        FROM advisor_sessions
        LEFT JOIN devices ON devices.id = advisor_sessions.device_id
        ORDER BY advisor_sessions.id DESC
        LIMIT 50
        """
    )
    return [_public_advisor_session(row) for row in rows]


@app.get("/api/advisor/sessions/{session_id}")
def get_advisor_session(session_id: int) -> dict[str, Any]:
    session = _get_advisor_session(session_id)
    session["messages"] = [
        _public_advisor_message(row)
        for row in db.query_all(
            """
            SELECT * FROM advisor_messages
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        )
    ]
    return session


@app.post("/api/advisor/sessions/{session_id}/messages")
def send_advisor_message(session_id: int, payload: AdvisorMessageCreate) -> dict[str, Any]:
    session = _require_advisor_session(session_id)
    db.execute(
        "INSERT INTO advisor_messages (session_id, role, content) VALUES (?, 'user', ?)",
        (session_id, payload.message),
    )
    db.execute("UPDATE advisor_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))

    history_rows = db.query_all(
        """
        SELECT role, content
        FROM advisor_messages
        WHERE session_id = ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (session_id,),
    )
    history = list(reversed(history_rows))
    context_summary = ""
    warnings: list[str] = []
    device = None
    if session["device_id"] is not None:
        device = _require_device(int(session["device_id"]))

    if payload.auto_collect and device is not None:
        try:
            context = collect_device_context(
                device=device,
                secrets=secrets,
                settings=settings,
                commands_text=payload.commands_text,
                include_running_config=True,
                max_output_chars=60000,
            )
            context_summary = summarize_context_for_ai(context)
            if not context.get("ok"):
                warnings.append("Không collect được context thiết bị: " + "; ".join(context.get("errors", [])))
        except Exception as exc:
            warnings.append(f"Không connect được thiết bị để collect context: {exc}")
            context_summary = f"Device context collection failed: {exc}"

    advisor_intent = _build_advisor_intent(
        latest_message=payload.message,
        history=history,
        context_summary=context_summary,
    )
    devices = _agent_devices([int(session["device_id"])]) if session["device_id"] is not None else []
    proposal = create_agent_proposal(
        settings=settings,
        intent=advisor_intent,
        devices=devices,
        topology_notes=session["topology_notes"],
        prefer_offline=payload.prefer_offline,
    )
    for warning in warnings:
        proposal.warnings.append(warning)

    assistant_text = _advisor_text_from_proposal(proposal.model_dump())
    proposal_json = proposal.model_dump()
    proposal_json["terminal_script"] = terminal_script_from_config(proposal.config)
    message_id = db.execute(
        """
        INSERT INTO advisor_messages (session_id, role, content, proposal, context_summary)
        VALUES (?, 'assistant', ?, ?, ?)
        """,
        (session_id, assistant_text, json.dumps(proposal_json, ensure_ascii=False), context_summary[:12000]),
    )
    db.execute("UPDATE advisor_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
    return {
        "session": _get_advisor_session(session_id),
        "assistant_message": _public_advisor_message(_require_advisor_message(message_id)),
    }


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


def _require_advisor_session(session_id: int) -> dict[str, Any]:
    row = db.query_one(
        """
        SELECT advisor_sessions.*, devices.name AS device_name
        FROM advisor_sessions
        LEFT JOIN devices ON devices.id = advisor_sessions.device_id
        WHERE advisor_sessions.id = ?
        """,
        (session_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Advisor session not found.")
    return row


def _get_advisor_session(session_id: int) -> dict[str, Any]:
    return _public_advisor_session(_require_advisor_session(session_id))


def _public_advisor_session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "device_id": row["device_id"],
        "device_name": row.get("device_name"),
        "title": row["title"],
        "topology_notes": row["topology_notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _require_advisor_message(message_id: int) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM advisor_messages WHERE id = ?", (message_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Advisor message not found.")
    return row


def _public_advisor_message(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "proposal": _json_object(row["proposal"]),
        "has_context": bool(row["context_summary"]),
        "created_at": row["created_at"],
    }


def _build_advisor_intent(
    *,
    latest_message: str,
    history: list[dict[str, Any]],
    context_summary: str,
) -> str:
    history_text = "\n".join(f"{row['role']}: {row['content']}" for row in history)
    parts = [
        "Bạn đang là quân sư cấu hình mạng Cisco trong một hội thoại nhiều lượt.",
        "Nếu thiếu thông tin, hãy hỏi lại trước khi cam kết config.",
        "Khi đủ thông tin, hãy đề xuất config kèm giải thích, precheck, verify và rollback.",
        "",
        "Conversation history:",
        history_text,
        "",
        "Latest user message:",
        latest_message,
    ]
    if context_summary:
        parts.extend(["", "Current device context collected by read-only show commands:", context_summary])
    return "\n".join(parts)


def _advisor_text_from_proposal(proposal: dict[str, Any]) -> str:
    lines = [proposal.get("plain_language_summary") or proposal.get("title") or "Đề xuất cấu hình"]
    questions = proposal.get("questions") or []
    if questions:
        lines.append("\nCần bạn xác nhận:")
        lines.extend(f"- {item}" for item in questions)
    warnings = proposal.get("warnings") or []
    if warnings:
        lines.append("\nCảnh báo:")
        lines.extend(f"- {item}" for item in warnings)
    config = proposal.get("config") or ""
    if config:
        lines.append("\nConfig đề xuất đã sẵn sàng để chuyển thành terminal script.")
    return "\n".join(lines)


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
