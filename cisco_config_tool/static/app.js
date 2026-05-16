const state = {
  devices: [],
  jobs: [],
  backups: [],
  selectedDeviceId: null,
  selectedJobId: null,
  lastAgentProposal: null,
};

const qs = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      message = await response.text();
    }
    throw new Error(message);
  }
  return response.json();
}

function showToast(message) {
  const toast = qs("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 4200);
}

async function refreshAll() {
  await Promise.all([refreshHealth(), refreshDevices(), refreshJobs(), refreshBackups(), refreshSerialPorts()]);
}

async function refreshHealth() {
  try {
    await api("/api/health");
    qs("#healthText").textContent = "Sẵn sàng";
  } catch (error) {
    qs("#healthText").textContent = `Lỗi: ${error.message}`;
  }
}

async function refreshDevices() {
  state.devices = await api("/api/devices");
  if (!state.selectedDeviceId && state.devices.length > 0) {
    state.selectedDeviceId = state.devices[0].id;
  }
  if (state.selectedDeviceId && !state.devices.some((device) => device.id === state.selectedDeviceId)) {
    state.selectedDeviceId = state.devices[0]?.id || null;
  }
  renderDevices();
  renderSelectedDevice();
}

async function refreshSerialPorts() {
  const ports = await api("/api/serial-ports");
  const input = qs("#serialPortInput");
  if (!input.value && ports.length > 0) {
    input.placeholder = ports.map((port) => port.device).join(", ");
  }
}

async function refreshJobs() {
  state.jobs = await api("/api/jobs");
  qs("#jobCount").textContent = String(state.jobs.length);
  renderJobs();
  if (state.selectedJobId) {
    await renderJobDetail(state.selectedJobId);
  }
}

async function refreshBackups() {
  state.backups = await api("/api/backups");
  renderBackups();
}

function renderDevices() {
  const list = qs("#deviceList");
  list.replaceChildren();
  qs("#deviceCount").textContent = String(state.devices.length);

  for (const device of state.devices) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `device-row ${device.id === state.selectedDeviceId ? "active" : ""}`;
    row.addEventListener("click", () => {
      state.selectedDeviceId = device.id;
      renderDevices();
      renderSelectedDevice();
    });

    const main = document.createElement("div");
    main.className = "row-main";
    const name = document.createElement("span");
    name.textContent = device.name;
    const type = document.createElement("span");
    type.className = "status";
    type.textContent = device.connection_type.toUpperCase();
    main.append(name, type);

    const sub = document.createElement("div");
    sub.className = "row-sub";
    sub.textContent =
      device.connection_type === "ssh"
        ? `${device.host}:${device.ssh_port} | ${device.platform}`
        : `${device.serial_port} | ${device.baud_rate} | ${device.platform}`;

    row.append(main, sub);
    list.append(row);
  }
}

function renderSelectedDevice() {
  const device = selectedDevice();
  const hasDevice = Boolean(device);
  qs("#selectedDeviceName").textContent = device ? `${device.name} (${device.connection_type})` : "Chưa chọn thiết bị";
  qs("#testBtn").disabled = !hasDevice;
  qs("#backupBtn").disabled = !hasDevice;
  qs("#deleteDeviceBtn").disabled = !hasDevice;
  qs("#pushBtn").disabled = !hasDevice;
  qs("#agentBtn").disabled = !hasDevice;
}

function renderJobs() {
  const list = qs("#jobList");
  list.replaceChildren();

  for (const job of state.jobs) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `job-row ${job.id === state.selectedJobId ? "active" : ""}`;
    row.addEventListener("click", () => {
      state.selectedJobId = job.id;
      renderJobs();
      renderJobDetail(job.id);
    });

    const main = document.createElement("div");
    main.className = "row-main";
    const title = document.createElement("span");
    title.textContent = `#${job.id} ${labelJobType(job.type)}`;
    const status = document.createElement("span");
    status.className = `status ${job.status}`;
    status.textContent = labelStatus(job.status);
    main.append(title, status);

    const sub = document.createElement("div");
    sub.className = "row-sub";
    sub.textContent = `${job.device_name || "Thiết bị đã xóa"} | ${job.created_at}`;
    row.append(main, sub);
    list.append(row);
  }
}

async function renderJobDetail(jobId) {
  const detail = await api(`/api/jobs/${jobId}`);
  qs("#jobTitle").textContent = `Job #${detail.id} - ${labelStatus(detail.status)}`;
  const lines = [];
  for (const log of detail.logs || []) {
    lines.push(`[${log.created_at}] ${log.level.toUpperCase()}: ${log.message}`);
  }
  if (detail.error) {
    lines.push("", `ERROR: ${detail.error}`);
  }
  if (Object.keys(detail.result || {}).length > 0) {
    lines.push("", "RESULT:", JSON.stringify(detail.result, null, 2));
  }
  qs("#jobOutput").textContent = lines.join("\n");
}

function renderBackups() {
  const list = qs("#backupList");
  list.replaceChildren();

  for (const backup of state.backups) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "backup-row";
    row.addEventListener("click", async () => {
      const payload = await api(`/api/backups/${backup.id}`);
      const pre = qs("#backupContent");
      pre.hidden = false;
      pre.textContent = payload.content;
    });

    const main = document.createElement("div");
    main.className = "row-main";
    const title = document.createElement("span");
    title.textContent = backup.name;
    const size = document.createElement("span");
    size.className = "muted";
    size.textContent = `${backup.chars} chars`;
    main.append(title, size);

    const sub = document.createElement("div");
    sub.className = "row-sub";
    sub.textContent = `${backup.device_name || "Thiết bị đã xóa"} | ${backup.created_at}`;
    row.append(main, sub);
    list.append(row);
  }
}

function selectedDevice() {
  return state.devices.find((device) => device.id === state.selectedDeviceId) || null;
}

async function createDevice(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    name: data.get("name"),
    host: data.get("host") || "",
    platform: data.get("platform") || "cisco_ios",
    connection_type: data.get("connection_type"),
    ssh_port: Number(data.get("ssh_port") || 22),
    username: data.get("username") || "",
    password: data.get("password") || "",
    secret: data.get("secret") || "",
    serial_port: data.get("serial_port") || "",
    baud_rate: Number(data.get("baud_rate") || 9600),
    notes: data.get("notes") || "",
  };
  try {
    const device = await api("/api/devices", { method: "POST", body: JSON.stringify(body) });
    form.reset();
    qs("#connectionType").value = "ssh";
    toggleConnectionFields();
    state.selectedDeviceId = device.id;
    await refreshDevices();
    showToast("Đã thêm thiết bị.");
  } catch (error) {
    showToast(error.message);
  }
}

async function enqueue(path, message) {
  try {
    const job = await api(path, { method: "POST", body: "{}" });
    state.selectedJobId = job.id;
    await refreshJobs();
    showToast(message);
  } catch (error) {
    showToast(error.message);
  }
}

async function pushConfig(event) {
  event.preventDefault();
  const device = selectedDevice();
  if (!device) return;

  const form = event.currentTarget;
  const data = new FormData(form);
  const config = data.get("config") || "";
  if (!config.trim()) {
    showToast("Config đang trống.");
    return;
  }
  if (!window.confirm(`Push config vào ${device.name}?`)) {
    return;
  }

  const body = {
    config,
    backup_first: data.get("backup_first") === "on",
    save: data.get("save") === "on",
    allow_risky: data.get("allow_risky") === "on",
    name: "manual-config",
  };

  try {
    const job = await api(`/api/devices/${device.id}/jobs/push`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.selectedJobId = job.id;
    await refreshJobs();
    showToast("Đã đưa job push config vào hàng đợi.");
  } catch (error) {
    showToast(error.message);
  }
}

async function createAgentProposal(event) {
  event.preventDefault();
  const device = selectedDevice();
  if (!device) return;

  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    intent: data.get("intent") || "",
    topology_notes: data.get("topology_notes") || "",
    device_ids: [device.id],
    prefer_offline: data.get("prefer_offline") === "on",
  };

  try {
    qs("#agentBtn").disabled = true;
    qs("#agentBtn").textContent = "Đang phân tích";
    const proposal = await api("/api/agent/propose", {
      method: "POST",
      body: JSON.stringify(body),
    });
    state.lastAgentProposal = proposal;
    renderAgentProposal(proposal);
    showToast("Agent đã tạo đề xuất.");
  } catch (error) {
    showToast(error.message);
  } finally {
    qs("#agentBtn").textContent = "Nhờ AI đề xuất";
    qs("#agentBtn").disabled = !selectedDevice();
  }
}

function renderAgentProposal(proposal) {
  qs("#agentResult").hidden = false;
  qs("#agentSource").textContent = proposal.source === "openai" ? "OpenAI" : "Offline";
  qs("#agentTitle").textContent = proposal.title || "Đề xuất cấu hình";
  const risk = qs("#agentRisk");
  risk.className = `status ${proposal.risk_level === "high" ? "failed" : proposal.risk_level === "medium" ? "running" : "succeeded"}`;
  risk.textContent = `Rủi ro ${labelRisk(proposal.risk_level)}`;
  qs("#agentSummaryText").textContent = proposal.plain_language_summary || "";
  qs("#agentConfig").value = proposal.config || "";

  renderListBlock("#agentQuestions", "Cần bạn xác nhận", proposal.questions || []);
  renderListBlock("#agentWarnings", "Cảnh báo", proposal.warnings || []);

  const detail = {
    assumptions: proposal.assumptions || [],
    risk_notes: proposal.risk_notes || [],
    precheck_commands: proposal.precheck_commands || [],
    verification_commands: proposal.verification_commands || [],
    rollback_commands: proposal.rollback_commands || [],
    next_steps: proposal.next_steps || [],
  };
  qs("#agentDetail").textContent = JSON.stringify(detail, null, 2);
}

function renderListBlock(selector, title, items) {
  const block = qs(selector);
  block.replaceChildren();
  if (!items.length) {
    return;
  }
  const strong = document.createElement("strong");
  strong.textContent = title;
  const list = document.createElement("ul");
  for (const item of items) {
    const li = document.createElement("li");
    li.textContent = item;
    list.append(li);
  }
  block.append(strong, list);
}

function copyAgentConfigToPush() {
  const config = qs("#agentConfig").value.trim();
  if (!config) {
    showToast("Agent chưa có config để đưa vào ô Push.");
    return;
  }
  qs("#configText").value = config;
  showToast("Đã đưa config agent đề xuất vào ô Push.");
}

function toggleAgentDetail() {
  const detail = qs("#agentDetail");
  detail.hidden = !detail.hidden;
}

async function deleteSelectedDevice() {
  const device = selectedDevice();
  if (!device) return;
  if (!window.confirm(`Xóa ${device.name}?`)) {
    return;
  }
  try {
    await api(`/api/devices/${device.id}`, { method: "DELETE" });
    state.selectedDeviceId = null;
    await Promise.all([refreshDevices(), refreshBackups(), refreshJobs()]);
    showToast("Đã xóa thiết bị.");
  } catch (error) {
    showToast(error.message);
  }
}

function toggleConnectionFields() {
  const type = qs("#connectionType").value;
  qs("#sshFields").hidden = type !== "ssh";
  qs("#serialFields").hidden = type !== "serial";
}

function labelJobType(type) {
  return {
    test_connection: "Test",
    backup_running_config: "Backup",
    push_config: "Push config",
  }[type] || type;
}

function labelStatus(status) {
  return {
    queued: "Đợi",
    running: "Đang chạy",
    succeeded: "Xong",
    failed: "Lỗi",
  }[status] || status;
}

function labelRisk(risk) {
  return {
    low: "thấp",
    medium: "trung bình",
    high: "cao",
  }[risk] || risk;
}

function bindEvents() {
  qs("#refreshAll").addEventListener("click", refreshAll);
  qs("#refreshBackups").addEventListener("click", refreshBackups);
  qs("#deviceForm").addEventListener("submit", createDevice);
  qs("#agentForm").addEventListener("submit", createAgentProposal);
  qs("#pushForm").addEventListener("submit", pushConfig);
  qs("#connectionType").addEventListener("change", toggleConnectionFields);
  qs("#testBtn").addEventListener("click", () => {
    const device = selectedDevice();
    if (device) enqueue(`/api/devices/${device.id}/jobs/test`, "Đã đưa job test vào hàng đợi.");
  });
  qs("#backupBtn").addEventListener("click", () => {
    const device = selectedDevice();
    if (device) enqueue(`/api/devices/${device.id}/jobs/backup`, "Đã đưa job backup vào hàng đợi.");
  });
  qs("#deleteDeviceBtn").addEventListener("click", deleteSelectedDevice);
  qs("#copyAgentConfig").addEventListener("click", copyAgentConfigToPush);
  qs("#showAgentDetail").addEventListener("click", toggleAgentDetail);
}

bindEvents();
toggleConnectionFields();
refreshAll().catch((error) => showToast(error.message));
window.setInterval(() => {
  refreshJobs().catch(() => {});
}, 3000);
