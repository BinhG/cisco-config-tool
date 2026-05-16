from cisco_config_tool.agent import create_agent_proposal
from cisco_config_tool.settings import Settings


def test_offline_agent_generates_vlan_proposal() -> None:
    proposal = create_agent_proposal(
        settings=Settings(openai_api_key=""),
        intent="tạo VLAN 20 tên Camera cho cổng Gi1/0/5-6",
        devices=[{"id": 1, "name": "SW1", "platform": "cisco_ios"}],
        topology_notes="",
        prefer_offline=True,
    )

    assert proposal.source == "offline"
    assert proposal.need_more_info is False
    assert "vlan 20" in proposal.config
    assert "interface range Gi1/0/5, Gi1/0/6" in proposal.config


def test_offline_agent_blocks_risky_commands_after_generation() -> None:
    proposal = create_agent_proposal(
        settings=Settings(openai_api_key=""),
        intent="chạy reload thiết bị",
        devices=[],
        topology_notes="",
        prefer_offline=True,
    )

    assert proposal.need_more_info is True
    assert proposal.config == ""
