const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let ws = null;
let currentJobId = null;

// Tabs
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "sessions") loadSessions();
    if (tab.dataset.tab === "history") loadHistory();
    if (tab.dataset.tab === "config") loadConfig();
  });
});

function log(msg, cls = "") {
  const el = $("#log");
  const time = new Date().toLocaleTimeString();
  el.innerHTML += `<div class="entry"><span class="time">[${time}]</span> <span class="${cls}">${esc(msg)}</span></div>`;
  el.scrollTop = el.scrollHeight;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function getSelectedSessions() {
  return Array.from($("#session-select").selectedOptions).map((o) => parseInt(o.value));
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// Config
async function loadConfig() {
  try {
    const cfg = await api("GET", "/api/config");
    $("#cfg-api-id").value = cfg.telegram_api_id || "";
    $("#cfg-api-hash").value = "";
    $("#cfg-lines").value = cfg.lines_per_chunk;
    $("#cfg-antispam").value = cfg.antispam_wait_seconds;
    $("#cfg-timeout").value = cfg.bot_response_timeout;
    $("#cfg-retries").value = cfg.max_retries;
    $("#cfg-default-bot").value = cfg.default_bot || "";
    $("#cfg-default-cmd").value = cfg.default_command || "";
    $("#cfg-default-group").value = cfg.default_group || "";
    $("#bot-username").value = cfg.default_bot || "";
    $("#command").value = cfg.default_command || "";
    $("#target-group").value = cfg.default_group || "";
  } catch (e) {
    log("Config load failed: " + e.message, "failed");
  }
}

$("#config-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/config", {
      telegram_api_id: parseInt($("#cfg-api-id").value) || undefined,
      telegram_api_hash: $("#cfg-api-hash").value || undefined,
      lines_per_chunk: parseInt($("#cfg-lines").value),
      antispam_wait_seconds: parseInt($("#cfg-antispam").value),
      bot_response_timeout: parseInt($("#cfg-timeout").value),
      max_retries: parseInt($("#cfg-retries").value),
    });
    const fd = new FormData();
    fd.append("default_bot", $("#cfg-default-bot").value);
    fd.append("default_command", $("#cfg-default-cmd").value);
    fd.append("default_group", $("#cfg-default-group").value);
    await api("POST", "/api/config/defaults", fd);
    log("Config saved", "found");
  } catch (e) {
    log("Config save failed: " + e.message, "failed");
  }
});

// Sessions
async function loadSessions() {
  try {
    const sessions = await api("GET", "/api/sessions");
    const tbody = $("#sessions-table tbody");
    tbody.innerHTML = "";
    const sel = $("#session-select");
    sel.innerHTML = "";

    sessions.forEach((s) => {
      const user = s.user?.username || s.user?.first_name || "—";
      const statusCls = s.connected ? "connected" : "disconnected";
      const statusTxt = s.connected ? "Connected" : (s.error || "Disconnected");
      tbody.innerHTML += `<tr>
        <td>${s.id}</td>
        <td>${esc(s.name)}</td>
        <td>${esc(user)}</td>
        <td>${esc(s.phone || s.user?.phone || "—")}</td>
        <td class="${statusCls}">${esc(statusTxt)}</td>
        <td><button class="btn danger" onclick="deleteSession(${s.id})">Remove</button></td>
      </tr>`;

      if (s.connected) {
        const opt = document.createElement("option");
        opt.value = s.id;
        opt.textContent = `${s.name} (${user})`;
        sel.appendChild(opt);
      }
    });
  } catch (e) {
    log("Session load failed: " + e.message, "failed");
  }
}

window.deleteSession = async (id) => {
  if (!confirm("Remove this session?")) return;
  try {
    await api("DELETE", `/api/sessions/${id}`);
    loadSessions();
  } catch (e) {
    alert(e.message);
  }
};

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("name", $("#upload-name").value);
  fd.append("file", $("#upload-file").files[0]);
  try {
    const res = await api("POST", "/api/sessions/upload", fd);
    log(`Session uploaded: ${res.name} (@${res.user?.username || "?"})`, "found");
    $("#upload-form").reset();
    loadSessions();
  } catch (e) {
    alert(e.message);
  }
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#login-name").value;
  const phone = $("#login-phone").value;
  const code = $("#login-code").value;
  const password = $("#login-password").value;

  try {
    if (!code) {
      await api("POST", "/api/sessions/login/start", { name, phone });
      log("Login code sent to " + phone, "info");
      alert("Code sent! Enter the code and click Login / Verify again.");
      return;
    }
    const res = await api("POST", "/api/sessions/login/verify", { name, phone, code, password });
    log(`Logged in: ${res.name} (@${res.user?.username || "?"})`, "found");
    $("#login-form").reset();
    loadSessions();
  } catch (e) {
    alert(e.message);
  }
});

// Preview
$("#btn-preview").addEventListener("click", async () => {
  const text = $("#input-text").value;
  const sessionIds = getSelectedSessions();
  if (!text.trim()) return alert("Enter text first");
  if (!sessionIds.length) return alert("Select at least one session");

  try {
    const preview = await api("POST", "/api/jobs/preview", {
      input_text: text,
      bot_username: $("#bot-username").value,
      command: $("#command").value,
      target_group: $("#target-group").value,
      session_ids: sessionIds,
    });

    const box = $("#preview-box");
    box.classList.remove("hidden");
    let html = `<strong>${preview.total_lines} lines → ${preview.total_chunks} chunks (${preview.lines_per_chunk} lines each)</strong><br><br>`;
    preview.assignments.forEach((a) => {
      html += `Chunk ${a.chunk_index + 1}: Session #${a.session_id} (${a.lines} lines)<br>`;
    });
    html += "<br><strong>Per session:</strong> ";
    for (const [sid, count] of Object.entries(preview.chunks_per_session)) {
      html += `Session #${sid}: ${count} chunks &nbsp;`;
    }
    box.innerHTML = html;
  } catch (e) {
    alert(e.message);
  }
});

// Start job
$("#btn-start").addEventListener("click", async () => {
  const text = $("#input-text").value;
  const sessionIds = getSelectedSessions();
  if (!text.trim()) return alert("Enter text");
  if (!sessionIds.length) return alert("Select sessions");
  if (!$("#bot-username").value) return alert("Enter bot username");
  if (!$("#target-group").value) return alert("Enter target group");

  $("#log").innerHTML = "";
  setStatus("running");
  resetStats();

  try {
    const res = await api("POST", "/api/jobs", {
      input_text: text,
      bot_username: $("#bot-username").value,
      command: $("#command").value,
      target_group: $("#target-group").value,
      session_ids: sessionIds,
    });
    currentJobId = res.job_id;
    log(`Job #${currentJobId} started`, "info");
    connectWs(currentJobId);
  } catch (e) {
    log("Start failed: " + e.message, "failed");
    setStatus("failed");
  }
});

function setStatus(s) {
  const el = $("#job-status");
  el.className = "status-badge " + s;
  el.textContent = s;
}

function resetStats() {
  $("#stat-found").textContent = "0";
  $("#stat-failed").textContent = "0";
  $("#stat-forwarded").textContent = "0";
  $("#stat-chunks").textContent = "0/0";
}

function connectWs(jobId) {
  if (ws) ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);

  ws.onmessage = (evt) => {
    const msg = JSON.parse(evt.data);
    handleWsEvent(msg);
  };

  ws.onclose = () => {
    log("WebSocket closed", "warn");
  };
}

function handleWsEvent(msg) {
  const { type, data } = msg;

  switch (type) {
    case "snapshot":
      updateStats(data);
      break;
    case "job_started":
      setStatus("running");
      log("Job running...", "info");
      break;
    case "chunk_started":
      log(`Chunk ${data.chunk_index + 1} started on session #${data.session_id}`, "info");
      break;
    case "chunk_done":
      log(`Chunk ${data.chunk_index + 1} done — found: ${data.found}, failed: ${data.failed}`, "found");
      refreshJobStats();
      break;
    case "antispam":
      log(`AntiSpam — waiting ${data.wait}s (retry ${data.retry})`, "warn");
      break;
    case "forwarded":
      log(`Forwarded CC: ${data.cc}`, "found");
      break;
    case "job_completed":
      setStatus("completed");
      updateStats(data);
      log(`Done — found: ${data.found}, failed: ${data.failed}, forwarded: ${data.forwarded}`, "found");
      break;
    case "job_failed":
      setStatus("failed");
      log("Job failed: " + (data.error || "unknown"), "failed");
      break;
    case "message":
      if (data.text && data.text.includes("Took:")) {
        log("Bot completion marker received", "info");
      }
      break;
  }
}

async function refreshJobStats() {
  if (!currentJobId) return;
  try {
    const data = await api("GET", `/api/jobs/${currentJobId}`);
    updateStats(data.job);
  } catch (_) {}
}

function updateStats(d) {
  if (d.found != null) $("#stat-found").textContent = d.found;
  if (d.found_count != null) $("#stat-found").textContent = d.found_count;
  if (d.failed != null) $("#stat-failed").textContent = d.failed;
  if (d.failed_count != null) $("#stat-failed").textContent = d.failed_count;
  if (d.forwarded != null) $("#stat-forwarded").textContent = d.forwarded;
  if (d.forwarded_count != null) $("#stat-forwarded").textContent = d.forwarded_count;
  if (d.completed_chunks != null && d.total_chunks != null) {
    $("#stat-chunks").textContent = `${d.completed_chunks}/${d.total_chunks}`;
  }
}

// History
async function loadHistory() {
  try {
    const jobs = await api("GET", "/api/jobs");
    const tbody = $("#history-table tbody");
    tbody.innerHTML = "";
    jobs.forEach((j) => {
      tbody.innerHTML += `<tr>
        <td>${j.id}</td>
        <td>${j.status}</td>
        <td>${esc(j.bot_username)}</td>
        <td>${j.completed_chunks}/${j.total_chunks}</td>
        <td>${j.found_count}</td>
        <td>${j.failed_count}</td>
        <td>${j.forwarded_count}</td>
        <td>${j.created_at || "—"}</td>
      </tr>`;
    });
  } catch (e) {
    log("History load failed: " + e.message, "failed");
  }
}

// Init
loadConfig();
loadSessions();
