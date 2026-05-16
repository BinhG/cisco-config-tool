from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


LogFn = Callable[[str, str], None]


def serial_device_type(platform: str) -> str:
    if platform.endswith("_serial"):
        return platform
    if platform == "cisco_ios":
        return "cisco_ios_serial"
    return f"{platform}_serial"


def list_serial_ports() -> list[dict[str, str]]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []

    ports: list[dict[str, str]] = []
    for port in list_ports.comports():
        ports.append(
            {
                "device": port.device,
                "description": port.description or "",
                "hwid": port.hwid or "",
            }
        )
    return ports


class CiscoConnection:
    def __init__(
        self,
        device: dict[str, Any],
        password: str,
        secret: str,
        session_log_path: Path,
        log: LogFn,
    ) -> None:
        self.device = device
        self.password = password
        self.secret = secret
        self.session_log_path = session_log_path
        self.log = log
        self._conn: Any = None

    def __enter__(self) -> "CiscoConnection":
        try:
            from netmiko import ConnectHandler
        except ImportError as exc:
            raise RuntimeError("Netmiko is not installed. Install project dependencies first.") from exc

        self.session_log_path.parent.mkdir(parents=True, exist_ok=True)
        params = self._build_params()
        self.log("Opening device session.", "info")
        self._conn = ConnectHandler(**params)

        if self.secret:
            self.log("Entering privileged mode.", "info")
            self._conn.enable()

        try:
            self._conn.disable_paging()
        except Exception as exc:  # pragma: no cover - device-specific behavior
            self.log(f"Could not disable paging: {exc}", "warning")

        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._conn is not None:
            try:
                self._conn.disconnect()
            finally:
                self.log("Device session closed.", "info")

    @property
    def session_log(self) -> str:
        return str(self.session_log_path)

    def test(self) -> dict[str, str]:
        prompt = self._conn.find_prompt()
        output = self._conn.send_command("show version", read_timeout=45)
        return {
            "prompt": prompt,
            "show_version_preview": output[:2500],
        }

    def backup_running_config(self) -> str:
        self.log("Reading running configuration.", "info")
        return self._conn.send_command("show running-config", read_timeout=180)

    def run_show_commands(self, commands: list[str], read_timeout: int = 120) -> dict[str, str]:
        outputs: dict[str, str] = {}
        for command in commands:
            self.log(f"Running read-only command: {command}", "info")
            outputs[command] = self._conn.send_command(command, read_timeout=read_timeout)
        return outputs

    def push_config(self, commands: list[str], save: bool) -> dict[str, str | bool]:
        self.log(f"Sending {len(commands)} config command(s).", "info")
        output = self._conn.send_config_set(commands, read_timeout=240)
        save_output = ""
        if save:
            self.log("Saving configuration.", "info")
            save_output = self._conn.save_config()
        return {
            "config_output": output,
            "save": save,
            "save_output": save_output,
        }

    def _build_params(self) -> dict[str, Any]:
        connection_type = self.device["connection_type"]
        platform = self.device["platform"]
        common: dict[str, Any] = {
            "username": self.device.get("username") or "",
            "password": self.password,
            "secret": self.secret,
            "session_log": str(self.session_log_path),
            "session_log_record_writes": False,
            "conn_timeout": 15,
            "auth_timeout": 20,
            "banner_timeout": 20,
            "timeout": 120,
            "fast_cli": False,
        }

        if connection_type == "serial":
            common.update(
                {
                    "device_type": serial_device_type(platform),
                    "host": self.device.get("serial_port") or "serial-console",
                    "serial_settings": {
                        "port": self.device["serial_port"],
                        "baudrate": int(self.device.get("baud_rate") or 9600),
                        "timeout": 15,
                    },
                }
            )
            return common

        common.update(
            {
                "device_type": platform,
                "host": self.device["host"],
                "port": int(self.device.get("ssh_port") or 22),
            }
        )
        return common
