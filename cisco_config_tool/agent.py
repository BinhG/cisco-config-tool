from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import ValidationError

from .commands import find_interactive_hints, find_risky_commands, split_config_commands
from .schemas import AgentProposal
from .settings import Settings


SYSTEM_PROMPT = """
Bạn là agent chuyên cấu hình Cisco IOS/IOS-XE cho người dùng không biết CCNA.
Nhiệm vụ của bạn là tạo đề xuất thay đổi mạng an toàn, dễ hiểu bằng tiếng Việt.

Quy tắc bắt buộc:
- Không tự động thực thi. Chỉ tạo đề xuất cấu hình để người vận hành duyệt.
- Nếu thiếu thông tin quan trọng như VLAN ID, port, IP/subnet, gateway hoặc thiết bị mục tiêu, đặt need_more_info=true và hỏi rõ.
- Không sinh lệnh phá hủy như reload, write erase, erase startup-config, format, delete flash, no username.
- Tránh lệnh interactive như copy, banner, crypto key generate nếu không giải thích rằng cần thao tác riêng.
- Ưu tiên cấu hình Cisco IOS/IOS-XE CLI thông thường.
- Config phải là các dòng CLI có thể đưa vào configuration mode bằng send_config_set.
- Luôn kèm lệnh precheck, verify và rollback.
- Giải thích bằng tiếng Việt đơn giản, tránh thuật ngữ khó nếu không cần.
- Trả về JSON hợp lệ đúng schema, không thêm markdown bên ngoài JSON.
""".strip()


def create_agent_proposal(
    settings: Settings,
    intent: str,
    devices: list[dict[str, Any]],
    topology_notes: str,
    prefer_offline: bool = False,
) -> AgentProposal:
    if prefer_offline:
        return _post_process(_offline_proposal(intent, devices, topology_notes))

    api_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        proposal = _offline_proposal(intent, devices, topology_notes)
        proposal.warnings.append(
            "Chưa cấu hình OpenAI API key, nên đây là đề xuất offline theo mẫu an toàn."
        )
        return _post_process(proposal)

    try:
        proposal = _openai_proposal(settings, api_key, intent, devices, topology_notes)
        return _post_process(proposal)
    except Exception as exc:
        proposal = _offline_proposal(intent, devices, topology_notes)
        proposal.warnings.append(f"Không gọi được AI cloud, đã dùng chế độ offline: {exc}")
        return _post_process(proposal)


def _openai_proposal(
    settings: Settings,
    api_key: str,
    intent: str,
    devices: list[dict[str, Any]],
    topology_notes: str,
) -> AgentProposal:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    schema = AgentProposal.model_json_schema()
    user_payload = {
        "user_intent": intent,
        "selected_devices": devices,
        "topology_notes": topology_notes,
        "required_output_language": "vi",
    }

    response = client.responses.create(
        model=settings.openai_model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Hãy tạo đề xuất cấu hình Cisco theo dữ liệu JSON sau:\n"
                + json.dumps(user_payload, ensure_ascii=False, indent=2),
            },
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "cisco_agent_proposal",
                "schema": schema,
                "strict": False,
            }
        },
    )
    raw_text = response.output_text
    try:
        payload = json.loads(raw_text)
        payload["source"] = "openai"
        return AgentProposal(**payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"AI returned an invalid proposal: {exc}") from exc


def _offline_proposal(intent: str, devices: list[dict[str, Any]], topology_notes: str) -> AgentProposal:
    text = intent.lower()
    if _looks_like_management_ip_request(text):
        return _offline_management_ip_proposal(intent, devices, topology_notes)
    if _looks_like_ssh_request(text):
        return _offline_ssh_proposal(intent, devices, topology_notes)
    if _looks_like_vlan_request(text):
        return _offline_vlan_proposal(intent, devices, topology_notes)
    return _offline_general_proposal(intent, devices, topology_notes)


def _offline_vlan_proposal(intent: str, devices: list[dict[str, Any]], topology_notes: str) -> AgentProposal:
    vlan_id = _first_int_after_keywords(intent, ["vlan"], allow_fallback=True)
    vlan_name = _extract_vlan_name(intent) or (f"VLAN_{vlan_id}" if vlan_id else "")
    ports = _extract_ports(intent)
    config_lines: list[str] = []
    questions: list[str] = []
    assumptions: list[str] = []

    if vlan_id:
        config_lines.extend([f"vlan {vlan_id}", f"name {vlan_name}"])
    else:
        questions.append("VLAN ID là số bao nhiêu? Ví dụ: VLAN 10 cho phòng kế toán.")

    if ports and vlan_id:
        interface_target = _format_interface_target(ports)
        config_lines.extend(
            [
                f"interface range {interface_target}" if len(ports) > 1 else f"interface {ports[0]}",
                "switchport mode access",
                f"switchport access vlan {vlan_id}",
                "spanning-tree portfast",
            ]
        )
        assumptions.append("Các port được hiểu là access port cho máy tính/camera/AP, không phải trunk uplink.")
    else:
        questions.append("Port nào cần đưa vào VLAN này? Ví dụ: Gi1/0/5 hoặc Gi1/0/5-10.")

    if topology_notes:
        assumptions.append("Đã dùng ghi chú topology bạn nhập để hiểu bối cảnh, nhưng chưa tự kiểm tra thiết bị.")

    need_more_info = not vlan_id or not ports
    return AgentProposal(
        title="Đề xuất tạo VLAN",
        need_more_info=need_more_info,
        plain_language_summary=(
            "Đề xuất này tạo VLAN mới và gán các cổng bạn nêu vào VLAN đó. "
            "Nếu port đang là uplink/trunk thì không nên áp dụng cấu hình access này."
        ),
        assumptions=assumptions,
        questions=questions,
        risk_level="medium" if ports else "low",
        risk_notes=[
            "Gán sai VLAN cho port có thể làm thiết bị phía sau mất kết nối.",
            "Không áp dụng cho port uplink/trunk nếu chưa xác nhận.",
        ],
        precheck_commands=["show vlan brief", "show interfaces status", "show interfaces trunk"],
        config="\n".join(config_lines),
        verification_commands=["show vlan brief", "show interfaces status"],
        rollback_commands=_vlan_rollback(vlan_id, ports),
        warnings=[],
        next_steps=[
            "Kiểm tra lại port mục tiêu trước khi push.",
            "Backup running-config trước khi thay đổi.",
            "Sau khi push, verify VLAN và trạng thái port.",
        ],
        source="offline",
    )


def _offline_management_ip_proposal(
    intent: str,
    devices: list[dict[str, Any]],
    topology_notes: str,
) -> AgentProposal:
    vlan_id = _first_int_after_keywords(intent, ["vlan"]) or _first_int_after_keywords(topology_notes, ["vlan"])
    ip_match = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", intent)
    mask_match = re.search(r"\b(255\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", intent)
    gateway_match = re.search(r"(?:gateway|default gateway|gw|cổng ra)\D+(\d{1,3}(?:\.\d{1,3}){3})", intent, re.IGNORECASE)

    config_lines: list[str] = []
    questions: list[str] = []
    if vlan_id and ip_match and mask_match:
        config_lines.extend([f"interface vlan {vlan_id}", f"ip address {ip_match.group(1)} {mask_match.group(1)}", "no shutdown"])
        if gateway_match:
            config_lines.append(f"ip default-gateway {gateway_match.group(1)}")
        else:
            questions.append("Default gateway của switch là IP nào?")
    else:
        questions.extend(
            [
                "Management VLAN là VLAN số mấy?",
                "IP management và subnet mask là gì? Ví dụ: 192.168.10.2 255.255.255.0.",
                "Default gateway là IP nào?",
            ]
        )

    return AgentProposal(
        title="Đề xuất IP quản trị switch",
        need_more_info=bool(questions),
        plain_language_summary="Đề xuất này đặt IP quản trị để bạn truy cập switch từ mạng LAN.",
        assumptions=["Thiết bị là switch Layer 2 dùng default gateway thay vì định tuyến Layer 3."],
        questions=questions,
        risk_level="high",
        risk_notes=[
            "Đổi IP quản trị có thể làm mất phiên SSH hiện tại nếu bạn đang kết nối qua IP cũ.",
            "Cần chắc chắn VLAN quản trị đi đúng trunk/uplink.",
        ],
        precheck_commands=["show ip interface brief", "show vlan brief", "show running-config | include default-gateway"],
        config="\n".join(config_lines),
        verification_commands=["show ip interface brief", "show running-config interface vlan"],
        rollback_commands=["interface vlan <OLD_MGMT_VLAN>", "ip address <OLD_IP> <OLD_MASK>", "ip default-gateway <OLD_GATEWAY>"],
        warnings=[],
        next_steps=["Nên thực hiện qua console nếu có nguy cơ mất SSH.", "Backup running-config trước khi đổi IP quản trị."],
        source="offline",
    )


def _offline_ssh_proposal(intent: str, devices: list[dict[str, Any]], topology_notes: str) -> AgentProposal:
    return AgentProposal(
        title="Đề xuất bật SSH an toàn",
        need_more_info=True,
        plain_language_summary=(
            "Bật SSH cần domain-name, user local hoặc AAA, RSA key và cấu hình line vty. "
            "Một số bước có thể tương tác trực tiếp trên thiết bị."
        ),
        assumptions=["Thiết bị đang chạy Cisco IOS/IOS-XE và có IP quản trị hoạt động."],
        questions=[
            "Domain name muốn dùng là gì? Ví dụ: local.lan.",
            "Bạn muốn tạo user local nào, quyền privilege bao nhiêu?",
            "Thiết bị đã có RSA key chưa? Có thể kiểm tra bằng show crypto key mypubkey rsa.",
        ],
        risk_level="medium",
        risk_notes=["Cấu hình sai line vty hoặc login có thể làm bạn không SSH được vào thiết bị."],
        precheck_commands=["show ip ssh", "show running-config | section line vty", "show crypto key mypubkey rsa"],
        config="ip ssh version 2\nline vty 0 4\ntransport input ssh\nlogin local",
        verification_commands=["show ip ssh", "show users"],
        rollback_commands=["line vty 0 4", "transport input all"],
        warnings=["Không tự sinh lệnh crypto key generate rsa trong config tự động vì lệnh này thường có prompt tương tác."],
        next_steps=["Xác nhận user/domain-name trước khi áp dụng.", "Nên test SSH bằng user mới trước khi đóng phiên console."],
        source="offline",
    )


def _offline_general_proposal(intent: str, devices: list[dict[str, Any]], topology_notes: str) -> AgentProposal:
    questions = [
        "Bạn muốn thay đổi gì: VLAN, IP quản trị, trunk, access port, DHCP snooping, ACL hay routing?",
        "Thiết bị nào sẽ thay đổi và port/interface nào liên quan?",
        "Mục tiêu sau khi cấu hình là thiết bị nào phải ping/truy cập được thiết bị nào?",
    ]
    if not devices:
        questions.append("Hãy chọn ít nhất một thiết bị trong danh sách trước khi tạo config.")

    return AgentProposal(
        title="Cần thêm thông tin để đề xuất cấu hình",
        need_more_info=True,
        plain_language_summary=(
            "Mình chưa đủ thông tin để tạo config an toàn. Với hệ thống mạng, thiếu port, VLAN hoặc IP "
            "có thể làm mất kết nối khi push."
        ),
        assumptions=[],
        questions=questions,
        risk_level="medium",
        risk_notes=["Không nên push config khi chưa xác định rõ thiết bị, interface và IP/VLAN."],
        precheck_commands=["show version", "show running-config", "show ip interface brief", "show interfaces status"],
        config="",
        verification_commands=[],
        rollback_commands=[],
        warnings=[],
        next_steps=["Trả lời các câu hỏi trên bằng tiếng Việt bình thường, không cần dùng thuật ngữ CCNA."],
        source="offline",
    )


def _post_process(proposal: AgentProposal) -> AgentProposal:
    commands = split_config_commands(proposal.config)
    risky = find_risky_commands(commands)
    interactive = find_interactive_hints(commands)
    warnings = list(proposal.warnings)

    if risky:
        warnings.append("Tool đã chặn config do có lệnh rủi ro: " + ", ".join(risky))
        proposal.config = ""
        proposal.need_more_info = True
        proposal.risk_level = "high"

    if interactive:
        warnings.append("Có lệnh có thể cần tương tác thủ công: " + ", ".join(interactive))

    proposal.warnings = warnings
    return proposal


def _looks_like_vlan_request(text: str) -> bool:
    return "vlan" in text or "mạng khách" in text or "guest" in text or "camera" in text


def _looks_like_management_ip_request(text: str) -> bool:
    return any(keyword in text for keyword in ["management", "quản trị", "ip switch", "ip quản"])


def _looks_like_ssh_request(text: str) -> bool:
    return "ssh" in text or "remote" in text or "truy cập từ xa" in text


def _first_int_after_keywords(text: str, keywords: list[str], allow_fallback: bool = False) -> int | None:
    for keyword in keywords:
        match = re.search(rf"{re.escape(keyword)}\D+(\d{{1,4}})", text, re.IGNORECASE)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 4094:
                return value
    if allow_fallback:
        match = re.search(r"\b(\d{1,4})\b", text)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 4094:
                return value
    return None


def _extract_vlan_name(text: str) -> str:
    match = re.search(r"(?:tên|name)\s+([A-Za-z0-9_-]{2,32})", text, re.IGNORECASE)
    if match:
        return match.group(1)
    if "guest" in text.lower() or "khách" in text.lower():
        return "GUEST"
    if "camera" in text.lower():
        return "CAMERA"
    return ""


def _extract_ports(text: str) -> list[str]:
    normalized = text.replace("GigabitEthernet", "Gi").replace("FastEthernet", "Fa").replace("TenGigabitEthernet", "Te")
    pattern = re.compile(r"\b(?:Gi|Fa|Te|Eth)\d+(?:/\d+){1,3}(?:-\d+)?\b", re.IGNORECASE)
    ports: list[str] = []
    for match in pattern.findall(normalized):
        if "-" in match:
            prefix, end = match.rsplit("-", 1)
            base_parts = prefix.split("/")
            if end.isdigit() and base_parts[-1].isdigit():
                start = int(base_parts[-1])
                stop = int(end)
                parent = "/".join(base_parts[:-1])
                if start <= stop and stop - start <= 48:
                    ports.extend([f"{parent}/{idx}" for idx in range(start, stop + 1)])
                else:
                    ports.append(match)
        else:
            ports.append(match)
    return ports


def _format_interface_target(ports: list[str]) -> str:
    return ", ".join(ports)


def _vlan_rollback(vlan_id: int | None, ports: list[str]) -> list[str]:
    rollback: list[str] = []
    if ports:
        target = _format_interface_target(ports)
        rollback.extend([f"interface range {target}" if len(ports) > 1 else f"interface {ports[0]}", "default switchport access vlan", "no spanning-tree portfast"])
    if vlan_id:
        rollback.append(f"no vlan {vlan_id}")
    return rollback
