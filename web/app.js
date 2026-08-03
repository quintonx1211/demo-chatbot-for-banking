const $ = (id) => document.getElementById(id);

let sessionId = null;
let selectedSession = null;
let selectedDoc = null;

const KB_TEMPLATE = `# Document title

**doc_id:** KB-XXXX-001
**owner:** Team name
**last_reviewed:** 2026-08-03

## First section
Each "## Section" becomes one retrievable passage. Write the answer a customer
needs, with the concrete figures and timeframes in the prose - the assistant may
only state what is written here.

## Second section
Keep sections focused on a single question. A section covering four topics
retrieves poorly for all four.
`;

/* ---------- helpers ---------- */

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Tiny renderer for the **bold**, `code` and _em_ the backend emits.
function render(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/_([^_]+)_/g, "<em>$1</em>");
}

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    // fetch only rejects when the request never reached a server; the browser's
    // own message ("Failed to fetch") gives no hint that the cause is almost
    // always a stopped backend, so say that instead.
    throw new Error(
      "Cannot reach the server - is `python server.py` still running? " +
      "Note that an API key entered in Settings lives in that process only, " +
      "so it needs re-entering after a restart."
    );
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${path} → ${response.status}`);
  return data;
}

const postJson = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function showError(element, message) {
  element.textContent = message;
  element.classList.remove("hidden");
}

function clearError(element) {
  element.textContent = "";
  element.classList.add("hidden");
}

/* ---------- chat ---------- */

function addMessage(role, text, meta) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = render(text);
  wrap.appendChild(bubble);

  if (meta) {
    const row = document.createElement("div");
    row.className = "msg-meta";
    row.innerHTML = `
      <span class="chip ${meta.route}">${meta.route}</span>
      <span class="chip">${escapeHtml(meta.intent)} · ${meta.confidence.toFixed(2)}</span>
      <span class="chip">${meta.latency_ms} ms</span>
      ${meta.generated ? '<span class="chip">LLM</span>' : ""}`;
    wrap.appendChild(row);

    if (meta.sources && meta.sources.length) {
      const sources = document.createElement("div");
      sources.className = "sources";
      sources.innerHTML =
        "<b>Grounded in:</b><ul>" +
        meta.sources.map((s) =>
          `<li>${escapeHtml(s.citation)} <em>(${s.score.toFixed(2)})</em></li>`
        ).join("") +
        "</ul>";
      wrap.appendChild(sources);
    }
  }

  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
}

function updateInspector(data) {
  const rows = [
    ["Route", data.route],
    ["Intent", data.intent],
    ["Confidence", data.confidence.toFixed(3)],
    ["Answer source", data.generated ? "LLM over retrieved passages" : "Deterministic / extractive"],
    ["Identity verified", data.verified ? "yes" : "no"],
    ["Latency", `${data.latency_ms} ms`],
  ];
  if (data.grounding !== null && data.grounding !== undefined) {
    rows.push(["Grounding score", data.grounding.toFixed(2)]);
  }
  if (data.escalation_reason) rows.push(["Escalated because", data.escalation_reason]);
  if (data.debug && data.debug.note) rows.push(["Note", data.debug.note]);

  let html = rows.map(([k, v]) =>
    `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`
  ).join("");
  html += `<div class="meter"><i style="width:${Math.min(100, data.confidence * 100)}%"></i></div>`;

  if (data.debug && data.debug.scores) {
    html += '<h3 style="margin-top:16px">Intent scores</h3>';
    html += Object.entries(data.debug.scores).map(([intent, score]) =>
      `<div class="kv"><span>${escapeHtml(intent)}</span><span>${score.toFixed(3)}</span></div>`
    ).join("");
  }
  $("inspector").innerHTML = html;
}

async function send(message) {
  if (!message.trim()) return;
  addMessage("customer", message);
  $("input").value = "";
  $("send").disabled = true;
  try {
    const data = await postJson("/api/chat", { session_id: sessionId, message });
    sessionId = data.session_id;
    addMessage("assistant", data.reply, data);
    updateInspector(data);
    if (data.escalated) {
      addMessage("system",
        `Handed off to a human agent · ${data.escalation_reason}. ` +
        "Open the Agent console tab to see the brief.");
      refreshQueue();
    }
  } catch (error) {
    addMessage("system", `Request failed: ${error.message}`);
  } finally {
    $("send").disabled = false;
    $("input").focus();
  }
}

/* ---------- agent console ---------- */

async function refreshQueue() {
  const data = await api("/api/queue");
  const badge = $("queue-count");
  badge.textContent = data.sessions.length;
  badge.classList.toggle("zero", data.sessions.length === 0);

  if (!data.sessions.length) {
    $("queue").innerHTML =
      '<p class="empty">Nothing waiting. Trigger a handoff from the customer chat.</p>';
    return;
  }

  $("queue").innerHTML = data.sessions.map((s) => `
    <button class="queue-item alert ${s.session_id === selectedSession ? "active" : ""}"
            data-id="${escapeHtml(s.session_id)}">
      <strong>${escapeHtml(s.customer_name || "Unidentified caller")}</strong>
      <small>${escapeHtml(s.session_id)} · ${s.turns} turns ·
        ${s.verified ? "verified" : "not verified"}</small>
      <small>${escapeHtml(s.reason || "")}</small>
    </button>`).join("");

  $("queue").querySelectorAll(".queue-item").forEach((button) => {
    button.onclick = () => openSession(button.dataset.id);
  });
}

async function openSession(id) {
  selectedSession = id;
  $("handover-empty").classList.add("hidden");
  $("handover").classList.remove("hidden");
  $("handover-title").textContent = `Conversation ${id}`;
  $("summary").textContent = "Generating handover brief…";

  const data = await postJson("/api/summary", { session_id: id });
  $("summary").innerHTML = render(data.summary);
  $("summary-source").textContent = data.generated
    ? `written by ${data.model}` : "offline template";
  $("transcript").textContent = data.transcript;
  $("audit").innerHTML = data.audit.map((row) => `
    <div class="audit-row">
      <span class="turn">#${row.turn}</span>
      <span class="chip ${row.route}">${row.route}</span>
      <span class="utt" title="${escapeHtml(row.utterance)}">${escapeHtml(row.utterance)}</span>
      <span class="turn">${row.confidence.toFixed(2)} · ${row.latency_ms}ms</span>
    </div>`).join("");
  refreshQueue();
}

/* ---------- knowledge base ---------- */

async function refreshKb() {
  const data = await api("/api/kb");
  $("kb-stats").textContent =
    `${data.stats.passages} retrievable passages across ${data.stats.documents} documents`;
  $("kb-pill").textContent =
    `KB: ${data.stats.passages} passages / ${data.stats.documents} docs`;

  if (!data.documents.length) {
    $("kb-list").innerHTML =
      '<p class="empty">No documents. The assistant will escalate every ' +
      'knowledge question until one is added.</p>';
    return;
  }

  $("kb-list").innerHTML = data.documents.map((d) => `
    <button class="queue-item ${d.filename === selectedDoc ? "active" : ""}"
            data-name="${escapeHtml(d.filename)}">
      <strong>${escapeHtml(d.title)}</strong>
      <small>${escapeHtml(d.filename)} · ${escapeHtml(d.doc_id)}</small>
      <small>${d.passages} passages · ${(d.bytes / 1024).toFixed(1)} KB</small>
    </button>`).join("");

  $("kb-list").querySelectorAll(".queue-item").forEach((button) => {
    button.onclick = () => openDoc(button.dataset.name);
  });
}

async function openDoc(filename) {
  selectedDoc = filename;
  const data = await api(`/api/kb/doc?name=${encodeURIComponent(filename)}`);

  $("kb-empty").classList.add("hidden");
  $("kb-editor").classList.add("hidden");
  $("kb-view").classList.remove("hidden");
  $("kb-title").textContent = filename;
  $("kb-passage-count").textContent = `${data.passages.length} passages`;
  $("kb-raw").textContent = data.content;
  $("kb-passages").innerHTML = data.passages.map((p) => `
    <div class="passage">
      <h4>${escapeHtml(p.heading)}</h4>
      <div class="cite">${escapeHtml(p.citation)}</div>
      <p>${escapeHtml(p.text.slice(0, 320))}${p.text.length > 320 ? "…" : ""}</p>
    </div>`).join("");
  refreshKb();
}

function openEditor(filename, content) {
  clearError($("kb-error"));
  $("kb-empty").classList.add("hidden");
  $("kb-view").classList.add("hidden");
  $("kb-editor").classList.remove("hidden");
  $("kb-editor-title").textContent = filename ? `Edit ${filename}` : "Add document";
  $("kb-filename").value = filename || "";
  $("kb-filename").disabled = Boolean(filename);
  $("kb-content").value = content || "";
}

async function saveDoc() {
  clearError($("kb-error"));
  $("kb-save").disabled = true;
  try {
    const data = await postJson("/api/kb/upload", {
      filename: $("kb-filename").value,
      content: $("kb-content").value,
    });
    $("kb-editor").classList.add("hidden");
    await refreshKb();
    await openDoc(data.document.filename);
  } catch (error) {
    showError($("kb-error"), error.message);
  } finally {
    $("kb-save").disabled = false;
  }
}

async function deleteDoc() {
  if (!selectedDoc) return;
  if (!confirm(`Delete ${selectedDoc}? Its passages stop being retrievable immediately.`)) {
    return;
  }
  await postJson("/api/kb/delete", { filename: selectedDoc });
  selectedDoc = null;
  $("kb-view").classList.add("hidden");
  $("kb-empty").classList.remove("hidden");
  refreshKb();
}

/* ---------- settings ---------- */

async function refreshProviders() {
  const data = await api("/api/providers");
  const active = data.active;

  $("provider-list").innerHTML = data.providers.map((p) => {
    const status = p.available
      ? "ready"
      : !p.sdk_installed ? `pip install ${p.package}`
      : `no key - set ${p.env_key}`;
    const isActive = active.provider === p.name;
    return `
      <button class="queue-item ${isActive ? "active" : ""}" data-name="${escapeHtml(p.name)}">
        <strong>${escapeHtml(p.name)}${isActive ? " · in use" : ""}</strong>
        <small>${escapeHtml(p.default_model)}</small>
        <small>${escapeHtml(status)}${p.key_masked ? ` · key ${escapeHtml(p.key_masked)}` : ""}</small>
      </button>`;
  }).join("");

  $("provider-list").querySelectorAll(".queue-item").forEach((button) => {
    button.onclick = () => {
      $("cfg-provider").value = button.dataset.name;
      $("cfg-key").focus();
    };
  });

  const rows = [
    ["Mode", active.mode],
    ["Provider", active.provider || "-"],
    ["Model", active.model || "-"],
    ["Effort", active.effort || "-"],
    ["Requested", active.requested],
  ];
  if (active.endpoint) rows.push(["Endpoint", active.endpoint]);
  if (active.detail) rows.push(["Detail", active.detail]);
  $("active-config").innerHTML = rows.map(([k, v]) =>
    `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`
  ).join("");

  // Reflect current state in the form without clobbering a key being typed.
  if (active.requested) $("cfg-provider").value = active.requested;
  if (active.effort) $("cfg-effort").value = active.effort;

  const selected = data.providers.find((p) => p.name === $("cfg-provider").value);
  $("cfg-key-hint").textContent = selected && selected.key_set
    ? `- ${selected.key_masked} stored; blank keeps it`
    : "- stored in memory only";
  $("cfg-model").placeholder = selected ? selected.default_model : "";
}

async function saveConfig(clearKey) {
  clearError($("cfg-error"));
  $("cfg-save").disabled = true;
  try {
    const body = {
      provider: $("cfg-provider").value,
      model: $("cfg-model").value,
      effort: $("cfg-effort").value,
      base_url: $("cfg-base").value,
    };
    // Only send api_key when there is something to say: a blank field means
    // "keep the stored key", while clearing is an explicit action.
    if (clearKey) body.api_key = "";
    else if ($("cfg-key").value.trim()) body.api_key = $("cfg-key").value;

    await postJson("/api/providers", body);
    $("cfg-key").value = "";
    await refreshProviders();
    await refreshHealth();
  } catch (error) {
    showError($("cfg-error"), error.message);
  } finally {
    $("cfg-save").disabled = false;
  }
}

/* ---------- health ---------- */

async function refreshHealth() {
  const health = await api("/api/health");
  const pill = $("mode-pill");
  pill.textContent = health.mode === "live"
    ? `${health.provider}: ${health.model}`
    : "LLM: offline (extractive)";
  pill.title = health.detail || `effort: ${health.effort}`;
  pill.className = `pill ${health.mode}`;
  $("kb-pill").textContent =
    `KB: ${health.knowledge_base.passages} passages / ${health.knowledge_base.documents} docs`;
}

/* ---------- wiring ---------- */

const REFRESH = { agent: refreshQueue, kb: refreshKb, settings: refreshProviders };

document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const view = tab.dataset.view;
    ["customer", "agent", "kb", "settings"].forEach((name) => {
      $(`view-${name}`).classList.toggle("hidden", name !== view);
    });
    if (REFRESH[view]) REFRESH[view]();
  };
});

$("composer").onsubmit = (event) => { event.preventDefault(); send($("input").value); };
$("suggestions").querySelectorAll("button").forEach((button) => {
  button.onclick = () => send(button.textContent);
});
$("regen").onclick = () => selectedSession && openSession(selectedSession);

$("kb-new").onclick = () => openEditor(null, "");
$("kb-cancel").onclick = () => {
  $("kb-editor").classList.add("hidden");
  if (selectedDoc) $("kb-view").classList.remove("hidden");
  else $("kb-empty").classList.remove("hidden");
};
$("kb-template").onclick = () => { $("kb-content").value = KB_TEMPLATE; };
$("kb-save").onclick = saveDoc;
$("kb-delete").onclick = deleteDoc;
$("kb-edit").onclick = async () => {
  const data = await api(`/api/kb/doc?name=${encodeURIComponent(selectedDoc)}`);
  openEditor(selectedDoc, data.content);
};

$("cfg-save").onclick = () => saveConfig(false);
$("cfg-clear").onclick = () => saveConfig(true);
$("cfg-provider").onchange = refreshProviders;
$("cfg-key-toggle").onclick = () => {
  const field = $("cfg-key");
  const hidden = field.type === "password";
  field.type = hidden ? "text" : "password";
  $("cfg-key-toggle").textContent = hidden ? "Hide" : "Show";
};

(async function init() {
  await refreshHealth();
  addMessage("assistant",
    "Hello! I'm the virtual assistant for Regional Trust Bank. I can check " +
    "balances and transactions, block a lost card, look up a loan application, " +
    "or answer questions about our products and fees. What do you need?");
  refreshQueue();
})();
