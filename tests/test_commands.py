from cisco_config_tool.commands import (
    find_interactive_hints,
    find_risky_commands,
    mask_sensitive_config,
    split_config_commands,
    split_show_commands,
    terminal_script_from_config,
    validate_show_commands,
)


def test_split_config_commands_skips_blank_comments_and_delimiters() -> None:
    config = """
    !
    # local note
    interface GigabitEthernet1/0/1
      description uplink

    no shutdown
    """

    assert split_config_commands(config) == [
        "interface GigabitEthernet1/0/1",
        "description uplink",
        "no shutdown",
    ]


def test_find_risky_commands_blocks_destructive_patterns() -> None:
    commands = ["interface vlan 10", "write erase", "reload"]

    assert find_risky_commands(commands) == ["write erase", "reload"]


def test_find_interactive_hints_flags_prompt_driven_commands() -> None:
    commands = ["banner motd ^hello^", "copy running-config startup-config"]

    assert find_interactive_hints(commands) == commands


def test_validate_show_commands_allows_only_show() -> None:
    commands = split_show_commands(
        """
        show version
        configure terminal
        show tech-support
        """
    )

    errors = validate_show_commands(commands)

    assert len(errors) == 2
    assert "configure terminal" in errors[0]
    assert "show tech-support" in errors[1]


def test_mask_sensitive_config_redacts_common_secrets() -> None:
    config = "\n".join(
        [
            "enable secret 9 verysecret",
            "username admin privilege 15 secret 9 hashhere",
            "snmp-server community public RO",
            "interface vlan 10",
        ]
    )

    masked = mask_sensitive_config(config)

    assert "verysecret" not in masked
    assert "hashhere" not in masked
    assert "public" not in masked
    assert "interface vlan 10" in masked


def test_terminal_script_from_config_wraps_config_mode() -> None:
    assert terminal_script_from_config("interface vlan 10\nno shutdown") == (
        "configure terminal\ninterface vlan 10\nno shutdown\nend"
    )
