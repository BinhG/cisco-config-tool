from __future__ import annotations

import re


RISKY_COMMAND_PATTERNS = [
    re.compile(r"^\s*reload\b", re.IGNORECASE),
    re.compile(r"^\s*write\s+erase\b", re.IGNORECASE),
    re.compile(r"^\s*erase\s+startup-config\b", re.IGNORECASE),
    re.compile(r"^\s*format\b", re.IGNORECASE),
    re.compile(r"^\s*delete\s+/?(?:recursive\s+)?(?:flash|bootflash|nvram):", re.IGNORECASE),
    re.compile(r"^\s*no\s+username\b", re.IGNORECASE),
]

INTERACTIVE_HINT_PATTERNS = [
    re.compile(r"^\s*banner\s+", re.IGNORECASE),
    re.compile(r"^\s*copy\s+", re.IGNORECASE),
    re.compile(r"^\s*crypto\s+key\s+generate\b", re.IGNORECASE),
]

DISALLOWED_SHOW_PATTERNS = [
    re.compile(r"^\s*show\s+tech-support\b", re.IGNORECASE),
    re.compile(r"^\s*show\s+platform\b.*\btrace\b", re.IGNORECASE),
    re.compile(r"^\s*show\s+logging\b", re.IGNORECASE),
]

SENSITIVE_CONFIG_PATTERNS = [
    re.compile(r"^(\s*enable\s+(?:secret|password)(?:\s+\d+)?\s+).+$", re.IGNORECASE),
    re.compile(r"^(\s*username\s+\S+.*\s+(?:secret|password)(?:\s+\d+)?\s+).+$", re.IGNORECASE),
    re.compile(r"^(\s*snmp-server\s+community\s+)\S+(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(\s*tacacs-server\s+key(?:\s+\d+)?\s+).+$", re.IGNORECASE),
    re.compile(r"^(\s*radius-server\s+key(?:\s+\d+)?\s+).+$", re.IGNORECASE),
    re.compile(r"^(\s*wpa-psk\s+ascii\s+).+$", re.IGNORECASE),
    re.compile(r"^(\s*crypto\s+isakmp\s+key\s+)\S+(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(\s*ntp\s+authentication-key\s+\d+\s+md5\s+).+$", re.IGNORECASE),
]

DEFAULT_DISCOVERY_COMMANDS = [
    "show version",
    "show running-config",
    "show ip interface brief",
    "show interfaces status",
    "show vlan brief",
    "show interfaces trunk",
    "show cdp neighbors detail",
    "show lldp neighbors detail",
    "show spanning-tree summary",
    "show etherchannel summary",
]


def split_config_commands(config: str) -> list[str]:
    commands: list[str] = []
    for raw_line in config.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("!") or line.startswith("#"):
            continue
        commands.append(line)
    return commands


def find_risky_commands(commands: list[str]) -> list[str]:
    risky: list[str] = []
    for command in commands:
        if any(pattern.search(command) for pattern in RISKY_COMMAND_PATTERNS):
            risky.append(command)
    return risky


def find_interactive_hints(commands: list[str]) -> list[str]:
    hints: list[str] = []
    for command in commands:
        if any(pattern.search(command) for pattern in INTERACTIVE_HINT_PATTERNS):
            hints.append(command)
    return hints


def split_show_commands(commands_text: str) -> list[str]:
    commands: list[str] = []
    for raw_line in commands_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!") or line.startswith("#"):
            continue
        commands.append(line)
    return commands


def validate_show_commands(commands: list[str]) -> list[str]:
    errors: list[str] = []
    for command in commands:
        lowered = command.lower().strip()
        if not lowered.startswith("show "):
            errors.append(f"Only show commands are allowed over MCP read-only collection: {command}")
            continue
        if len(command) > 180:
            errors.append(f"Show command is too long: {command}")
        if any(pattern.search(command) for pattern in DISALLOWED_SHOW_PATTERNS):
            errors.append(f"Show command is blocked because it can be too large or sensitive: {command}")
    return errors


def mask_sensitive_config(text: str) -> str:
    masked_lines: list[str] = []
    for line in text.splitlines():
        masked = line
        for pattern in SENSITIVE_CONFIG_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            if len(match.groups()) >= 2 and match.group(2) is not None:
                masked = f"{match.group(1)}<redacted>{match.group(2)}"
            else:
                masked = f"{match.group(1)}<redacted>"
            break
        masked_lines.append(masked)
    return "\n".join(masked_lines)


def terminal_script_from_config(config: str) -> str:
    commands = split_config_commands(config)
    if not commands:
        return ""
    return "\n".join(["configure terminal", *commands, "end"])
