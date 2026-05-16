# MCP Client Setup

Cisco Config Tool exposes a proposal-only MCP server named `cisco-config-assistant`.

It is safe by design:

- no config-mode SSH or console write tools;
- no push-config tool;
- read-only device collection permits only `show ...` commands;
- no stored password or enable secret output;
- running-config output is redacted for common secrets;
- analysis, validation, job metadata, backup metadata, terminal-ready scripts, and config proposals only.

## Codex

The shared workspace config was updated at:

```text
D:\Code\.mcp.json
```

It contains:

```json
{
  "mcpServers": {
    "cisco-config-assistant": {
      "type": "stdio",
      "command": "D:\\Code\\cisco-config-tool\\.venv\\Scripts\\python.exe",
      "args": ["-m", "cisco_config_tool.mcp_server"],
      "env": {
        "CISCO_TOOL_DATA_DIR": "D:/Code/cisco-config-tool/data",
        "OPENAI_API_KEY": "${OPENAI_API_KEY}"
      }
    }
  }
}
```

## AG-Mini / Antigravity

AG-Mini was updated in:

```text
D:\Code\ag-mini\.mcp_servers.json
D:\Code\ag-mini\hub\tools\mcp_registry.json
```

The registry marks `cisco-config-assistant` as `low` risk and allowlists only:

- `cisco_network_status`
- `cisco_list_devices`
- `cisco_propose_config`
- `cisco_validate_config`
- `cisco_collect_device_info`
- `cisco_collect_and_propose`
- `cisco_analyze_show_output`
- `cisco_terminal_script`
- `cisco_recent_jobs`
- `cisco_recent_backups`

## Claude Desktop / Claude Code

Use the same server block under `mcpServers`:

```json
{
  "mcpServers": {
    "cisco-config-assistant": {
      "command": "D:/Code/cisco-config-tool/.venv/Scripts/python.exe",
      "args": ["-m", "cisco_config_tool.mcp_server"],
      "env": {
        "CISCO_TOOL_DATA_DIR": "D:/Code/cisco-config-tool/data",
        "OPENAI_API_KEY": "${OPENAI_API_KEY}"
      }
    }
  }
}
```

Restart the MCP client after changing its config.

## Smoke Test

```powershell
cd D:\Code\cisco-config-tool
uv run python -m cisco_config_tool.mcp_server
```

The server uses stdio, so it waits for an MCP client and does not print an HTTP URL.
