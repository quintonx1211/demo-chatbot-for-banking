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

const showError = (box, msg) => { box.textContent = msg; box.classList.remove("hidden"); };
const clearError = (box) => { box.textContent = ""; box.classList.add("hidden"); };
const pct = (n) => `${Math.round(n * 100)}%`;

/** Build an element in one call - the transcript is assembled node by node so
    that customer data never travels through innerHTML. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---------- screens ---------- */

function showScreen(name) {
  ["customer", "login", "agent"].forEach((s) =>
    $(`screen-${s}`).classList.toggle("hidden", s !== name));
  $("brand-sub").textContent =
    name === "agent" ? "Bảng điều khiển nhân viên" : "Trợ lý ảo";
  $("nav-assistant").classList.toggle("active", name === "customer");
  setRail(false);
}

/* ---------- live assistant status ---------- */

// The header's second line reports what the assistant is doing right now.
// Derived from state rather than set at each call site, so the three inputs
// can never disagree about what the line should say.
let isSending = false;
let rawMode = false;
let handoff = null;      // { handled_by } once the customer is with a human

function refreshChatStatus() {
  let text = "Trực tuyến";
  let tone = "ok";

  if (rawMode) {
    text = "Chế độ LLM thuần";
    tone = "alert";
  } else if (handoff) {
    text = handoff.handled_by
      ? `Đang với ${handoff.handled_by}` : "Đang chờ chuyên viên";
    tone = "wait";
  }
  if (isSending) text = "Đang trả lời…";

  $("chat-status").className = `chat-status ${tone}`;
  $("chat-status-text").textContent = text;
}

function showPanel(name) {
  ["dashboard", "queue", "kb", "settings"].forEach((p) =>
    $(`panel-${p}`).classList.toggle("hidden", p !== name));
  document.querySelectorAll(".subtab").forEach((t) =>
    t.classList.toggle("active", t.dataset.panel === name));
  const load = { dashboard: refreshDashboard, queue: refreshQueue,
                 kb: refreshKb, settings: refreshProviders };
  if (load[name]) load[name]();
}

/* ---------- customer chat ---------- */

// Screen readers get told who is speaking; sighted users get the bubble side,
// colour and avatar, which carry the same information.
const SPEAKER = {
  customer: "Anh/chị nói", assistant: "Trợ lý trả lời",
  agent: "Chuyên viên trả lời", system: "Hệ thống",
};

// Never scrollIntoView - it moves the page, not just the transcript.
const scrollTranscript = () => {
  $("messages").scrollTop = $("messages").scrollHeight;
};

function bubble(text) {
  const node = el("div", "bubble");
  node.innerHTML = render(text);
  return node;
}

function clockNow() {
  return new Date().toLocaleTimeString("vi-VN",
    { hour: "2-digit", minute: "2-digit" });
}

function avatarFor(role) {
  if (role === "customer") return el("span", "avatar sm me", "B");   // "Bạn"
  if (role === "agent") return el("span", "avatar sm", "NV");        // nhân viên
  return el("span", "avatar sm bot", "AB");
}

function addMessage(role, text, meta, author) {
  const wrap = el("div", `msg ${role}`);
  wrap.appendChild(el("span", "vh", `${SPEAKER[role] || role}: `));

  // The avatar sits beside the message; everything else stacks inside `body`.
  // A system notice is nobody's turn, so it gets neither an avatar nor a time.
  if (role !== "system") wrap.appendChild(avatarFor(role));
  const body = el("div", "msg-body");
  wrap.appendChild(body);

  if (author) body.appendChild(el("div", "author", author));

  // Balances and recent transactions are figures, and the design shows figures
  // as cards rather than prose. The server still returns one markdown string,
  // so the split is keyed off the flow's own note - an explicit signal from the
  // deterministic flow that produced it, not a guess about the wording. If any
  // line fails to parse the whole thing falls back to the plain bubble, so a
  // change of copy degrades to the old rendering rather than losing content.
  // A structured `blocks` payload on /api/chat would retire this entirely.
  const note = meta && meta.debug ? meta.debug.note : null;
  const cards = role === "assistant" ? accountCards(text, note) : null;

  if (cards) {
    // Figures get the design's wider measure than prose does.
    wrap.classList.add("has-cards");
    body.appendChild(bubble(cards.intro));
    body.appendChild(cards.node);
  } else {
    body.appendChild(bubble(text));
  }

  if (role !== "system") {
    // A double tick on the customer's own message, which is what every
    // messaging app uses to mean "delivered". Decorative here - there is no
    // delivery receipt behind it - but its absence is what makes a chat demo
    // feel unfinished.
    body.appendChild(el("div", "msg-time",
      role === "customer" ? `${clockNow()} ✓✓` : clockNow()));
  }

  // Route, intent, confidence, latency and the grounding citations all live in
  // the routing inspector beside this pane. They were repeated under every
  // bubble as well, which made the conversation read like a debug log rather
  // than a bank talking to a customer - and the customer-facing half of the
  // demo is meant to look like the product, not the instrumentation. The
  // inspector is the one place that owns them now.

  $("messages").appendChild(wrap);
  scrollTranscript();
}

/* ---------- figures as cards ---------- */

/** Read an amount aloud with the currency spelled out. */
function spokenAmount(amount) {
  const text = amount.trim();
  if (text.startsWith("$")) return `${text.slice(1)} đô la Mỹ`;
  if (text.endsWith(" VND")) return `${text.slice(0, -4)} đồng`;
  return text;
}

const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2)
  .map((word) => word[0].toUpperCase()).join("");

// A flow reached straight away and the same flow reached through the
// verification sub-flow report different notes, and both render the same
// figures, so both spellings are listed rather than pattern-matched.
const BALANCE_NOTES = new Set(["balance_read_from_core", "verified_then_balance_inquiry"]);
const TXN_NOTES = new Set(["transactions_read_from_core", "verified_then_transaction_history"]);

function accountCards(text, note) {
  if (BALANCE_NOTES.has(note)) return balanceCards(text);
  if (TXN_NOTES.has(note)) return transactionCard(text);
  return null;
}

/** Split a flow's reply into the prose that introduces it and the data lines.
    Anything before the first line the pattern recognises stays prose. */
function splitFlowText(text, pattern) {
  const lines = text.split("\n");
  const first = lines.findIndex((line) => pattern.test(line));
  if (first === -1) return null;
  return {
    intro: lines.slice(0, first).join("\n").trim(),
    rows: lines.slice(first).map((line) => line.match(pattern)).filter(Boolean),
  };
}

// "- **Tài khoản thanh toán** ····4417 - số dư 41.238.000 VND, khả dụng 40.938.000 VND"
//
// Both spellings are matched. `_balance` in app/flows.py speaks Vietnamese now,
// but the English wording is what the flow shipped with and what any untranslated
// fixture still produces - and a card that silently stops rendering is worse than
// one that never rendered, because nothing in the transcript says it was meant to.
const BALANCE_LINE =
  /^-\s+\*\*(.+?)\*\*\s+(\S+)\s+-\s+(?:số dư|balance)\s+(.+?),\s+(?:khả dụng|available)\s+(.+)$/;

function balanceCards(text) {
  const parsed = splitFlowText(text, BALANCE_LINE);
  if (!parsed) return null;
  const cards = el("div", "data-cards");

  for (const match of parsed.rows) {
    const [, type, mask, balance, available] = match;

    const card = el("div", "data-card");
    card.appendChild(el("span", "eyebrow", `${type} · ${mask}`));

    const amount = el("div", "amount", balance);
    amount.setAttribute("aria-label", `Số dư ${spokenAmount(balance)}`);
    card.appendChild(amount);

    const sub = el("div", "sub");
    sub.appendChild(document.createTextNode("khả dụng "));
    sub.appendChild(el("span", "num", available));
    card.appendChild(sub);

    cards.appendChild(card);
  }

  return { intro: parsed.intro, node: cards };
}

// "- 2024-05-31 · Coffee Union · −4.20"
const TXN_LINE = /^-\s+(.+?)\s+·\s+(.+?)\s+·\s+([+−-])(.+)$/;

function transactionCard(text) {
  const parsed = splitFlowText(text, TXN_LINE);
  if (!parsed) return null;

  const card = el("div", "txn-card");
  card.appendChild(el("div", "eyebrow", `${parsed.rows.length} giao dịch gần nhất`));

  for (const match of parsed.rows) {
    const [, date, description, sign, amount] = match;
    const credit = sign === "+";

    const row = el("div", `txn-row${credit ? " credit" : ""}`);
    row.appendChild(el("span", "txn-tile", initials(description)));

    const body = el("div", "txn-body");
    body.appendChild(el("span", "name", description));
    body.appendChild(el("span", "meta", date));
    row.appendChild(body);

    const value = el("span", "txn-amount", `${sign}${amount}`);
    value.setAttribute("aria-label",
      `${credit ? "Ghi có" : "Ghi nợ"} ${spokenAmount(amount)}`);
    row.appendChild(value);

    card.appendChild(row);
  }

  return { intro: parsed.intro, node: card };
}

/* ---------- typing indicator ---------- */

// The waiting state, shown as three dots in a bubble where the reply will
// appear. Retrieval plus a model call is rarely instant, and a chat that sits
// silent reads as broken rather than busy - the customer's own message is the
// last thing on screen and nothing acknowledges it.
//
// Two guards, at opposite ends. It is held for at least TYPING_MIN_MS so a fast
// reply does not flash three dots and vanish; and the label changes at
// TYPING_LABEL_MS, because past a couple of seconds people start wondering
// whether the send worked, and naming what is happening answers that without
// promising a finish time.
const TYPING_MIN_MS = 400;
const TYPING_LABEL_MS = 3000;
let typingNode = null;
let typingShownAt = 0;
let typingTimer = null;

function showTyping() {
  if (typingNode) return;
  typingNode = el("div", "msg assistant");
  typingNode.setAttribute("aria-label", "Trợ lý đang soạn câu trả lời");
  typingNode.appendChild(avatarFor("assistant"));

  const body = el("div", "msg-body");
  const dots = el("div", "bubble typing-bubble");
  dots.append(el("i"), el("i"), el("i"));
  body.appendChild(dots);
  const label = el("div", "typing-label", "Đang soạn câu trả lời…");
  body.appendChild(label);
  typingNode.appendChild(body);

  $("messages").appendChild(typingNode);
  typingShownAt = Date.now();
  scrollTranscript();

  typingTimer = setTimeout(() => {
    label.textContent = "Đang tra cứu tài liệu đã thẩm định…";
  }, TYPING_LABEL_MS);
}

async function hideTyping() {
  if (!typingNode) return;
  clearTimeout(typingTimer);
  // Captured before the await: a second caller arriving during the minimum-hold
  // would otherwise remove a node the first one has already detached.
  const node = typingNode;
  typingNode = null;
  const shownFor = Date.now() - typingShownAt;
  if (shownFor < TYPING_MIN_MS) {
    await new Promise((done) => setTimeout(done, TYPING_MIN_MS - shownFor));
  }
  node.remove();
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
  renderTrace(data.trace || []);
}

// The decision path answers the question every prospect asks about a hybrid
// assistant: how does it decide? Showing the gates beats describing them.
//
// main had removed this from the customer screen, on the grounds that a bank
// chat should not read as an instrumentation readout. That objection is about
// where it sits, not whether it should exist - and in the redesign it sits in
// the context rail, which is a column on desktop and an opt-in drawer below
// 1024px. The transcript itself stays clean either way, and the agent console
// still owns the same data for the people who read it all day.
function renderTrace(steps) {
  if (!steps.length) {
    $("trace").innerHTML = '<p class="empty">-</p>';
    return;
  }
  $("trace").innerHTML = steps.map((s) => `
    <div class="trace-step ${s.decisive ? "decisive" : ""}">
      <div class="trace-head">
        <span class="trace-stage">${escapeHtml(s.stage)}</span>
        <span>${escapeHtml(s.outcome)}</span>
      </div>
      ${s.detail ? `<div class="trace-detail">${escapeHtml(s.detail)}</div>` : ""}
    </div>`).join("");
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
  rawMode = enabled;
  refreshChatStatus();
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

async function send(message) {
  if (!message.trim()) return;
  addMessage("customer", message);
  $("input").value = "";
  $("send").disabled = true;
  showTyping();
  isSending = true;
  refreshChatStatus();
  try {
    const data = await postJson("/api/chat", { session_id: sessionId, message });
    await hideTyping();
    sessionId = data.session_id;
    applyRawMode(Boolean(data.raw_mode));
    if (data.route === "agent") {
      addMessage("system", data.handled_by
        ? `Sent to ${data.handled_by}.`
        : "Sent to the specialist queue - someone will pick this up.");
    } else {
      addMessage("assistant", data.reply, data);
    }
    updateInspector(data);
    setRailUser(data.verified);
    renderChatActions(data);
    // Driven by session state: an "@bot" aside is answered by the assistant
    // but does not take the customer out of the queue, so the banner stays.
    renderHandoff(data.in_handoff ? { handled_by: data.handled_by || null } : null);
    if (data.in_handoff) startPolling();
  } catch (error) {
    // Cleared here as well as on the success path: a failed request that
    // leaves the dots animating tells the customer a reply is still coming.
    await hideTyping();
    addMessage("system", `Không gửi được: ${error.message}`);
  } finally {
    isSending = false;
    refreshChatStatus();
    updateSendState();
    $("input").focus();
  }
}

/** Send is dead while there is nothing to send - the design asks for 50%
    opacity and no pointer rather than a button that submits an empty turn. */
function updateSendState() {
  $("send").disabled = !$("input").value.trim();
}

/* ---------- contextual chat actions ---------- */

// Buttons that mirror what can also be typed. The offer of a handoff is a
// decision the customer makes, so it gets a button; typing "yes" does the same
// thing, because a chat that only works by clicking is not a chat.
// `handoff` (declared with the status state above) is set from the handoff
// until the customer leaves, and is kept on the client so the banner survives
// turns that return nothing - every message while queued does.
function renderHandoff(state) {
  handoff = state;
  refreshChatStatus();
  const box = $("handoff-banner");
  if (!state) {
    box.classList.add("hidden");
    // Matches the placeholder in the markup, so returning from a handoff puts
    // the composer back exactly where it started. The @agent / @bot syntax it
    // used to advertise is explained by the handoff banner instead - which is
    // the only state where either prefix does anything.
    $("input").placeholder = "Nhập câu hỏi về tài chính của anh/chị…";
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
    if (handoff) renderHandoff({ handled_by: data.handled_by || null });
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
    `${m.conversations} conversations · ${m.turns} turns - every figure below ` +
    `counted from this running process`;

  const median = m.median_latency_ms;
  const latency = median >= 1000 ? `${(median / 1000).toFixed(1)}s` : `${median} ms`;

  $("stat-grid").innerHTML = [
    statTile("Deflection rate", pct(m.deflection_rate),
             `${m.deflected} resolved without a human`, "good"),
    statTile("Median response", latency,
             `call-centre baseline ${m.baseline_response_seconds}s`, "good"),
    statTile("Answers with a citation", pct(m.grounding_rate),
             `${m.grounded_answers} of ${m.knowledge_answers} knowledge answers`),
    statTile("Escalated to a human", String(m.escalated),
             "each with a written brief"),
    statTile("Regulated requests blocked", String(m.guardrail_blocks),
             "refused before any model ran"),
    statTile("Agent time saved", `${(m.estimated_minutes_saved / 60).toFixed(1)} h`,
             `estimate · ${m.assumptions.handle_minutes_per_deflected_conversation} min per conversation`,
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
      <strong>${escapeHtml(a.last_utterance || "(no message)")}</strong>
      <small>${escapeHtml(a.customer_name || "unidentified")} · ${a.turns} turns ·
        last route <b>${escapeHtml(a.last_route)}</b> · ${a.age_seconds}s ago</small>
      ${a.escalated ? `<small>escalated - ${escapeHtml(a.reason || "")}</small>` : ""}
    </button>`).join("") ||
    '<p class="empty">No conversations yet. Use "Seed demo traffic", or chat as a customer.</p>';

  $("activity").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => { showPanel("queue"); openSession(b.dataset.id); };
  });

  $("queue-count").textContent = data.queue.length;
  $("queue-count").classList.toggle("zero", data.queue.length === 0);

  const t = data.topics || { topics: [], singletons: [] };
  $("topic-count").textContent =
    `${t.total_unresolved || 0} unresolved turns, ${t.distinct_questions || 0} distinct`;
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
      : '<p class="empty">No repeated gaps yet - every unanswered question so far was a one-off.</p>')
    + (t.singletons.length
      ? `<p class="hint" style="margin-top:10px">Plus ${t.singletons.length} one-off question(s): `
        + t.singletons.map((c) => escapeHtml(c.questions[0])).join("; ") + "</p>"
      : "");

  const p = data.policy || {};
  $("policy-box").innerHTML = [
    ["Strictness", p.strictness], ["Source", p.source],
    ["Grounding floor", p.min_grounding], ["Relevance floor", p.min_relevance],
    ["Intent threshold", p.high_confidence],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Higher = refuses more, wrong less. '
   + 'Lower = answers more, wrong more. Edit config.json; applies on refresh.</p>';

  const c = data.campaigns || {};
  $("campaign-box").innerHTML = [
    ["Campaigns", c.campaigns], ["Customers targeted", c.customers_targeted],
    ["Source", c.source], ["Batch age", c.age_hours != null ? `${c.age_hours} h` : "-"],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Overnight CRM extract. Eligibility is '
   + 'read, never inferred - the bank decides who is targeted.</p>';

  const mem = data.memory || {};
  $("memory-box").innerHTML = [
    ["Customers remembered", mem.customers], ["Notes held", mem.notes],
    ["Retention", mem.ttl_days != null ? `${mem.ttl_days} days` : "-"],
    ["Stored", mem.retains],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">Written only after verification, '
   + 'and cleared with the sessions.</p>';

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
    ["Customers", dbs.customers], ["Cards", dbs.cards],
    ["Card state changes", dbs.card_events],
  ].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${escapeHtml(v)}</span></div>`).join("")
   + Object.entries(byStatus).map(([k, v]) =>
       `<div class="kv"><span>&nbsp;&nbsp;${escapeHtml(k)}</span><span>${v}</span></div>`).join("")
   + '<p class="hint" style="margin:10px 0 0">sqlite, reseeded by Clear sessions '
   + 'and by Seed demo traffic.</p>';

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
      <strong>${escapeHtml(s.customer_name || "Unidentified caller")}</strong>
      <small>${s.turns} turns · ${s.verified ? "verified" : "not verified"}</small>
      <small>${escapeHtml(s.reason || "")}</small>
    </button>`).join("") || '<p class="empty">Nothing waiting.</p>';

  $("queue").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => openSession(b.dataset.id);
  });
}

async function openSession(id) {
  selectedSession = id;
  $("handover-empty").classList.add("hidden");
  $("handover").classList.remove("hidden");
  $("handover-title").textContent = "Conversation";
  $("summary").textContent = "Generating handover brief…";

  const data = await postJson("/api/summary", { session_id: id });
  $("summary").innerHTML = render(data.summary);
  $("summary-source").textContent = data.generated
    ? `written by ${data.model}` : "offline template";
  $("transcript").textContent = data.transcript;
  $("handled-by").textContent = data.handled_by ? `handled by ${data.handled_by}` : "";
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
    `${data.stats.passages} retrievable passages across ${data.stats.documents} documents`;

  $("kb-list").innerHTML = data.documents.map((d) => `
    <button class="queue-item ${d.filename === selectedDoc ? "active" : ""}"
            data-name="${escapeHtml(d.filename)}">
      <strong>${escapeHtml(d.title)}</strong>
      <small>${escapeHtml(d.filename)} · ${escapeHtml(d.format)}</small>
      <small>${d.passages} passages · ${(d.bytes / 1024).toFixed(1)} KB</small>
    </button>`).join("") ||
    '<p class="empty">No documents. Every knowledge question will escalate.</p>';

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
  $("kb-passage-count").textContent = `${data.passages.length} passages`;
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
  if (!confirm(`Delete ${selectedDoc}? Its passages stop being retrievable immediately.`)) return;
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
    reader.onerror = () => reject(new Error("Could not read that file"));
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
    const status = p.available ? "ready"
      : !p.sdk_installed ? `pip install ${p.package}`
      : `no key - set ${p.env_key}`;
    return `<button class="queue-item ${active.provider === p.name ? "active" : ""}"
              data-name="${escapeHtml(p.name)}">
      <strong>${escapeHtml(p.name)}${active.provider === p.name ? " · in use" : ""}</strong>
      <small>${escapeHtml(p.default_model)}</small>
      <small>${escapeHtml(status)}${p.key_masked ? ` · key ${escapeHtml(p.key_masked)}` : ""}</small>
    </button>`;
  }).join("");

  $("provider-list").querySelectorAll(".queue-item").forEach((b) => {
    b.onclick = () => { $("cfg-provider").value = b.dataset.name; $("cfg-key").focus(); };
  });

  const rows = [["Mode", active.mode], ["Provider", active.provider || "-"],
                ["Model", active.model || "-"], ["Effort", active.effort || "-"]];
  if (active.tone) rows.push(["Tone", active.tone]);
  rows.push(["Temperature",
    active.temperature === null || active.temperature === undefined
      ? "provider default" : String(active.temperature)]);
  if (active.endpoint) rows.push(["Endpoint", active.endpoint]);
  if (active.detail) rows.push(["Detail", active.detail]);
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
  pill.textContent = h.mode === "live" ? `${h.provider}: ${h.model}` : "LLM: offline";
  pill.title = h.detail || `effort: ${h.effort}`;
  pill.className = `pill ${h.mode}`;
}

/* ---------- app shell ---------- */

// The context rail is the only drawer in the app: below 1024px it stops being
// a column and slides in from the right. Wider than that the CSS keeps it in
// flow and this is inert.
function setRail(open) {
  $("context-rail").classList.toggle("open", open);
  $("nav-scrim").hidden = !open;
}

$("rail-toggle").onclick = () => setRail(!$("context-rail").classList.contains("open"));
$("nav-scrim").onclick = () => setRail(false);
$("nav-assistant").onclick = () => showScreen("customer");
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setRail(false); });

// A mock, by request: the banking app this chat sits inside owns the
// destination, so the control is real and focusable but goes nowhere.
$("back-btn").onclick = () => {};

// The same request a customer can type. A button for it because asking for a
// human is a decision, not a phrasing exercise.
// Phrased to hit the `human_agent` anchor in app/nlu.py rather than to read
// well in isolation - the button has to take the same route a typed request
// would, or it is demonstrating a code path the customer cannot reach.
$("talk-to-person").onclick = () => send("Tôi muốn gặp nhân viên");

/* ---------- wiring ---------- */

$("raw-toggle").onchange = (e) => toggleRawMode(e.target.checked);

$("composer").onsubmit = (e) => { e.preventDefault(); send($("input").value); };
$("input").oninput = updateSendState;
$("suggestions").querySelectorAll("button").forEach((b) => {
  b.onclick = () => send(b.textContent);
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
  $("trace").innerHTML = '<p class="empty">-</p>';
  setRailUser(false);
  startTranscript();
  $("input").focus();
}
$("new-chat").onclick = newConversation;

// Decorative controls are inert by design. Saying so out loud beats a silent
// no-op that leaves someone clicking a dead button during a demo.
document.querySelectorAll("[data-demo]").forEach((node) => {
  node.title = (node.title ? node.title + " - " : "") + "minh hoạ giao diện, chưa hoạt động";
  node.addEventListener("click", (e) => e.preventDefault());
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
  if (!confirm("Delete every conversation in this process? " +
               "The dashboard resets to zero and transcripts are gone.")) return;
  const d = await postJson("/api/sessions/clear", {});
  sessionId = null;
  renderedCount = 0;
  selectedSession = null;
  $("handover").classList.add("hidden");
  $("handover-empty").classList.remove("hidden");
  await refreshDashboard();
  alert(`Cleared ${d.removed} conversation(s).`);
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
  $("cfg-key-toggle").textContent = hidden ? "Hide" : "Show";
};

/** Open a transcript: the standing notice, the day divider the design asks for,
    then the greeting. One function so "new conversation" and first load cannot
    drift apart - the divider went missing from the reset path when they were
    two. */
function startTranscript() {
  // The OTP/PIN warning used to sit under the composer as fine print, where a
  // phone gave it a whole line of permanent chrome above the keyboard for
  // something nobody reads twice. At the head of the transcript it is the
  // first thing in the conversation instead - read once, then scrolled past -
  // and it reappears with every new conversation, which fine print never did.
  const notice = el("div", "chat-notice");
  notice.appendChild(el("span", "ic", "🔒"));
  notice.appendChild(el("p", null,
    "Trợ lý chỉ trả lời dựa trên tài liệu đã được ngân hàng thẩm định. "
    + "Không bao giờ cung cấp mã OTP, mã PIN hay số thẻ đầy đủ cho bất kỳ ai."));
  $("messages").appendChild(notice);

  $("messages").appendChild(el("div", "day-divider", "Hôm nay"));
  addMessage("assistant",
    "Xin chào! Tôi là trợ lý ảo của **ABC Bank**. Tôi có thể tra cứu số dư và "
    + "giao dịch, khoá hoặc mở khoá thẻ, kiểm tra hồ sơ vay, và giải đáp về sản "
    + "phẩm, biểu phí của ngân hàng.\n\nAnh/chị cần hỗ trợ điều gì ạ?");
}

// Whether this session has passed the identity check, so the demo shows
// identity being established rather than just asserted in a chat bubble.
//
// main drove this from a customer record and had nowhere to get one - only the
// null branch was ever reached, which was invisible in the old dark rail but
// not in the left nav, where the line now sits in view for the whole session.
// /api/chat returns `verified` and deliberately not the record, so this reports
// the state and never the name.
function setRailUser(verified) {
  $("rail-user").textContent = verified ? "Khách hàng" : "Khách";
  $("rail-user-sub").textContent = verified ? "Đã xác thực" : "Chưa xác thực";
  $("rail-avatar").textContent = verified ? "✓" : "K";
}

(async function init() {
  updateSendState();
  refreshChatStatus();
  await refreshHealth();
  await refreshStaff();
  startTranscript();
  if (staff) { showScreen("agent"); showPanel("dashboard"); }
})();
