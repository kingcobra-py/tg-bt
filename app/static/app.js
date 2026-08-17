const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let ws = null;
let currentJobId = null;
const foundResults = [];

function saveCurrentJob(id) {
  if (id) localStorage.setItem("tgbt_current_job", String(id));
  else localStorage.removeItem("tgbt_current_job");
}

function renderResultsList() {
  const el = $("#results-list");
  if (!foundResults.length) {
    el.innerHTML = '<p class="empty-hint">Results appear here as they are found</p>';
    return;
  }
  el.innerHTML = foundResults
    .map((r) => {
      const lines = [
        `CC : ${esc(r.cc || "")}`,
        `Status : ${esc(r.status || "")}`,
        `Response : ${esc(r.response || "")}`,
      ];
      if (r.receipt) lines.push(`Receipt : ${esc(r.receipt)}`);
      return `<div class="result-card valid">${lines.join("\n")}</div>`;
    })
    .join("");
}

function addFoundResults(results) {
  for (const r of results) {
    const key = r.cc || JSON.stringify(r);
    if (!foundResults.some((x) => x.cc === r.cc && x.status === r.status)) {
      foundResults.push(r);
    }
  }
  renderResultsList();
}

function loadResultsFromChunks(chunks) {
  foundResults.length = 0;
  for (const ch of chunks || []) {
    addFoundResults(ch.found_results || []);
  }
}

async function restoreJobState() {
  const saved = localStorage.getItem("tgbt_current_job");
  if (!saved) return;
  try {
    const data = await api("GET", `/api/jobs/${saved}`);
    currentJobId = data.job.id;
    updateStats(data.job);
    loadResultsFromChunks(data.chunks);
    setStatus(data.job.status);
    if (data.job.status === "running") {
      connectWs(currentJobId);
      notify(`Resumed watching job #${currentJobId}`, "info");
    } else if (data.job.status === "completed") {
      log(`Job #${currentJobId} completed — found: ${data.job.found_count}`, "found");
    }
  } catch (_) {
    localStorage.removeItem("tgbt_current_job");
  }
}

// Tabs
$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
    if (tab.dataset.tab === "sessions") loadSessions();
    if (tab.dataset.tab === "process") loadSessions();
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

function notify(message, type = "info") {
  const container = $("#toasts");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("show"));
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

function getSelectedSessions() {
  return Array.from(document.querySelectorAll("#session-checkboxes input[type=checkbox]:checked"))
    .map((cb) => parseInt(cb.value, 10));
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
    const hint = $("#cfg-hash-hint");
    if (cfg.telegram_api_hash_set) {
      hint.textContent = "✓ API Hash saved (leave blank to keep current)";
      $("#cfg-api-hash").placeholder = "Leave blank to keep saved hash";
    } else {
      hint.textContent = "";
      $("#cfg-api-hash").placeholder = "Enter API Hash";
    }
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
    notify("Config load failed: " + e.message, "error");
  }
}

$("#config-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const apiId = $("#cfg-api-id").value.trim();
  const apiHash = $("#cfg-api-hash").value.trim();
  if (!apiId) {
    notify("API ID is required", "error");
    return;
  }
  if (!apiHash && !$("#cfg-hash-hint").textContent) {
    notify("API Hash is required", "error");
    return;
  }
  try {
    const payload = {
      telegram_api_id: parseInt(apiId),
      lines_per_chunk: parseInt($("#cfg-lines").value),
      antispam_wait_seconds: parseInt($("#cfg-antispam").value),
      bot_response_timeout: parseInt($("#cfg-timeout").value),
      max_retries: parseInt($("#cfg-retries").value),
    };
    if (apiHash) payload.telegram_api_hash = apiHash;

    const res = await api("POST", "/api/config", payload);
    const fd = new FormData();
    fd.append("default_bot", $("#cfg-default-bot").value);
    fd.append("default_command", $("#cfg-default-cmd").value);
    fd.append("default_group", $("#cfg-default-group").value);
    await api("POST", "/api/config/defaults", fd);

    notify(res.message || "Config saved successfully", "success");
    log("Config saved", "found");
    await loadConfig();
  } catch (e) {
    notify("Config save failed: " + e.message, "error");
    log("Config save failed: " + e.message, "failed");
  }
});

// Sessions
async function loadSessions() {
  try {
    const sessions = await api("GET", "/api/sessions");
    const tbody = $("#sessions-table tbody");
    tbody.innerHTML = "";
    const box = $("#session-checkboxes");
    box.innerHTML = "";

    if (!sessions.length) {
      box.innerHTML = '<p class="empty-hint">No sessions yet — add them in the Sessions tab.</p>';
    }

    sessions.forEach((s) => {
      const user = s.user?.username || s.user?.first_name || "—";
      const type = s.type || (s.session_token ? "token" : "file");
      const statusCls = s.connected ? "connected" : "disconnected";
      const statusTxt = s.connected ? "Connected" : (s.error || "Disconnected");
      tbody.innerHTML += `<tr>
        <td>${s.id}</td>
        <td>${esc(s.name)}</td>
        <td>${esc(type)}</td>
        <td>${esc(user)}</td>
        <td>${esc(s.phone || s.user?.phone || "—")}</td>
        <td class="${statusCls}">${esc(statusTxt)}</td>
        <td><button class="btn danger" onclick="deleteSession(${s.id})">Remove</button></td>
      </tr>`;

      const row = document.createElement("label");
      row.className = `session-check ${s.connected ? "" : "disabled"}`;
      row.innerHTML = `
        <input type="checkbox" value="${s.id}" ${s.connected ? "" : "disabled"} />
        <span class="session-check-label">
          <strong>${esc(s.name)}</strong>
          <span class="session-check-meta">${esc(user)} · ${esc(type)} · ${esc(statusTxt)}</span>
        </span>`;
      box.appendChild(row);
    });
  } catch (e) {
    notify("Session load failed: " + e.message, "error");
  }
}

$("#btn-select-all-sessions").addEventListener("click", () => {
  document.querySelectorAll("#session-checkboxes input[type=checkbox]:not(:disabled)").forEach((cb) => {
    cb.checked = true;
  });
  const count = getSelectedSessions().length;
  notify(count ? `${count} session(s) selected` : "No connected sessions available", count ? "info" : "warn");
});

window.deleteSession = async (id) => {
  if (!confirm("Remove this session?")) return;
  try {
    await api("DELETE", `/api/sessions/${id}`);
    notify("Session removed", "success");
    loadSessions();
  } catch (e) {
    notify(e.message, "error");
  }
};

$("#token-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const res = await api("POST", "/api/sessions/token", {
      name: $("#token-name").value,
      token: $("#token-value").value.trim(),
    });
    notify(`Session added: ${res.name} (@${res.user?.username || "?"})`, "success");
    log(`Token session added: ${res.name} (@${res.user?.username || "?"})`, "found");
    $("#token-form").reset();
    loadSessions();
  } catch (e) {
    notify(e.message, "error");
  }
});

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("name", $("#upload-name").value);
  fd.append("file", $("#upload-file").files[0]);
  try {
    const res = await api("POST", "/api/sessions/upload", fd);
    notify(`Session uploaded: ${res.name}`, "success");
    log(`Session uploaded: ${res.name} (@${res.user?.username || "?"})`, "found");
    $("#upload-form").reset();
    loadSessions();
  } catch (e) {
    notify(e.message, "error");
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
      notify("Login code sent to " + phone, "info");
      log("Login code sent to " + phone, "info");
      return;
    }
    const res = await api("POST", "/api/sessions/login/verify", { name, phone, code, password });
    notify(`Logged in: ${res.name} (@${res.user?.username || "?"})`, "success");
    log(`Logged in: ${res.name} (@${res.user?.username || "?"})`, "found");
    $("#login-form").reset();
    loadSessions();
  } catch (e) {
    notify(e.message, "error");
  }
});

// Preview
$("#btn-preview").addEventListener("click", async () => {
  const text = $("#input-text").value;
  const sessionIds = getSelectedSessions();
  if (!text.trim()) return notify("Enter text first", "warn");
  if (!sessionIds.length) return notify("Select at least one session", "warn");

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
    notify(`Preview: ${preview.total_chunks} chunks from ${preview.total_lines} lines`, "info");
  } catch (e) {
    notify(e.message, "error");
  }
});

// Start job
$("#btn-start").addEventListener("click", async () => {
  const text = $("#input-text").value;
  const sessionIds = getSelectedSessions();
  if (!text.trim()) return notify("Enter text", "warn");
  if (!sessionIds.length) return notify("Select sessions", "warn");
  if (!$("#bot-username").value) return notify("Enter bot username", "warn");
  if (!$("#target-group").value) return notify("Enter target group", "warn");

  $("#log").innerHTML = "";
  foundResults.length = 0;
  renderResultsList();
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
    saveCurrentJob(currentJobId);
    notify(`Job #${currentJobId} started`, "success");
    log(`Job #${currentJobId} started`, "info");
    connectWs(currentJobId);
  } catch (e) {
    notify("Start failed: " + e.message, "error");
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
    case "chunks":
      loadResultsFromChunks(data);
      break;
    case "stats_update":
      updateStats(data);
      break;
    case "results_found":
      addFoundResults(data.results || []);
      notify(`Found ${(data.results || []).length} result(s) in chunk ${data.chunk_index + 1}`, "success");
      break;
    case "job_started":
      setStatus("running");
      log("Job running...", "info");
      break;
    case "chunk_started":
      log(`Chunk ${data.chunk_index + 1}/${data.total_chunks || "?"} started on session #${data.session_id}`, "info");
      break;
    case "chunk_done":
      updateStats(data);
      log(
        `Chunk ${data.chunk_index + 1} done — +${data.chunk_found} found, +${data.chunk_failed} failed (${data.completed_chunks}/${data.total_chunks} total)`,
        data.chunk_found ? "found" : "info"
      );
      break;
    case "antispam":
      notify(`AntiSpam — waiting ${data.wait}s`, "warn");
      log(`AntiSpam — waiting ${data.wait}s (retry ${data.retry})`, "warn");
      break;
    case "forwarded":
      log(`Forwarded CC: ${data.cc}`, "found");
      if (data.result) addFoundResults([data.result]);
      break;
    case "job_completed":
      setStatus("completed");
      updateStats(data);
      refreshJobStats();
      notify(`Job done — found: ${data.found}, forwarded: ${data.forwarded}`, "success");
      log(`Done — found: ${data.found}, failed: ${data.failed}, forwarded: ${data.forwarded}`, "found");
      break;
    case "job_failed":
      setStatus("failed");
      notify("Job failed: " + (data.error || "unknown"), "error");
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
    loadResultsFromChunks(data.chunks);
  } catch (_) {}
}

function updateStats(d) {
  if (d.found_count != null) $("#stat-found").textContent = d.found_count;
  else if (d.found != null) $("#stat-found").textContent = d.found;
  if (d.failed_count != null) $("#stat-failed").textContent = d.failed_count;
  else if (d.failed != null) $("#stat-failed").textContent = d.failed;
  if (d.forwarded_count != null) $("#stat-forwarded").textContent = d.forwarded_count;
  else if (d.forwarded != null) $("#stat-forwarded").textContent = d.forwarded;
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
restoreJobState();
