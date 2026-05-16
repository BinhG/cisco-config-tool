from __future__ import annotations

import json
import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - startup guard
    raise RuntimeError("Install MCP dependencies first: uv sync --extra dev") from exc

from .agent import create_agent_proposal
from .commands import (
    find_interactive_hints,
    find_risky_commands,
    split_config_commands,
    terminal_script_from_config,
)
from .db import Database
from .device_context import collect_device_context, summarize_context_for_ai
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
    device = _device_for_connection(device_id)
    return _json(
        collect_device_context(
            device=device,
            secrets=secrets,
            settings=settings,
            commands_text=commands_text,
            include_running_config=include_running_config,
            max_output_chars=max_output_chars,
        )
    )


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

    analysis_intent = (
        f"{intent}\n\nCurrent device context from read-only MCP collection:\n"
        f"{summarize_context_for_ai(collected)}"
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
def cisco_explain_config(config: str, detail_level: str = "beginner") -> str:
    """
    Explain Cisco IOS/IOS-XE configuration in Vietnamese for non-CCNA users.

    Args:
        config: Running-config or a config snippet. Secrets should already be redacted.
        detail_level: "beginner", "normal", or "expert".
    """
    intent = (
        "Giải thích config Cisco sau bằng tiếng Việt, ưu tiên người không biết CCNA. "
        f"Mức chi tiết: {detail_level}. Nêu mục đích từng khối, rủi ro, và phần nào không nên sửa.\n\n"
        f"{config[:50000]}"
    )
    proposal = create_agent_proposal(
        settings=settings,
        intent=intent,
        devices=[],
        topology_notes="",
        prefer_offline=False,
    )
    return _json(proposal.model_dump())


@mcp.tool()
def cisco_compare_config(
    running_config: str,
    desired_config: str,
    intent: str = "So sánh running-config với desired config",
) -> str:
    """
    Compare current Cisco config with a desired baseline or proposed config.

    Returns Vietnamese explanation, risk notes, and next checks. Does not push.
    """
    compare_intent = (
        f"{intent}\n\n"
        "Hãy so sánh config hiện tại và config mong muốn. Nêu điểm khác nhau quan trọng, "
        "rủi ro, missing items, lệnh cần kiểm tra trước khi áp dụng, và rollback.\n\n"
        "RUNNING CONFIG:\n"
        f"{running_config[:30000]}\n\n"
        "DESIRED CONFIG:\n"
        f"{desired_config[:30000]}"
    )
    proposal = create_agent_proposal(
        settings=settings,
        intent=compare_intent,
        devices=[],
        topology_notes="",
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
