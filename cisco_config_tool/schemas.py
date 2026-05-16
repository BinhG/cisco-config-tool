from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ConnectionType = Literal["ssh", "serial"]


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = ""
    platform: str = Field(default="cisco_ios", min_length=1, max_length=80)
    connection_type: ConnectionType = "ssh"
    ssh_port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="", max_length=120)
    password: str = Field(default="", max_length=512)
    secret: str = Field(default="", max_length=512)
    serial_port: str = Field(default="", max_length=120)
    baud_rate: int = Field(default=9600, ge=300, le=921600)
    notes: str = Field(default="", max_length=1000)

    @field_validator("name", "host", "platform", "username", "serial_port", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_connection_target(self) -> "DeviceCreate":
        if self.connection_type == "ssh" and not self.host:
            raise ValueError("Host/IP is required for SSH devices.")
        if self.connection_type == "serial" and not self.serial_port:
            raise ValueError("Serial port is required for console devices.")
        return self


class DeviceOut(BaseModel):
    id: int
    name: str
    host: str
    platform: str
    connection_type: ConnectionType
    ssh_port: int
    username: str
    has_password: bool
    has_secret: bool
    serial_port: str
    baud_rate: int
    notes: str
    created_at: str
    updated_at: str


class PushConfigRequest(BaseModel):
    config: str = Field(min_length=1)
    save: bool = False
    backup_first: bool = True
    allow_risky: bool = False
    name: str = Field(default="manual-config", max_length=160)


class AgentRequest(BaseModel):
    intent: str = Field(min_length=3, max_length=6000)
    device_ids: list[int] = Field(default_factory=list)
    topology_notes: str = Field(default="", max_length=4000)
    prefer_offline: bool = False

    @field_validator("intent", "topology_notes")
    @classmethod
    def strip_agent_text(cls, value: str) -> str:
        return value.strip()


class AdvisorSessionCreate(BaseModel):
    device_id: int | None = None
    title: str = Field(default="Advisor session", max_length=160)
    topology_notes: str = Field(default="", max_length=4000)

    @field_validator("title", "topology_notes")
    @classmethod
    def strip_session_text(cls, value: str) -> str:
        return value.strip()


class AdvisorMessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    auto_collect: bool = True
    commands_text: str = Field(default="", max_length=3000)
    prefer_offline: bool = False

    @field_validator("message", "commands_text")
    @classmethod
    def strip_message_text(cls, value: str) -> str:
        return value.strip()


RiskLevel = Literal["low", "medium", "high"]
ProposalSource = Literal["openai", "offline"]


class AgentProposal(BaseModel):
    title: str
    need_more_info: bool
    plain_language_summary: str
    assumptions: list[str]
    questions: list[str]
    risk_level: RiskLevel
    risk_notes: list[str]
    precheck_commands: list[str]
    config: str
    verification_commands: list[str]
    rollback_commands: list[str]
    warnings: list[str]
    next_steps: list[str]
    source: ProposalSource


class JobOut(BaseModel):
    id: int
    device_id: int
    device_name: str | None = None
    type: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str
    created_at: str
    started_at: str | None
    finished_at: str | None


class SerialPortOut(BaseModel):
    device: str
    description: str
    hwid: str
