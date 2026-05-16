from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - startup guard
    raise RuntimeError("Install MCP dependencies first: uv sync --extra dev") from exc

from .agent import create_agent_proposal
from .commands import (
    DEFAULT_DISCOVERY_COMMANDS,
    find_interactive_hints,
    find_risky_commands,
    mask_sensitive_config,
    split_config_commands,
    split_show_commands,
    terminal_script_from_config,
    validate_show_commands,
)
from .connections import CiscoConnection
from .db import Database
from .security import SecretBox
from .settings import get_settings


settings = get_settings()
db = Database(settings.resolved_database_path)
secrets = SecretBox.from_file(settings.resolved_secret_key_file)
mcp = FastMCP("cisco-config-assistant")


def _initialize() -> None:
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _parse_device_ids(device_ids_json: str) -> list[int]:
    if not device_ids_json.strip():
        return []
    try:
        value = json.loads(device_ids_json)
    except json.JSONDecodeError as exc:
        raise ValueError("device_ids_json must be a JSON array of device IDs.") from exc
    if not isinstance(value, list):
        raise ValueError("device_ids_json must be a JSON array.")
    ids: list[int] = []
    for item in value:
        ids.append(int(item))
    return ids


def _safe_devices(device_ids: list[int] | None = None) -> list[dict[str, Any]]:
    if device_ids:
        placeholders = ",".join("?" for _ in device_ids)
        rows = db.query_all(f"SELECT * FROM devices WHERE id IN ({placeholders})", device_ids)
    else:
        rows = db.query_all("SELECT * FROM devices ORDER BY name COLLATE NOCASE")
    return [
        {
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
        for row in rows
    ]


def _device_for_connection(device_id: int) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
    if row is None:
        raise ValueError(f"Device not found: {device_id}")
    return row


def _session_log_path(device_id: int) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return settings.resolved_session_log_dir / f"mcp_read_device_{device_id}_{timestamp}.log"


def _mask_outputs(outputs: dict[str, str], include_running_config: bool) -> dict[str, str]:
    masked: dict[str, str] = {}
    for command, output in outputs.items():
        lowered = command.lower()
        if "running-config" in lowered or lowered in {"show run", "show running"}:
            if include_running_config:
                masked[command] = mask_sensitive_config(output)
            else:
                masked[command] = "<running-config omitted; call with include_running_config=true to return redacted config>"
        else:
            masked[command] = output
    return masked


@mcp.tool()
def cisco_network_status() -> str:
    """
    Return Cisco Config Tool MCP status. Read-only; no device connection is opened.
    """
    _initialize()
    device_count = db.query_one("SELECT count(*) AS count FROM devices") or {"count": 0}
    backup_count = db.query_one("SELECT count(*) AS count FROM backups") or {"count": 0}
    return _json(
        {
            "status": "ok",
            "server": "cisco-config-assistant",
            "database": str(settings.resolved_database_path),
            "device_count": device_count["count"],
            "backup_count": backup_count["count"],
            "openai_model": settings.openai_model,
            "openai_configured": bool(settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")),
            "policy": {
                "mcp_can_push_config": False,
                "mcp_can_run_show_commands": True,
                "mcp_surface": "read_only_collection_and_proposal_only",
                "execution_path": "Copy terminal-ready script manually; MCP will not enter config mode.",
            },
        }
    )


@mcp.tool()
def cisco_list_devices() -> str:
    """
    List configured Cisco devices without exposing passwords or enable secrets.
    Read-only; no device connection is opened.
    """
    _initialize()
    return _json({"devices": _safe_devices()})


@mcp.tool()
def cisco_propose_config(
    intent: str,
    device_ids_json: str = "[]",
    topology_notes: str = "",
    prefer_offline: bool = False,
) -> str:
    """
    Create a Cisco IOS/IOS-XE config proposal in Vietnamese.

    The proposal includes assumptions, questions, risk notes, prechecks, config,
    verification commands, and rollback commands. This tool does not connect to
    devices and does not push config.

    Args:
        intent: Natural language goal, e.g. "tạo VLAN 20 tên Camera cho Gi1/0/5-10".
        device_ids_json: JSON array of device IDs from cisco_list_devices, e.g. "[1, 2]".
        topology_notes: Optional topology notes such as uplinks, firewall links, and forbidden ports.
        prefer_offline: Force deterministic offline templates instead of cloud AI.
    """
    _initialize()
    device_ids = _parse_device_ids(device_ids_json)
    devices = _safe_devices(device_ids)
    proposal = create_agent_proposal(
        settings=settings,
        intent=intent,
        devices=devices,
        topology_notes=topology_notes,
        prefer_offline=prefer_offline,
    )
    return _json(proposal.model_dump())


@mcp.tool()
def cisco_validate_config(config: str) -> str:
    """
    Validate proposed Cisco config for risky or interactive commands.
    Read-only; no device connection is opened.
    """
    _initialize()
    commands = split_config_commands(config)
    risky = find_risky_commands(commands)
    interactive = find_interactive_hints(commands)
    return _json(
        {
            "command_count": len(commands),
            "risky_commands": risky,
            "interactive_hints": interactive,
            "safe_to_stage_for_human_review": not risky,
            "recommendation": (
                "Blocked: remove risky commands before staging."
                if risky
                else "No blocked command detected. Still require human review before push."
            ),
        }
    )


@mcp.tool()
def cisco_collect_device_info(
    device_id: int,
    commands_text: str = "",
    include_running_config: bool = True,
    max_output_chars: int = 60000,
) -> str:
    """
    Connect to a configured Cisco device and collect read-only show-command output.

    This tool only permits commands starting with "show ". It never enters config
    mode and never pushes changes. Running-config is returned with common secrets
    redacted.

    Args:
        device_id: Device ID from cisco_list_devices.
        commands_text: Newline-separated show commands. Empty uses a safe discovery set.
        include_running_config: Include redacted show running-config output.
        max_output_chars: Truncate combined output to this limit.
    """
    _initialize()
    commands = split_show_commands(commands_text) if commands_text.strip() else list(DEFAULT_DISCOVERY_COMMANDS)
    if not include_running_config:
        commands = [cmd for cmd in commands if "running-config" not in cmd.lower() and cmd.lower() not in {"show run", "show running"}]
    errors = validate_show_commands(commands)
    if errors:
        return _json({"ok": False, "errors": errors, "outputs": {}})

    device = _device_for_connection(device_id)
    password = secrets.decrypt(device.get("password"))
    secret = secrets.decrypt(device.get("secret"))
    session_log_path = _session_log_path(device_id)
    logs: list[dict[str, str]] = []

    def log(message: str, level: str = "info") -> None:
        logs.append({"level": level, "message": message})

    with CiscoConnection(device, password, secret, session_log_path, log) as conn:
        outputs = conn.run_show_commands(commands)

    masked_outputs = _mask_outputs(outputs, include_running_config)
    result = {
        "ok": True,
        "device": _safe_devices([device_id])[0],
        "commands": commands,
        "outputs": masked_outputs,
        "session_log": str(session_log_path),
        "logs": logs,
        "policy": "read-only show commands only; running-config secrets redacted",
    }
    text = _json(result)
    if len(text) > max_output_chars:
        result["truncated"] = True
        result["outputs"] = {"truncated": text[:max_output_chars]}
    return _json(result)


@mcp.tool()
def cisco_collect_and_propose(
    device_id: int,
    intent: str,
    topology_notes: str = "",
    commands_text: str = "",
    prefer_offline: bool = False,
) -> str:
    """
    Collect read-only device context, then create a reviewed config proposal.

    This does not push config. It is intended for Codex/Claude/Antigravity to
    inspect current state before advising a new configuration.
    """
    collected_text = cisco_collect_device_info(
        device_id=device_id,
        commands_text=commands_text,
        include_running_config=True,
        max_output_chars=80000,
    )
    collected = json.loads(collected_text)
    if not collected.get("ok"):
        return collected_text

    summarized = {
        "device": collected.get("device"),
        "commands": collected.get("commands"),
        "outputs": collected.get("outputs"),
    }
    analysis_intent = (
        f"{intent}\n\nCurrent device context from read-only MCP collection:\n"
        f"{json.dumps(summarized, ensure_ascii=False, indent=2)[:50000]}"
    )
    proposal = create_agent_proposal(
        settings=settings,
        intent=analysis_intent,
        devices=[collected["device"]],
        topology_notes=topology_notes,
        prefer_offline=prefer_offline,
    )
    return _json(
        {
            "collection": {
                "device": collected.get("device"),
                "commands": collected.get("commands"),
                "session_log": collected.get("session_log"),
                "policy": collected.get("policy"),
            },
            "proposal": proposal.model_dump(),
        }
    )


@mcp.tool()
def cisco_analyze_show_output(
    intent: str,
    show_outputs_json: str,
    topology_notes: str = "",
) -> str:
    """
    Analyze pasted Cisco show-command outputs and suggest next checks/config direction.

    Args:
        intent: What the user wants to accomplish or troubleshoot.
        show_outputs_json: JSON object where keys are commands and values are outputs.
        topology_notes: Optional topology notes.
    """
    _initialize()
    try:
        outputs = json.loads(show_outputs_json)
    except json.JSONDecodeError as exc:
        raise ValueError("show_outputs_json must be a JSON object.") from exc
    if not isinstance(outputs, dict):
        raise ValueError("show_outputs_json must be a JSON object.")

    summarized = []
    for command, output in outputs.items():
        text = str(output)
        summarized.append(
            {
                "command": str(command),
                "chars": len(text),
                "preview": text[:3000],
            }
        )

    analysis_intent = (
        f"{intent}\n\nDữ liệu show commands đã cung cấp:\n"
        f"{json.dumps(summarized, ensure_ascii=False, indent=2)}"
    )
    proposal = create_agent_proposal(
        settings=settings,
        intent=analysis_intent,
        devices=[],
        topology_notes=topology_notes,
        prefer_offline=False,
    )
    return _json(proposal.model_dump())


@mcp.tool()
def cisco_terminal_script(config: str, include_save_hint: bool = False) -> str:
    """
    Convert proposed config into a terminal-ready paste block.

    This only returns text. It does not connect to the device and does not paste
    into any terminal. The user remains responsible for pasting and pressing
    Enter/Apply.
    """
    commands = split_config_commands(config)
    risky = find_risky_commands(commands)
    interactive = find_interactive_hints(commands)
    if risky:
        return _json(
            {
                "ok": False,
                "errors": ["Risky commands blocked: " + ", ".join(risky)],
                "script": "",
            }
        )
    script = terminal_script_from_config(config)
    if include_save_hint and script:
        script = f"{script}\n! Sau khi verify OK, chạy thủ công: write memory"
    return _json(
        {
            "ok": True,
            "interactive_hints": interactive,
            "script": script,
            "instructions": [
                "Paste block này vào terminal thiết bị sau khi bạn đã kiểm tra lại.",
                "Không paste vào uplink/core nếu chưa chắc chắn tác động.",
                "Verify bằng các lệnh show mà proposal đưa ra trước khi save.",
            ],
        }
    )


@mcp.tool()
def cisco_recent_jobs(limit: int = 20) -> str:
    """
    List recent Cisco tool jobs without exposing stored secrets.
    Read-only; no device connection is opened.
    """
    _initialize()
    limit = max(1, min(int(limit), 100))
    rows = db.query_all(
        """
        SELECT jobs.id, jobs.device_id, devices.name AS device_name, jobs.type,
               jobs.status, jobs.error, jobs.created_at, jobs.started_at, jobs.finished_at
        FROM jobs
        LEFT JOIN devices ON devices.id = jobs.device_id
        ORDER BY jobs.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return _json({"jobs": rows})


@mcp.tool()
def cisco_recent_backups(limit: int = 20) -> str:
    """
    List recent backup metadata. Does not return backup contents.
    Read-only; no device connection is opened.
    """
    _initialize()
    limit = max(1, min(int(limit), 100))
    rows = db.query_all(
        """
        SELECT backups.id, backups.device_id, backups.job_id, backups.name,
               backups.created_at, length(backups.content) AS chars,
               devices.name AS device_name
        FROM backups
        LEFT JOIN devices ON devices.id = backups.device_id
        ORDER BY backups.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return _json({"backups": rows})


if __name__ == "__main__":
    _initialize()
    mcp.run(transport="stdio")
