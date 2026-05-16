# Cisco Config Tool

MVP tool để cấu hình thiết bị Cisco qua SSH hoặc dây console. Bản đầu tiên tập trung vào các thao tác an toàn: lưu inventory, test kết nối, backup `running-config`, push config, lưu log job và lưu backup trước thay đổi.

Tool cũng có lớp AI agent để người không biết CCNA mô tả mục tiêu bằng tiếng Việt. Agent tạo đề xuất gồm giải thích, câu hỏi cần xác nhận, config Cisco IOS/IOS-XE, lệnh kiểm tra và rollback. Agent không tự push; người vận hành phải đưa config sang ô Push và xác nhận.

## Chạy local

```powershell
uv sync --extra dev
uv run cisco-config-tool
```

Mặc định ứng dụng chạy tại:

```text
http://127.0.0.1:8088
```

Có thể đổi port bằng `.env`:

```text
CISCO_TOOL_PORT=8090
```

## Bật AI cloud

Nếu có OpenAI API key:

```powershell
$env:OPENAI_API_KEY="sk-..."
uv run cisco-config-tool
```

Hoặc đặt trong `.env`:

```text
CISCO_TOOL_OPENAI_API_KEY=sk-...
CISCO_TOOL_OPENAI_MODEL=gpt-5.2
```

Ứng dụng dùng OpenAI Responses API cho đề xuất cấu hình. Nếu chưa có API key, màn hình AI agent vẫn chạy ở chế độ offline theo các mẫu an toàn phổ biến như VLAN, IP quản trị và SSH.

## Cách dùng AI agent

1. Thêm thiết bị SSH hoặc console.
2. Chọn thiết bị.
3. Nhập mục tiêu bằng tiếng Việt, ví dụ:

```text
tạo VLAN 20 tên Camera cho cổng Gi1/0/5-10, không đụng Gi1/0/48 vì đó là uplink
```

4. Bấm `Nhờ AI đề xuất`.
5. Đọc phần cảnh báo/câu hỏi. Nếu hợp lý, bấm `Đưa vào ô Push`.
6. Bấm `Push config` sau khi đã chọn backup trước thay đổi.

## MCP server cho Codex, Claude, Antigravity, ag-mini

Tool có MCP server chỉ để phân tích và đề xuất cấu hình, không có quyền push config vào thiết bị.

Chạy thử:

```powershell
cd D:\Code\cisco-config-tool
uv run python -m cisco_config_tool.mcp_server
```

MCP tools được expose:

- `cisco_network_status`
- `cisco_list_devices`
- `cisco_propose_config`
- `cisco_validate_config`
- `cisco_collect_device_info`
- `cisco_collect_and_propose`
- `cisco_analyze_show_output`
- `cisco_explain_config`
- `cisco_compare_config`
- `cisco_terminal_script`
- `cisco_recent_jobs`
- `cisco_recent_backups`

## Luồng Quân sư

Luồng chính cho người không biết CCNA là tab `Quân sư`:

1. Chọn thiết bị.
2. Nhập nhu cầu bằng tiếng Việt.
3. Tool tự chạy bộ lệnh `show ...` read-only để lấy context hiện tại.
4. AI trả lời theo hội thoại nhiều lượt: nếu thiếu thông tin thì hỏi lại, nếu đủ thì tạo config, verify, rollback.
5. Tool sinh `terminal script` để bạn copy/paste vào terminal. MCP không tự push.

Nếu không muốn connect thiết bị ở lượt hỏi đó, bỏ chọn `Tự động đọc context thiết bị`.

Config MCP dạng phổ biến:

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

## Thiết bị SSH

Điền:

- `Kiểu kết nối`: SSH
- `IP/Host`
- `Port SSH`: thường là `22`
- `Platform Netmiko`: thường là `cisco_ios`
- `Username`, `Password`, `Enable secret` nếu cần privilege mode

## Thiết bị console

Điền:

- `Kiểu kết nối`: Console
- `Cổng console`: ví dụ `COM3` trên Windows hoặc `/dev/ttyUSB0` trên Linux
- `Baud`: thường là `9600`
- `Platform Netmiko`: thường là `cisco_ios`

Với console, tool dùng Netmiko serial driver. Nếu cần xử lý trạng thái thô như ROMmon hoặc bootloader, có thể bổ sung adapter `pySerial` riêng ở bước tiếp theo.

## Giới hạn MVP

- Chưa có login nhiều người dùng.
- Chưa có phân quyền theo nhóm thiết bị.
- Chưa có rollback tự động; backup đã được lưu để restore thủ công.
- Các lệnh interactive như `copy`, `banner`, `crypto key generate` có thể cần workflow riêng.
- Job runner hiện chạy một worker cục bộ, phù hợp MVP và lab.
- AI agent chỉ tạo đề xuất. Không coi đề xuất AI là đúng tuyệt đối khi thay đổi core switch, uplink, routing hoặc firewall path.

## Cấu trúc dữ liệu

Dữ liệu nằm trong thư mục `data`:

- `cisco_config_tool.sqlite3`: database SQLite
- `secret.key`: khóa mã hóa mật khẩu
- `session_logs`: log phiên Netmiko

Không xóa `secret.key` nếu vẫn cần đọc lại mật khẩu đã lưu.
