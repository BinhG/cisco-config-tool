from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .commands import DEFAULT_DISCOVERY_COMMANDS, mask_sensitive_config, split_show_commands, validate_show_commands
from .connections import CiscoConnection
from .security import SecretBox
from .settings import Settings


def collect_device_context(
    *,
    device: dict[str, Any],
    secrets: SecretBox,
    settings: Settings,
    commands_text: str = "",
    include_running_config: bool = True,
    max_output_chars: int = 60000,
) -> dict[str, Any]:
    commands = split_show_commands(commands_text) if commands_text.strip() else list(DEFAULT_DISCOVERY_COMMANDS)
    if not include_running_config:
        commands = [
            cmd
            for cmd in commands
            if "running-config" not in cmd.lower() and cmd.lower() not in {"show run", "show running"}
        ]

    errors = validate_show_commands(commands)
    if errors:
        return {"ok": False, "errors": errors, "outputs": {}, "commands": commands}

    password = secrets.decrypt(device.get("password"))
    secret = secrets.decrypt(device.get("secret"))
    session_log_path = _session_log_path(settings, int(device["id"]))
    logs: list[dict[str, str]] = []

    def log(message: str, level: str = "info") -> None:
        logs.append({"level": level, "message": message})

    with CiscoConnection(device, password, secret, session_log_path, log) as conn:
        outputs = conn.run_show_commands(commands)

    masked_outputs = _mask_outputs(outputs, include_running_config)
    result = {
        "ok": True,
        "device": _public_context_device(device),
        "commands": commands,
        "outputs": masked_outputs,
        "session_log": str(session_log_path),
        "logs": logs,
        "policy": "read-only show commands only; running-config secrets redacted",
    }
    text = str(result)
    if len(text) > max_output_chars:
        result["truncated"] = True
        result["outputs"] = {"truncated": text[:max_output_chars]}
    return result


def summarize_context_for_ai(context: dict[str, Any], max_chars: int = 50000) -> str:
    if not context.get("ok"):
        return f"Device context collection failed: {context.get('errors') or context.get('error')}"
    compact = {
        "device": context.get("device"),
        "commands": context.get("commands"),
        "outputs": context.get("outputs"),
        "policy": context.get("policy"),
    }
    import json

    return json.dumps(compact, ensure_ascii=False, indent=2)[:max_chars]


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


def _session_log_path(settings: Settings, device_id: int) -> Path:
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return settings.resolved_session_log_dir / f"mcp_read_device_{device_id}_{timestamp}.log"


def _public_context_device(row: dict[str, Any]) -> dict[str, Any]:
    return {
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
