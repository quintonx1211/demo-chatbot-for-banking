const $ = (id) => document.getElementById(id);

let sessionId = null;
let selectedSession = null;
let selectedDoc = null;
let staff = null;
let renderedCount = 0;
let pollTimer = null;

const KB_TEMPLATE = `# Document title

**doc_id:** KB-XXXX-001
**owner:** Team name

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
    throw new Error(
      "Không kết nối được máy chủ - `python server.py` còn đang chạy không? " +
      "API key nhập trong Cấu hình chỉ tồn tại trong tiến trình đó, nên phải " +
      "nhập lại sau mỗi lần khởi động lại."
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

const showError = (el, msg) => { el.textContent = msg; el.classList.remove("hidden"); };
const clearError = (el) => { el.textContent = ""; el.classList.add("hidden"); };
const pct = (n) => `${Math.round(n * 100)}%`;

/* ---------- screens ---------- */

function showScreen(name) {
  ["customer", "login", "agent"].forEach((s) =>
    $(`screen-${s}`).classList.toggle("hidden", s !== name));
  $("brand-sub").textContent =
    name === "agent" ? "Bảng điều khiển nhân viên" : "Trợ lý ảo";
}

function showPanel(name) {
  ["dashboard", "queue", "kb", "settings"].forEach((p) =>
    $(`panel-${p}`).classList.toggle("hidden", p !== name));
  document.querySelectorAll(".subtab").forEach((t) =>
    t.classList.toggle("active", t.dataset.panel === name));
  const load = { dashboard: refreshDashboard, queue: refreshQueue,
                 kb: refreshKb, settings: () => { refreshProviders(); refreshTtsStatus(); } };
  if (load[name]) load[name]();
}

/* ---------- customer chat ---------- */

function clockNow() {
  return new Date().toLocaleTimeString("vi-VN",
    { hour: "2-digit", minute: "2-digit" });
}

function avatarFor(role) {
  const span = document.createElement("span");
  if (role === "customer") {
    span.className = "avatar sm me";
    span.textContent = "B";           // "Bạn"
  } else if (role === "agent") {
    span.className = "avatar sm";
    span.textContent = "NV";          // nhân viên
  } else {
    span.className = "avatar sm bot";
    span.textContent = "AB";
  }
  return span;
}

function addMessage(role, text, meta, author) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;

  if (role !== "system") wrap.appendChild(avatarFor(role));

  const body = document.createElement("div");
  body.className = "msg-body";
  wrap.appendChild(body);

  if (author) {
    const who = document.createElement("div");
    who.className = "author";
    who.textContent = author;
    body.appendChild(who);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  // System messages are generated status/error text, not assistant prose -
  // they were never meant to carry markdown. Running them through render()
  // let its `_..._` -> italic rule eat the underscores out of any
  // snake_case error code (voice_bad_request rendered as "voicebadrequest"
  // with "bad" italicised), which is worse than showing no formatting at
  // all: it silently changes what the operator reads off the code.
  bubble.innerHTML = role === "system" ? escapeHtml(text) : render(text);
  body.appendChild(bubble);

  if (role === "assistant" && text.trim()) {
    body.appendChild(makeVoiceButton(text));
  }

  if (role !== "system") {
    const time = document.createElement("div");
    time.className = "msg-time";
    // A double tick on the customer's own message, which is what every
    // messaging app uses to mean "delivered". Decorative here - there is no
    // delivery receipt behind it - but its absence is what makes a chat demo
    // feel unfinished.
    time.textContent = role === "customer" ? `${clockNow()} ✓✓` : clockNow();
    body.appendChild(time);
  }

  // Route, intent, confidence, latency and the grounding citations all live in
  // the routing inspector beside this pane. They were repeated under every
  // bubble as well, which made the conversation read like a debug log rather
  // than a bank talking to a customer - and the customer-facing half of the
  // demo is meant to look like the product, not the instrumentation. The
  // inspector is the one place that owns them now.

  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;
}

// Where the answer came from, and nothing else.
//
// This panel used to carry route, intent, confidence, a confidence meter,
// latency, per-intent scores and the decision path. All of that is real and
// worth showing - to staff. On the customer screen it turned a bank chat into
// an instrumentation readout, which was the complaint. The technical view now
// lives in the agent console, against the session it describes.
const SOURCE_LABEL = {
  raw_llm: "LLM thuần - không truy xuất, không guardrail, không kiểm tra dẫn nguồn",
  deterministic: "Quy trình nghiệp vụ - đọc thẳng từ hồ sơ, không qua model",
  guardrail: "Chặn bởi quy định - từ chối trước khi model nhìn thấy câu hỏi",
  agent: "Nhân viên đang phụ trách hội thoại này",
};

function updateInspector(data) {
  let source = SOURCE_LABEL[data.route];
  if (!source) {
    source = data.generated
      ? "Model trả lời dựa trên tài liệu đã truy xuất"
      : "Trích nguyên văn từ tài liệu đã thẩm định";
  }

  let html = `<div class="source-head">${escapeHtml(source)}</div>`;

  const sources = data.sources || [];
  if (sources.length) {
    // The unverified market-reference documents have to be visibly different
    // here. A competitor fee schedule listed in the same style as the bank's
    // own policy is the misattribution the KB-MKT- headers exist to prevent.
    html += '<div class="source-list">' + sources.map((s) => {
      const unverified = (s.citation || "").includes("KB-MKT-");
      return `<div class="source-item${unverified ? " unverified" : ""}">
        <span class="ic">${unverified ? "🌐" : "📄"}</span>
        <span>${escapeHtml(s.breadcrumb || s.citation)}
          ${unverified ? '<em>Tham khảo thị trường - chưa được ngân hàng thẩm định</em>' : ""}
        </span></div>`;
    }).join("") + "</div>";
  } else if (data.route !== "deterministic" && data.route !== "agent") {
    html += '<p class="empty">Không có tài liệu nào được dẫn cho câu trả lời này.</p>';
  }

  if (data.escalation_reason) {
    html += `<div class="source-note">Lý do chuyển nhân viên:
      ${escapeHtml(data.escalation_reason)}</div>`;
  }

  $("inspector").innerHTML = html;
}

/* ---------- architecture toggle (demo lever) ---------- */

function applyRawMode(enabled) {
  $("raw-toggle").checked = enabled;
  $("arch-toggle-box").classList.toggle("raw", enabled);
  $("arch-toggle-title").textContent =
    enabled ? "Chế độ LLM thuần" : "Kiến trúc đầy đủ";
  $("arch-toggle-hint").textContent = enabled
    ? "Định tuyến, guardrail, truy xuất và kiểm tra dẫn nguồn đang bị bỏ qua trong hội thoại này."
    : "Định tuyến, guardrail, truy xuất và kiểm tra dẫn nguồn đều đang bật.";
  $("raw-banner").classList.toggle("hidden", !enabled);
}

async function toggleRawMode(enabled) {
  try {
    const data = await postJson("/api/session/raw-mode", { session_id: sessionId, enabled });
    sessionId = data.session_id;
    applyRawMode(data.raw_mode);
  } catch (error) {
    applyRawMode(!enabled); // revert the switch - the request didn't take
    addMessage("system", `Could not change mode: ${error.message}`);
  }
}

// The waiting state, shown as three dots in a bubble where the reply will
// appear. Retrieval plus a model call is rarely instant, and a chat that sits
// silent reads as broken rather than busy - the customer's own message is the
// last thing on screen and nothing acknowledges it.
//
// The label changes at three seconds. Not decoration: past a couple of seconds
// people start wondering whether the send worked, and naming what is happening
// ("đang tra cứu tài liệu") answers that without promising a finish time.
let typingTimer = null;

function showTyping() {
  hideTyping();
  const wrap = document.createElement("div");
  wrap.className = "msg assistant typing";
  wrap.id = "typing";
  wrap.appendChild(avatarFor("assistant"));
  const body = document.createElement("div");
  body.className = "msg-body";
  body.innerHTML = '<div class="bubble"><i></i><i></i><i></i></div>'
    + '<div class="typing-label" id="typing-label">Đang soạn câu trả lời…</div>';
  wrap.appendChild(body);
  $("messages").appendChild(wrap);
  $("messages").scrollTop = $("messages").scrollHeight;

  typingTimer = setTimeout(() => {
    const label = $("typing-label");
    if (label) label.textContent = "Đang tra cứu tài liệu đã thẩm định…";
  }, 3000);
}

function hideTyping() {
  clearTimeout(typingTimer);
  const existing = $("typing");
  if (existing) existing.remove();
}

async function send(message) {
  if (!message.trim()) return;
  addMessage("customer", message);
  $("input").value = "";
  $("send").disabled = true;
  showTyping();
  try {
    const data = await postJson("/api/chat", { session_id: sessionId, message });
    hideTyping();
    sessionId = data.session_id;
    applyRawMode(Boolean(data.raw_mode));
    if (data.route === "agent") {
      addMessage("system", data.handled_by
        ? `Đã gửi đến ${data.handled_by}.`
        : "Đã gửi vào hàng chờ chuyên viên - sẽ có người tiếp nhận sớm.");
    } else {
      addMessage("assistant", data.reply, data);
    }
    updateInspector(data);
    renderChatActions(data);
    // Driven by session state: an "@bot" aside is answered by the assistant
    // but does not take the customer out of the queue, so the banner stays.
    renderHandoff(data.in_handoff ? { handled_by: data.handled_by || null } : null);
    if (data.in_handoff) startPolling();
  } catch (error) {
    // Cleared here as well as on the success path: a failed request that
    // leaves the dots animating tells the customer a reply is still coming.
    hideTyping();
    addMessage("system", `Không gửi được: ${error.message}`);
  } finally {
    $("send").disabled = false;
    $("input").focus();
  }
}

/* ---------- contextual chat actions ---------- */

// Buttons that mirror what can also be typed. The offer of a handoff is a
// decision the customer makes, so it gets a button; typing "yes" does the same
// thing, because a chat that only works by clicking is not a chat.
// True from the handoff until the customer leaves. Kept on the client so the
// banner survives turns that return nothing (every message while queued does).
let inHandoff = false;

function renderHandoff(state) {
  inHandoff = !!state;
  const box = $("handoff-banner");
  if (!state) {
    box.classList.add("hidden");
    $("input").placeholder =
      "Nhập câu hỏi, hoặc dùng @agent / @bot để gọi trực tiếp…";
    return;
  }
  const who = state.handled_by
    ? `Anh/chị đang trao đổi với <b>${escapeHtml(state.handled_by)}</b>.`
    : "Anh/chị đang trong hàng chờ gặp chuyên viên.";
  box.innerHTML = `
    <div>
      <strong>Đã chuyển cho chuyên viên</strong>
      <p>${who} Trợ lý ảo đã tạm dừng, nên tin nhắn của anh/chị sẽ tới chuyên
         viên chứ không tới tôi.</p>
      <p class="hint">Gõ <code>/leave</code> để quay lại trợ lý ảo, hoặc
         <code>@bot &lt;câu hỏi&gt;</code> để hỏi tôi mà vẫn giữ chỗ trong
         hàng chờ.</p>
    </div>
    <button class="ghost small" data-say="/leave">Quay lại trợ lý ảo</button>`;
  box.classList.remove("hidden");
  box.querySelector("[data-say]").onclick = () => send("/leave");
  $("input").placeholder = state.handled_by
    ? `Nhắn cho ${state.handled_by}…  (/leave để quay lại trợ lý ảo)`
    : "Nhắn cho chuyên viên…  (/leave để quay lại trợ lý ảo)";
}

function renderChatActions(data) {
  const box = $("chat-actions");
  let html = "";

  if (data.offer_escalation) {
    html = `<span class="action-label">Kết nối anh/chị với chuyên viên nhé?</span>
      <button class="primary small" data-say="có">Có, kết nối giúp tôi</button>
      <button class="ghost small" data-say="không, cảm ơn">Không, tiếp tục hỗ trợ</button>`;
  }
  // No "leave" button here any more - the handoff banner owns that, and owns it
  // permanently. Two copies of the same escape hatch, one of which vanishes on
  // the next turn, is worse than one that stays put.

  box.innerHTML = html;
  box.classList.toggle("hidden", !html);
  box.querySelectorAll("[data-say]").forEach((b) => {
    b.onclick = () => send(b.dataset.say);
  });
}

/* ---------- live agent replies (customer side) ---------- */

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollForAgent, 3000);
}

async function pollForAgent() {
  if (!sessionId) return;
  try {
    const data = await api(
      `/api/chat/poll?session_id=${encodeURIComponent(sessionId)}&since=${renderedCount}`);
    for (const m of data.messages) {
      if (m.role === "agent") addMessage("agent", m.text, null, m.author);
    }
    renderedCount = data.total;
    // An agent claiming the session changes the banner from "in the queue" to
    // naming them, without the customer having to send anything.
    if (inHandoff) renderHandoff({ handled_by: data.handled_by || null });
  } catch {
    // A failed poll is not worth interrupting the customer; the next tick retries.
  }
}

/* ---------- dashboard ---------- */

function statTile(label, value, sub, tone) {
  return `<div class="stat ${tone || ""}">
    <div class="stat-value">${escapeHtml(value)}</div>
    <div class="stat-label">${escapeHtml(label)}</div>
    ${sub ? `<div class="stat-sub">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

async function refreshDashboard() {
  const data = await api("/api/dashboard");
  const m = data.metrics;

  $("dash-scope").textContent =
    `${m.conversations} hội thoại · ${m.turns} lượt trao đổi - mọi số liệu dưới đây ` +
    `được tính từ chính tiến trình đang chạy`;

  const median = m.median_latency_ms;
  const latency = median >= 1000 ? `${(median / 1000).toFixed(1)}s` : `${median} ms`;

  $("stat-grid").innerHTML = [
    statTile("Tỷ lệ tự xử lý", pct(m.deflection_rate),
             `${m.deflected} được giải quyết không cần nhân viên`, "good"),
    statTile("Thời gian phản hồi trung vị", latency,
             `mốc tổng đài truyền thống ${m.baseline_response_seconds}s`, "good"),
    statTile("Câu trả lời có dẫn nguồn", pct(m.grounding_rate),
             `${m.grounded_answers}/${m.knowledge_answers} câu trả lời tra cứu`),
    statTile("Chuyển cho nhân viên", String(m.escalated),
             "mỗi ca đều có bản tóm tắt bàn giao"),
    statTile("Yêu cầu bị chặn theo quy định", String(m.guardrail_blocks),
             "từ chối trước khi model xử lý"),
    statTile("Thời gian nhân viên tiết kiệm được", `${(m.estimated_minutes_saved / 60).toFixed(1)} h`,
             `ước tính · ${m.assumptions.handle_minutes_per_deflected_conversation} phút/hội thoại`,
             "estimate"),
  ].join("");

  const total = Object.values(m.route_mix).reduce((a, b) => a + b, 0) || 1;
  $("route-bars").innerHTML = Object.entries(m.route_mix).map(([route, n]) => `
    <div class="bar-row">
      <span class="bar-label"><span class="chip ${route}">${route}</span></span>
      <span class="bar"><i class="${route}" style="width:${(n / total) * 100}%"></i></span>
      <span class="bar-n">${n}</span>
    </div>`).join("") +
    `<p class="hint" style="margin:12px 0 0">${escapeHtml(m.assumptions.note)}</p>`;

  $("activity").innerHTML = data.activity.map((a) => `
    <button class="queue-item ${a.escalated ? "alert" : ""}" data-id="${escapeHtml(a.session_id)}">
      <strong>${escapeHtml(a.last_utterance || "(không có tin nhắn)")}</strong>
      <small>${escapeHtml(a.customer_name || "chưa xác định")} · ${a.turns} lượt ·
        route gần nhất <b>${escapeHtml(a.last_route)}</b> · ${a.age_seconds}s trước</small>
      ${a.escalated ? `<small>đã chuyển nhân viên - ${escapeHtml(a.reason || "")}</small>` : ""}
    </button>`).join("") ||
    '<p class="empty">Chưa có hội thoại nào. Dùng "Nạp dữ liệu demo", hoặc chat thử với vai khách hàng.</p>';

  $("activity").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => { showPanel("queue"); openSession(b.dataset.id); };
  });

  $("queue-count").textContent = data.queue.length;
  $("queue-count").classList.toggle("zero", data.queue.length === 0);

  const t = data.topics || { topics: [], singletons: [] };
  $("topic-count").textContent =
    `${t.total_unresolved || 0} lượt chưa trả lời được, ${t.distinct_questions || 0} câu hỏi khác nhau`;
  $("topics").innerHTML =
    (t.topics.length
      ? t.topics.map((c) => `
          <div class="topic">
            <div class="row-between">
              <strong>${escapeHtml(c.label)}</strong>
              <span class="chip escalation">${c.size}x</span>
            </div>
            <ul>${c.questions.map((q) => `<li>${escapeHtml(q)}</li>`).join("")}</ul>
          </div>`).join("")
      : '<p class="empty">Chưa có khoảng trống lặp lại nào - mọi câu chưa trả lời được đến giờ đều là ca đơn lẻ.</p>')
    + (t.singletons.length
      ? `<p class="hint" style="margin-top:10px">Và ${t.singletons.length} câu hỏi đơn lẻ khác: `
        + t.singletons.map((c) => escapeHtml(c.questions[0])).join("; ") + "</p>"
      : "");

  const p = data.policy || {};
  $("policy-box").innerHTML = [
    ["Mức độ nghiêm ngặt", p.strictness], ["Nguồn cấu hình", p.source],
    ["Ngưỡng dẫn nguồn", p.min_grounding], ["Ngưỡng liên quan", p.min_relevance],
    ["Ngưỡng nhận diện ý định", p.high_confidence],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Càng cao thì càng hay từ chối, càng ít sai. '
   + 'Càng thấp thì trả lời nhiều hơn, sai nhiều hơn. Sửa trong config.json; áp dụng khi tải lại.</p>';

  const c = data.campaigns || {};
  $("campaign-box").innerHTML = [
    ["Chương trình", c.campaigns], ["Khách hàng được nhắm tới", c.customers_targeted],
    ["Nguồn dữ liệu", c.source], ["Tuổi dữ liệu", c.age_hours != null ? `${c.age_hours} giờ` : "-"],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Trích xuất CRM qua đêm. Điều kiện tham gia được '
   + 'đọc từ dữ liệu, không suy diễn - ngân hàng quyết định ai được nhắm tới.</p>';

  const mem = data.memory || {};
  $("memory-box").innerHTML = [
    ["Khách hàng được ghi nhớ", mem.customers], ["Ghi chú lưu lại", mem.notes],
    ["Thời hạn lưu", mem.ttl_days != null ? `${mem.ttl_days} ngày` : "-"],
    ["Lưu trữ", mem.retains],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Chỉ ghi sau khi đã xác thực, '
   + 'và xoá cùng với các phiên hội thoại.</p>';

  const rt = data.router || {};
  $("router-mode").textContent = `chế độ: ${rt.mode || "nlu"}`;
  if (!rt.comparisons) {
    $("router-box").innerHTML = rt.mode === "nlu"
      ? '<p class="empty">Chỉ dùng bộ phân loại lexical. Đặt "router_mode": "shadow" '
        + 'trong config.json để bắt đầu thu thập số liệu so sánh.</p>'
      : '<p class="empty">Chưa có lượt nào được so sánh.</p>';
  } else {
    $("router-box").innerHTML =
      [["Số lượt so sánh", rt.comparisons],
       ["Trùng khớp", `${rt.agreements} (${pct(rt.agreement_rate)})`],
       ["Lệch", rt.comparisons - rt.agreements]]
        .map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`)
        .join("")
      + (rt.disagreements.length
        ? `<table class="events" style="margin-top:10px"><thead><tr>
             <th>Câu hỏi</th><th>Lexical</th><th>LLM</th><th>Đã dùng</th></tr></thead><tbody>`
          + rt.disagreements.map((d) => `<tr>
              <td>${escapeHtml(d.text)}</td>
              <td>${escapeHtml(d.lexical)} <em>${d.lexical_confidence ?? ""}</em></td>
              <td>${escapeHtml(d.llm)} <em>${d.llm_confidence ?? ""}</em></td>
              <td>${escapeHtml(d.used)}</td></tr>`).join("")
          + "</tbody></table>"
        : "");
  }

  const dbs = data.database || {};
  const byStatus = dbs.cards_by_status || {};
  $("db-box").innerHTML = [
    ["Khách hàng", dbs.customers], ["Thẻ", dbs.cards],
    ["Thay đổi trạng thái thẻ", dbs.card_events],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + Object.entries(byStatus).map(([k, v]) =>
       `<div class="kv"><span>&nbsp;&nbsp;${escapeHtml(k)}</span><span>${v}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">sqlite, được nạp lại bởi Xoá toàn bộ phiên '
   + 'và Nạp dữ liệu demo.</p>';

  const events = data.card_events || [];
  $("card-events").innerHTML = events.length
    ? `<table class="events"><thead><tr><th>Card</th><th>Transition</th>
        <th>Action</th><th>By</th><th>Ref</th><th>When</th></tr></thead><tbody>`
      + events.map((e) => `<tr>
          <td><code>${escapeHtml(e.card_id)}</code></td>
          <td>${escapeHtml(e.from_status || "-")} &rarr; <b>${escapeHtml(e.to_status)}</b></td>
          <td>${escapeHtml(e.action)}</td>
          <td>${escapeHtml(e.actor)}</td>
          <td>${escapeHtml(e.reference || "-")}</td>
          <td>${escapeHtml((e.at || "").replace("T", " ").replace("+00:00", ""))}</td>
        </tr>`).join("") + "</tbody></table>"
    : '<p class="empty">No card actions yet. Block or freeze a card in the customer chat and it appears here.</p>';
}

/* ---------- queue ---------- */

async function refreshQueue() {
  const data = await api("/api/queue");
  $("queue-count").textContent = data.sessions.length;
  $("queue-count").classList.toggle("zero", data.sessions.length === 0);

  $("queue").innerHTML = data.sessions.map((s) => `
    <button class="queue-item alert ${s.session_id === selectedSession ? "active" : ""}"
            data-id="${escapeHtml(s.session_id)}">
      <strong>${escapeHtml(s.customer_name || "Khách chưa xác định")}</strong>
      <small>${s.turns} lượt · ${s.verified ? "đã xác thực" : "chưa xác thực"}</small>
      <small>${escapeHtml(s.reason || "")}</small>
    </button>`).join("") || '<p class="empty">Không có ca nào đang chờ.</p>';

  $("queue").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => openSession(b.dataset.id);
  });
}

async function openSession(id) {
  selectedSession = id;
  $("handover-empty").classList.add("hidden");
  $("handover").classList.remove("hidden");
  $("handover-title").textContent = "Hội thoại";
  $("summary").textContent = "Đang tạo bản tóm tắt bàn giao…";

  const data = await postJson("/api/summary", { session_id: id });
  $("summary").innerHTML = render(data.summary);
  $("summary-source").textContent = data.generated
    ? `viết bởi ${data.model}` : "mẫu offline";
  $("transcript").textContent = data.transcript;
  $("handled-by").textContent = data.handled_by ? `đang xử lý bởi ${data.handled_by}` : "";
  $("audit").innerHTML = data.audit.map((row) => `
    <div class="audit-row">
      <span class="turn">#${row.turn}</span>
      <span class="chip ${row.route}">${row.route}</span>
      <span class="utt" title="${escapeHtml(row.utterance)}">${escapeHtml(row.utterance)}</span>
      <span class="turn">${row.actor ? escapeHtml(row.actor) + " · " : ""}${row.latency_ms}ms</span>
    </div>`).join("");
  refreshQueue();
}

async function sendAgentReply(event) {
  event.preventDefault();
  clearError($("reply-error"));
  const message = $("reply-input").value.trim();
  if (!message || !selectedSession) return;
  try {
    await postJson("/api/session/reply", { session_id: selectedSession, message });
    $("reply-input").value = "";
    await openSession(selectedSession);
  } catch (error) {
    showError($("reply-error"), error.message);
  }
}

/* The grounding comparison and the safeguard probes used to live here as two
   console tabs. They were demonstrations, not operating tools - a contact
   centre console is where escalations get worked, and an experiment about the
   architecture sitting beside the live queue invites someone to run it on a
   real shift. Both are now scripted demo steps: see KICH-BAN-TEST.txt sections
   B4 and B1/B3/B7/B8, and `python compare_grounding.py` for the side-by-side. */

/* ---------- knowledge base ---------- */

async function refreshKb() {
  const data = await api("/api/kb");
  $("kb-stats").textContent =
    `${data.stats.passages} đoạn trích có thể truy xuất trong ${data.stats.documents} tài liệu`;

  $("kb-list").innerHTML = data.documents.map((d) => `
    <button class="queue-item ${d.filename === selectedDoc ? "active" : ""}"
            data-name="${escapeHtml(d.filename)}">
      <strong>${escapeHtml(d.title)}</strong>
      <small>${escapeHtml(d.filename)} · ${escapeHtml(d.format)}</small>
      <small>${d.passages} đoạn · ${(d.bytes / 1024).toFixed(1)} KB</small>
    </button>`).join("") ||
    '<p class="empty">Chưa có tài liệu. Mọi câu hỏi về kiến thức sẽ được chuyển tiếp.</p>';

  $("kb-list").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => openDoc(b.dataset.name);
  });
}

async function openDoc(filename) {
  selectedDoc = filename;
  const data = await api(`/api/kb/doc?name=${encodeURIComponent(filename)}`);
  $("kb-empty").classList.add("hidden");
  $("kb-editor").classList.add("hidden");
  $("kb-view").classList.remove("hidden");
  $("kb-title").textContent = filename;
  $("kb-edit").classList.toggle("hidden", !data.editable);
  $("kb-passage-count").textContent = `${data.passages.length} đoạn trích`;
  $("kb-raw").textContent = data.content;
  $("kb-passages").innerHTML = data.passages.map((p) => `
    <div class="passage">
      <h4>${escapeHtml(p.heading)}</h4>
      <div class="cite">${escapeHtml(p.breadcrumb || p.citation)}</div>
      <p>${escapeHtml(p.text.slice(0, 320))}${p.text.length > 320 ? "…" : ""}</p>
    </div>`).join("");
  refreshKb();
}

function openEditor(filename, content) {
  clearError($("kb-error"));
  $("kb-empty").classList.add("hidden");
  $("kb-view").classList.add("hidden");
  $("kb-editor").classList.remove("hidden");
  $("kb-editor-title").textContent = filename ? `Sửa ${filename}` : "Thêm tài liệu";
  $("kb-filename").value = filename || "";
  $("kb-filename").disabled = Boolean(filename);
  $("kb-content").value = content || "";
}

async function saveDoc() {
  clearError($("kb-error"));
  $("kb-save").disabled = true;
  try {
    const data = await postJson("/api/kb/upload", {
      filename: $("kb-filename").value, content: $("kb-content").value,
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
  if (!confirm(`Xoá ${selectedDoc}? Các đoạn trích từ tài liệu này sẽ ngừng được truy xuất ngay lập tức.`)) return;
  await postJson("/api/kb/delete", { filename: selectedDoc });
  selectedDoc = null;
  $("kb-view").classList.add("hidden");
  $("kb-empty").classList.remove("hidden");
  refreshKb();
}

// The browser reads the file and posts it as base64 in JSON. Multipart would
// need a hand-written parser server-side (Python 3.14 removed `cgi`), and at a
// 256 KB cap its only real advantage - streaming - does not apply.
function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Không đọc được tệp này"));
    reader.onload = () => {
      const comma = reader.result.indexOf(",");
      resolve(comma === -1 ? reader.result : reader.result.slice(comma + 1));
    };
    reader.readAsDataURL(file);
  });
}

async function uploadFile(file) {
  if (!file) return;
  clearError($("kb-upload-error"));
  $("kb-drop").classList.add("busy");
  try {
    const data = await postJson("/api/kb/upload", {
      filename: file.name, file_base64: await readAsBase64(file),
    });
    await refreshKb();
    await openDoc(data.document.filename);
  } catch (error) {
    showError($("kb-upload-error"), error.message);
  } finally {
    $("kb-drop").classList.remove("busy");
    $("kb-file").value = "";
  }
}

/* ---------- settings ---------- */

async function refreshProviders() {
  const data = await api("/api/providers");
  const active = data.active;

  $("provider-list").innerHTML = data.providers.map((p) => {
    const status = p.available ? "sẵn sàng"
      : !p.sdk_installed ? `pip install ${p.package}`
      : `chưa có key - đặt ${p.env_key}`;
    return `<button class="queue-item ${active.provider === p.name ? "active" : ""}"
              data-name="${escapeHtml(p.name)}">
      <strong>${escapeHtml(p.name)}${active.provider === p.name ? " · đang dùng" : ""}</strong>
      <small>${escapeHtml(p.default_model)}</small>
      <small>${escapeHtml(status)}${p.key_masked ? ` · key ${escapeHtml(p.key_masked)}` : ""}</small>
    </button>`;
  }).join("");

  $("provider-list").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => { $("cfg-provider").value = b.dataset.name; $("cfg-key").focus(); };
  });

  const rows = [["Chế độ", active.mode], ["Nhà cung cấp", active.provider || "-"],
                ["Model", active.model || "-"], ["Mức suy luận", active.effort || "-"]];
  if (active.tone) rows.push(["Văn phong", active.tone]);
  rows.push(["Nhiệt độ",
    active.temperature === null || active.temperature === undefined
      ? "mặc định của nhà cung cấp" : String(active.temperature)]);
  if (active.endpoint) rows.push(["Điểm cuối", active.endpoint]);
  if (active.detail) rows.push(["Chi tiết", active.detail]);
  $("active-config").innerHTML = rows.map(([k, v]) =>
    `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("");

  if (active.requested) $("cfg-provider").value = active.requested;
  if (active.effort) $("cfg-effort").value = active.effort;
  if (active.tone) $("cfg-tone").value = active.tone;
  // Left blank when unset, because blank is what means "provider default" on
  // the way back in. Writing a number here would silently pin it.
  $("cfg-temp").value =
    active.temperature === null || active.temperature === undefined
      ? "" : active.temperature;
  const selected = data.providers.find((p) => p.name === $("cfg-provider").value);
  $("cfg-key-hint").textContent = selected && selected.key_set
    ? `- ${selected.key_masked} stored; blank keeps it` : "- stored in memory only";
  $("cfg-model").placeholder = selected ? selected.default_model : "";
}

async function saveConfig(clearKey) {
  clearError($("cfg-error"));
  $("cfg-save").disabled = true;
  try {
    const body = {
      provider: $("cfg-provider").value, model: $("cfg-model").value,
      effort: $("cfg-effort").value, base_url: $("cfg-base").value,
      tone: $("cfg-tone").value, temperature: $("cfg-temp").value,
    };
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

/* ---------- auth ---------- */

function renderStaff() {
  $("staff-pill").classList.toggle("hidden", !staff);
  $("logout").classList.toggle("hidden", !staff);
  $("login-btn").classList.toggle("hidden", Boolean(staff));
  // The raw-mode switch bypasses every guardrail, so it is an admin control,
  // not a customer-facing one - it only exists in the DOM at all once staff
  // has signed in, mirroring how the endpoint behind it is gated server-side.
  $("view-customer-btn").classList.toggle("hidden", !staff);
  $("view-console-btn").classList.toggle("hidden", !staff);
  $("arch-toggle-box").classList.toggle("hidden", !staff);
  if (staff) $("staff-pill").textContent = `${staff.display_name} · ${staff.role}`;
}

async function refreshStaff() {
  staff = (await api("/api/auth/me")).staff;
  renderStaff();
}

async function login(event) {
  event.preventDefault();
  clearError($("login-error"));
  try {
    const data = await postJson("/api/auth/login", {
      username: $("login-user").value, password: $("login-pass").value,
    });
    staff = data.staff;
    $("login-pass").value = "";
    renderStaff();
    showScreen("agent");
    showPanel("dashboard");
  } catch (error) {
    showError($("login-error"), error.message);
  }
}

async function logout() {
  // Leave no live conversation stuck in raw mode behind a sign-out - the
  // endpoint that turns it off requires staff, so it has to happen first.
  if ($("raw-toggle").checked) await toggleRawMode(false);
  await postJson("/api/auth/logout", {});
  staff = null;
  renderStaff();
  showScreen("customer");
}

/* ---------- health ---------- */

async function refreshHealth() {
  const h = await api("/api/health");
  const pill = $("mode-pill");
  pill.textContent = h.mode === "live" ? `${h.provider}: ${h.model}` : "Model: ngoại tuyến";
  pill.title = h.detail || `mức suy luận: ${h.effort}`;
  pill.className = `pill ${h.mode}`;
}

/* ---------- voice (TTS/STT) ---------- */

let voiceAvailable = true;   // luôn hiện mic & loa, lỗi sẽ báo sau
let currentAudio = null;
let currentTtsAbort = null;

async function checkVoiceStatus() {
  // Kiểm tra TTS của chúng ta (không cần Vbee voice.py)
  try {
    const s = await api("/api/tts/provider");
    voiceAvailable = Boolean(s.gtts_available || s.vbee_available);
  } catch { voiceAvailable = true; }
  $("mic-btn").classList.toggle("hidden", false); // luôn hiện mic
}

function makeVoiceButton(text) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "voice-btn";
  btn.title = "Nghe câu trả lời";
  btn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
    <path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/>
  </svg>`;
  btn.onclick = () => playTts(text, btn);
  return btn;
}

// Phát TTS qua endpoint /api/tts (trả về binary audio)
async function playTts(text, btn) {
  // Nếu đang phát hoặc đang tải → dừng lại
  if (btn.classList.contains("playing")) {
    if (currentTtsAbort) { currentTtsAbort.abort(); currentTtsAbort = null; }
    stopCurrentAudio();
    return;
  }
  if (currentTtsAbort) { currentTtsAbort.abort(); currentTtsAbort = null; }
  stopCurrentAudio();
  if ($("tts-stop-btn")) $("tts-stop-btn").classList.remove("hidden");

  const origHTML = btn.innerHTML;
  btn.classList.add("playing");
  btn.title = "Đang tải… (nhấn để dừng)";
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="2.2" stroke-linecap="round" class="spin">
    <path d="M21 12a9 9 0 1 1-9-9"/>
  </svg>`;

  const abortCtrl = new AbortController();
  currentTtsAbort = abortCtrl;

  try {
    const resp = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.replace(/[*_`#>]/g, "") }),
      signal: abortCtrl.signal,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.status }));
      throw new Error(err.error || resp.status);
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    const cleanup = () => { URL.revokeObjectURL(url); _resetVoiceBtn(btn, origHTML); };
    audio._cleanup = cleanup;
    currentAudio = audio;
    // Khi đang phát: đổi icon thành pause
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
      <rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>
    </svg>`;
    btn.title = "Đang phát – nhấn để dừng";
    audio.onended = cleanup;
    audio.onerror = cleanup;
    currentTtsAbort = null;
    await audio.play();
    return; // không chạy reset nếu audio đang phát
  } catch (err) {
    if (err.name !== "AbortError") {
      addMessage("system", `Không phát được giọng nói: ${err.message}`);
    }
  }
  currentTtsAbort = null;
  _resetVoiceBtn(btn, origHTML);
}

function _resetVoiceBtn(btn, origHTML) {
  btn.classList.remove("playing");
  btn.innerHTML = origHTML;
  btn.title = "Nghe câu trả lời";
  if ($("tts-stop-btn")) $("tts-stop-btn").classList.add("hidden");
  currentAudio = null;
}

function stopCurrentAudio() {
  if (currentTtsAbort) { currentTtsAbort.abort(); currentTtsAbort = null; }
  if (currentAudio) {
    currentAudio.pause();
    if (currentAudio._cleanup) currentAudio._cleanup();
    currentAudio = null;
  }
  if ($("tts-stop-btn")) $("tts-stop-btn").classList.add("hidden");
}

// Vbee's realtime TTS caps a single request at 300 characters, so a long
// reply comes back as several chunks (see app/voice.py _chunk_text). They are
// played back to back with one <audio> element rather than all at once, so
// the customer hears one continuous reply instead of overlapping voices.
function playChunks(chunks, format) {
  return new Promise((resolve) => {
    if (!chunks.length) { resolve(); return; }
    let i = 0;
    const audio = new Audio();
    currentAudio = audio;
    audio.onended = () => { i += 1; if (i < chunks.length) playNext(); else resolve(); };
    audio.onerror = () => resolve();
    function playNext() {
      audio.src = `data:audio/${format};base64,${chunks[i]}`;
      audio.play().catch(() => resolve());
    }
    playNext();
  });
}

async function playReply(text, btn) {
  stopCurrentAudio();
  if ($("tts-stop-btn")) $("tts-stop-btn").classList.remove("hidden");
  btn.disabled = true;
  btn.classList.add("playing");
  // Vbee's TTS runs as a background job now (see app/voice.py) - the request
  // can take a few seconds before any audio exists, so the button says so
  // rather than showing the pause icon before there is anything to pause.
  btn.textContent = "⏳";
  try {
    // Markdown marks (**bold**, `code`) read out as literal asterisks and
    // backticks otherwise - stripped here rather than in the reply text
    // itself, which still needs them for the on-screen rendering.
    const spoken = text.replace(/[*_`]/g, "");
    const data = await postJson("/api/voice/tts", { text: spoken });
    if (data.error && (!data.chunks || !data.chunks.length)) throw new Error(data.error);
    btn.textContent = "⏸";
    await playChunks(data.chunks || [], data.format || "mp3");
  } catch (error) {
    addMessage("system", `Không phát được giọng nói: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.classList.remove("playing");
    btn.textContent = "🔊";
  }
}

/* ---- voice input: mic -> WAV -> Vbee STT -> fills the composer ---- */

// Matches Vbee Realtime STT's own ceiling - stopping locally means the
// customer sees "recording stopped" instantly instead of waiting on a round
// trip just to be told afterwards that the clip was too long.
const MAX_RECORD_MS = 10000;
// One of Vbee's accepted sample rates, and roughly a third the size of the
// browser's native 44.1/48 kHz - smaller upload, faster round trip.
const RECORD_SAMPLE_RATE = 16000;

let recorder = null;

function encodeWav(samples, sourceRate, targetRate) {
  const ratio = sourceRate / targetRate;
  const outLength = Math.floor(samples.length / ratio);
  const resampled = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIndex = i * ratio;
    const lo = Math.floor(srcIndex);
    const hi = Math.min(lo + 1, samples.length - 1);
    const frac = srcIndex - lo;
    resampled[i] = samples[lo] + (samples[hi] - samples[lo]) * frac;
  }

  const pcm = new Int16Array(resampled.length);
  for (let i = 0; i < resampled.length; i++) {
    const s = Math.max(-1, Math.min(1, resampled[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }

  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + pcm.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);           // PCM
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, targetRate, true);
  view.setUint32(28, targetRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, pcm.length * 2, true);
  new Int16Array(buffer, 44).set(pcm);
  return new Blob([buffer], { type: "audio/wav" });
}

function setMicUI(state) {
  const btn = $("mic-btn");
  btn.classList.toggle("recording", state === "recording");
  btn.disabled = state === "processing";
  btn.title = state === "recording" ? "Đang ghi âm - bấm để dừng"
    : state === "processing" ? "Đang nhận diện giọng nói…"
    : "Nhập bằng giọng nói";
  btn.textContent = state === "recording" ? "⏹" : "🎙️";
}

async function startRecording() {
  stopCurrentAudio(); // avoid the mic picking up the assistant's own voice
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    addMessage("system", "Không truy cập được micro - hãy kiểm tra quyền truy cập trình duyệt.");
    return;
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  // Routed to destination through a silent gain node: onaudioprocess needs a
  // connected graph to fire in some browsers, but connecting the mic straight
  // to destination would play the customer's own voice back to them.
  const mute = ctx.createGain();
  mute.gain.value = 0;
  const samples = [];

  processor.onaudioprocess = (e) => samples.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  source.connect(processor);
  processor.connect(mute);
  mute.connect(ctx.destination);

  recorder = { stream, ctx, source, processor, mute, samples,
              timeout: setTimeout(finishRecording, MAX_RECORD_MS) };
  setMicUI("recording");
}

async function finishRecording() {
  if (!recorder) return;
  const { stream, ctx, source, processor, mute, samples, timeout } = recorder;
  recorder = null;
  clearTimeout(timeout);
  processor.disconnect();
  source.disconnect();
  mute.disconnect();
  stream.getTracks().forEach((t) => t.stop());

  const total = samples.reduce((sum, s) => sum + s.length, 0);
  const merged = new Float32Array(total);
  let offset = 0;
  for (const chunk of samples) { merged.set(chunk, offset); offset += chunk.length; }
  const sourceRate = ctx.sampleRate;
  await ctx.close();

  if (total / sourceRate < 0.3) {
    setMicUI("idle"); // too short to be a real question - most likely a mis-tap
    return;
  }

  setMicUI("processing");
  try {
    const wav = encodeWav(merged, sourceRate, RECORD_SAMPLE_RATE);
    const audioBase64 = await readAsBase64(wav);
    const data = await postJson("/api/stt", { audio_b64: audioBase64 });
    if (data.error) throw new Error(data.error);
    // Fills the composer rather than sending straight away - a misheard digit
    // in a verification code is exactly the kind of STT error a customer
    // needs the chance to see and fix before it goes anywhere.
    $("input").value = (data.text || "").trim();
    $("input").focus();
  } catch (error) {
    addMessage("system", `Không nhận diện được giọng nói: ${error.message}`);
  } finally {
    setMicUI("idle");
  }
}

function toggleRecording() {
  if (recorder) finishRecording(); else startRecording();
}

/* ---------- wiring ---------- */

$("raw-toggle").onchange = (e) => toggleRawMode(e.target.checked);

$("composer").onsubmit = (e) => { e.preventDefault(); send($("input").value); };
$("mic-btn").onclick = toggleRecording;

// Nút dừng TTS
if ($("tts-stop-btn")) {
  $("tts-stop-btn").onclick = stopCurrentAudio;
}

// Suggestions: ẩn khi user gửi tin nhắn đầu tiên
document.querySelectorAll("#suggestions button").forEach((btn) => {
  btn.onclick = () => {
    send(btn.textContent.trim());
    $("suggestions").style.display = "none";
  };
});

// Starting over drops the server-side session too. Clearing only the visible
// transcript would leave the customer talking to a conversation they cannot
// see - including any pending verification or handoff.
function newConversation() {
  sessionId = null;
  renderedCount = 0;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  hideTyping();
  $("messages").innerHTML = "";
  renderChatActions({});
  renderHandoff(null);
  $("inspector").innerHTML =
    '<p class="empty">Gửi một câu hỏi để xem nguồn.</p>';
  setRailUser(null);
  greet();
  $("input").focus();
}
$("new-chat").onclick = newConversation;
$("clear-chat").onclick = newConversation;

// Decorative controls are inert by design. Saying so out loud beats a silent
// no-op that leaves someone clicking a dead button during a demo.
document.querySelectorAll("[data-demo]").forEach((el) => {
  el.title = (el.title ? el.title + " - " : "") + "minh hoạ giao diện, chưa hoạt động";
  el.addEventListener("click", (e) => e.preventDefault());
});

$("login-btn").onclick = () => { showScreen("login"); $("login-pass").focus(); };
$("login-cancel").onclick = () => showScreen("customer");
$("login-form").onsubmit = login;
$("logout").onclick = logout;
$("view-customer-btn").onclick = () => showScreen("customer");
$("view-console-btn").onclick = () => { showScreen("agent"); showPanel("dashboard"); };

document.querySelectorAll(".subtab").forEach((t) => {
  t.onclick = () => showPanel(t.dataset.panel);
});

$("dash-refresh").onclick = refreshDashboard;
$("clear-btn").onclick = async () => {
  if (!confirm("Xoá toàn bộ hội thoại trong tiến trình này? " +
               "Bảng điều khiển sẽ về 0 và các bản ghi hội thoại sẽ mất.")) return;
  const d = await postJson("/api/sessions/clear", {});
  sessionId = null;
  renderedCount = 0;
  selectedSession = null;
  $("handover").classList.add("hidden");
  $("handover-empty").classList.remove("hidden");
  await refreshDashboard();
  alert(`Đã xoá ${d.removed} hội thoại.`);
};
$("seed-btn").onclick = async () => {
  $("seed-btn").disabled = true;
  try { await postJson("/api/replay", {}); await refreshDashboard(); }
  finally { $("seed-btn").disabled = false; }
};

$("regen").onclick = () => selectedSession && openSession(selectedSession);
$("reply-form").onsubmit = sendAgentReply;

$("kb-upload-btn").onclick = () => $("kb-file").click();
$("kb-file").onchange = (e) => uploadFile(e.target.files[0]);
$("kb-new").onclick = () => openEditor(null, "");
$("kb-cancel").onclick = () => {
  $("kb-editor").classList.add("hidden");
  (selectedDoc ? $("kb-view") : $("kb-empty")).classList.remove("hidden");
};
$("kb-template").onclick = () => { $("kb-content").value = KB_TEMPLATE; };
$("kb-save").onclick = saveDoc;
$("kb-delete").onclick = deleteDoc;
$("kb-edit").onclick = async () => {
  const d = await api(`/api/kb/doc?name=${encodeURIComponent(selectedDoc)}`);
  openEditor(selectedDoc, d.content);
};

const drop = $("kb-drop");
["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (e) => uploadFile(e.dataTransfer.files[0]));
drop.onclick = () => $("kb-file").click();

$("cfg-save").onclick = () => saveConfig(false);
$("cfg-clear").onclick = () => saveConfig(true);
$("cfg-provider").onchange = refreshProviders;
$("cfg-key-toggle").onclick = () => {
  const f = $("cfg-key");
  const hidden = f.type === "password";
  f.type = hidden ? "text" : "password";
  $("cfg-key-toggle").textContent = hidden ? "Ẩn" : "Hiện";
};

/* ---------- TTS bar (khung chat) ---------- */

function _ttsToggleVbeeFields() {
  const isVbee = $("tts-provider") && $("tts-provider").value === "vbee";
  if ($("tts-voice-field")) $("tts-voice-field").style.display = isVbee ? "" : "none";
}

async function refreshTtsStatus() {
  try {
    const s = await api("/api/tts/provider");
    if ($("tts-provider")) $("tts-provider").value = s.provider;
    if (s.vbee_voice && $("tts-voice")) $("tts-voice").value = s.vbee_voice;
    if ($("tts-status")) {
      $("tts-status").textContent = s.provider === "vbee"
        ? (s.vbee_available ? `Vbee AI – giọng ${s.vbee_voice}` : "Vbee – chưa cấu hình credentials")
        : "gTTS – Google (đang dùng)";
    }
    _ttsToggleVbeeFields();
    _syncTtsBar(s);
  } catch (_) {}
}

async function saveTtsConfig() {
  if ($("tts-error")) clearError($("tts-error"));
  if ($("tts-save")) $("tts-save").disabled = true;
  try {
    const body = { provider: $("tts-provider").value };
    if (body.provider === "vbee" && $("tts-voice")) body.vbee_voice = $("tts-voice").value;
    await postJson("/api/tts/provider", body);
    await refreshTtsStatus();
  } catch (err) {
    if ($("tts-error")) showError($("tts-error"), err.message);
  } finally {
    if ($("tts-save")) $("tts-save").disabled = false;
  }
}

function _syncTtsBar(s) {
  if ($("tts-bar-provider")) $("tts-bar-provider").value = s.provider;
  if ($("tts-bar-voice")) {
    $("tts-bar-voice").value = s.vbee_voice || "hn_female_ngochuyen_full_48k-fhg";
    $("tts-bar-voice").style.display = s.provider === "vbee" ? "" : "none";
  }
}

async function _applyTtsBar() {
  const provider = $("tts-bar-provider").value;
  const body = { provider };
  if (provider === "vbee" && $("tts-bar-voice")) body.vbee_voice = $("tts-bar-voice").value;
  try {
    await postJson("/api/tts/provider", body);
    if ($("tts-bar-voice")) $("tts-bar-voice").style.display = provider === "vbee" ? "" : "none";
    if ($("tts-provider")) $("tts-provider").value = provider;
    if (body.vbee_voice && $("tts-voice")) $("tts-voice").value = body.vbee_voice;
    _ttsToggleVbeeFields();
  } catch (_) {}
}

if ($("tts-bar-provider")) $("tts-bar-provider").onchange = _applyTtsBar;
if ($("tts-bar-voice")) $("tts-bar-voice").onchange = _applyTtsBar;
if ($("tts-save")) $("tts-save").onclick = saveTtsConfig;
if ($("tts-provider")) $("tts-provider").onchange = _ttsToggleVbeeFields;

function greet() {
  addMessage("assistant",
    "Xin chào! Tôi là trợ lý ảo của **ABC Bank**. Tôi có thể tra cứu số dư và "
    + "giao dịch, khoá hoặc mở khoá thẻ, kiểm tra hồ sơ vay, và giải đáp về sản "
    + "phẩm, biểu phí của ngân hàng.\n\nAnh/chị cần hỗ trợ điều gì ạ?");
}

// The rail footer names whoever the session has verified, so the demo shows
// identity being established rather than just asserted in a chat bubble.
function setRailUser(customer) {
  $("rail-user").textContent = customer ? customer.name : "Khách";
  $("rail-user-sub").textContent = customer
    ? `Đã xác thực · ${customer.segment || "MASS"}` : "Chưa xác thực";
  const avatar = document.querySelector(".rail-foot .avatar");
  if (avatar) avatar.textContent = customer ? customer.name.trim().slice(-1) : "K";
}

(async function init() {
  await refreshHealth();
  await refreshStaff();
  await checkVoiceStatus();
  refreshTtsStatus();
  greet();
  if (staff) { showScreen("agent"); showPanel("dashboard"); }
})();
