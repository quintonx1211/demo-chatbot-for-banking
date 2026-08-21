const $ = (id) => document.getElementById(id);

// One silent AudioContext buffer play unlocks the browser's autoplay policy.
// Must be called synchronously inside a user-gesture handler (click/submit).
let _audioUnlocked = false;
function _unlockAudio() {
  if (_audioUnlocked) return;
  _audioUnlocked = true;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const buf = ctx.createBuffer(1, 1, 22050);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(0);
    setTimeout(() => ctx.close(), 500);
  } catch (e) {}
}

let sessionId = null;
let selectedSession = null;
let selectedDoc = null;
let staff = null;
let renderedCount = 0;
let pollTimer = null;
let voiceEnabled = localStorage.getItem("voiceEnabled") !== "false";
let streamController = null;

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
    .replace(/_([^_]+)_/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");
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

/* ---------- chunk buffer + audio queue ---------- */

class ChunkBuffer {
  constructor(onFlush) {
    this.tokens = [];
    this.onFlush = onFlush;
  }

  push(token) {
    this.tokens.push(token);
    const text = this.tokens.join("");
    const words = text.trim().split(/\s+/).filter(Boolean).length;
    const hardStop = /[.!?…。！？]\s*$/.test(text.trim());
    if (hardStop || words >= 40) this.flush();
  }

  flush() {
    const text = this.tokens.join("").trim();
    this.tokens = [];
    if (text) this.onFlush(text);
  }
}

class SyncedPlayer {
  constructor() {
    this.pipeline = [];
    this.active = false;
    this.streamEnded = false;
    this.currentAudio = null;
    this.wordTimers = [];
    this.onAllDone = null;
  }

  init() {
    this.pipeline = [];
    this.active = false;
    this.streamEnded = false;
    this.currentAudio = null;
    this.wordTimers = [];
    this.onAllDone = null;
  }

  addChunk(chunkText, urlPromise, onPlay) {
    this.pipeline.push({ chunkText, urlPromise, onPlay });
    if (!this.active) this._advance();
  }

  notifyStreamEnd() {
    this.streamEnded = true;
    if (!this.active && !this.pipeline.length && this.onAllDone) {
      const cb = this.onAllDone; this.onAllDone = null; cb();
    }
  }

  _clearTimers() { this.wordTimers.forEach(clearTimeout); this.wordTimers = []; }

  async _advance() {
    if (!this.pipeline.length) {
      this.active = false;
      if (this.streamEnded && this.onAllDone) {
        const cb = this.onAllDone; this.onAllDone = null; cb();
      }
      return;
    }
    this.active = true;
    const { urlPromise, onPlay } = this.pipeline.shift();
    const url = await urlPromise;
    if (!this.active) { if (url) URL.revokeObjectURL(url); return; }
    if (!url) { if (onPlay) onPlay(); this._advance(); return; }

    if (ttsPlayingBtn) { ttsPlayer.pause(); resetSpeakBtn(ttsPlayingBtn); ttsPlayingBtn = null; }
    const audio = new Audio();
    this.currentAudio = audio;

    // Set handlers BEFORE src to avoid loadedmetadata race condition
    const ready = new Promise((res) => {
      audio.onloadedmetadata = res;
      audio.onerror = res;
      audio.src = url;
    });
    await ready;
    if (!this.active) { URL.revokeObjectURL(url); this.currentAudio = null; return; }

    if (onPlay) onPlay();
    audio.play().catch((e) => console.warn("audio.play() blocked:", e.message));
    audio.onended = () => {
      URL.revokeObjectURL(url); this.currentAudio = null; this._advance();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url); this.currentAudio = null; this._advance();
    };
  }

  stop() {
    this.active = false;
    this._clearTimers();
    this.pipeline = [];
    this.onAllDone = null;
    if (this.currentAudio) { this.currentAudio.pause(); this.currentAudio.src = ""; this.currentAudio = null; }
  }

  get isPlaying() { return this.active; }
}

let syncedPlayer = new SyncedPlayer();
let streamingAudio = null; // current auto-play TTS audio (full response)

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
                 kb: refreshKb, settings: () => { refreshProviders(); refreshTtsStatus(); } };
  if (load[name]) load[name]();
}

/* ---------- voice (TTS/STT) ---------- */

// Read from /api/health on every refresh - the server's TTS/STT providers
// can go from unavailable to available (or back) without a page reload if
// an operator changes Settings mid-demo, so this is state, not a constant.
let ttsAvailable = false;
let sttAvailable = false;
const canRecord = Boolean(
  navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);

function updateVoiceUI() {
  const mic = $("mic-btn");
  // Always shown, like the other composer icons - a browser with no
  // recording API is the only reason to hide it outright. When the server's
  // STT provider isn't configured the button stays visible but disabled, so
  // a customer sees the feature exists instead of wondering why it's gone.
  mic.classList.toggle("hidden", !canRecord);
  mic.disabled = !sttAvailable;
  mic.title = sttAvailable ? "Nhập bằng giọng nói" : "Giọng nói chưa khả dụng";
  // Reruns already-rendered speak buttons too, so a provider going dark
  // mid-conversation greys out replies already on screen, not just future
  // ones - a button that plays nothing on click is worse than one visibly
  // disabled.
  document.querySelectorAll(".speak-btn").forEach((b) => { b.disabled = !ttsAvailable; });
  const vt = $("voice-toggle");
  if (vt) {
    vt.textContent = voiceEnabled ? "🔊" : "🔇";
    vt.title = voiceEnabled ? "Tắt đọc tự động" : "Bật đọc tự động";
    vt.classList.toggle("active", voiceEnabled);
    vt.disabled = !ttsAvailable;
  }
  // Call button: visible only when mic recording AND STT are both available
  const cb = $("call-btn");
  if (cb) cb.classList.toggle("hidden", !(canRecord && sttAvailable));
}

// Played through a single <audio> element rather than one per bubble, so
// starting a second reply's audio stops the first instead of overlapping it.
const ttsPlayer = new Audio();
let ttsPlayingBtn = null;

function resetSpeakBtn(btn) {
  btn.textContent = "🔊";
  btn.classList.remove("playing");
}

async function playTts(text, btn) {
  _unlockAudio();  // synchronous, inside click gesture
  if (streamingAudio) { streamingAudio.pause(); streamingAudio.src = ""; streamingAudio = null; }
  syncedPlayer.stop();
  if (ttsPlayingBtn === btn) {
    ttsPlayer.pause();
    resetSpeakBtn(btn);
    ttsPlayingBtn = null;
    return;
  }
  if (ttsPlayingBtn) resetSpeakBtn(ttsPlayingBtn);

  btn.textContent = "⏳";
  btn.classList.remove("playing");
  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: stripMarkdownForTts(text) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `${response.status}`);
    }
    const url = URL.createObjectURL(await response.blob());
    ttsPlayer.src = url;
    ttsPlayingBtn = btn;
    btn.textContent = "⏸";
    btn.classList.add("playing");
    await ttsPlayer.play();
    ttsPlayer.onended = () => { resetSpeakBtn(btn); ttsPlayingBtn = null; URL.revokeObjectURL(url); };
  } catch (error) {
    resetSpeakBtn(btn);
    ttsPlayingBtn = null;
    addMessage("system", `Không đọc được câu trả lời: ${error.message}`);
  }
}

/** 16-bit PCM mono WAV, hand-written header - MediaRecorder gives WebM/Opus,
    but /api/stt expects WAV, so this is the bridge between them. */
function encodeWav(audioBuffer) {
  const channels = audioBuffer.numberOfChannels;
  const sampleRate = audioBuffer.sampleRate;
  const length = audioBuffer.length;
  const mono = new Float32Array(length);
  for (let c = 0; c < channels; c++) {
    const data = audioBuffer.getChannelData(c);
    for (let i = 0; i < length; i++) mono[i] += data[i] / channels;
  }

  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset, str) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (let i = 0; i < length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, mono[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",", 2)[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

let mediaRecorder = null;
let recordedChunks = [];
let recordingTimer = null;
const RECORDING_MAX_MS = 9500;

// Web Speech API tiếng Việt đôi khi transcribe dấu câu thành từ ("phấy" = dấu phẩy)
function cleanStt(t) {
  return t
    .replace(/\b(phấy|phẩy|chấm|dấu phẩy|dấu chấm)\b\.?/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

// Strip Markdown formatting before sending to TTS so the voice engine
// never reads raw symbols like **bold** or # Heading aloud.
function stripMarkdownForTts(text) {
  return text
    // Fenced code blocks - remove entirely
    .replace(/```[\s\S]*?```/g, "")
    // Markdown links → keep label only
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    // Bold + italic combined (***)
    .replace(/\*{3}([^*]+)\*{3}/g, "$1")
    // Bold (**)
    .replace(/\*{2}([^*]+)\*{2}/g, "$1")
    // Italic (*) - avoid matching stray asterisks inside numbers
    .replace(/\*([^*\n]+)\*/g, "$1")
    // Underline bold (__) and italic (_)
    .replace(/_{2}([^_]+)_{2}/g, "$1")
    .replace(/_([^_\n]+)_/g, "$1")
    // Inline code
    .replace(/`([^`]+)`/g, "$1")
    // ATX headings (# ## ###…)
    .replace(/^#{1,6}\s+(.+)$/gm, "$1")
    // Horizontal rules → natural pause
    .replace(/^[-*_]{3,}\s*$/gm, ".")
    // Blockquotes
    .replace(/^>\s*/gm, "")
    // Unordered list bullets (-, *, +, •)
    .replace(/^[ \t]*[-*+•]\s+/gm, "")
    // Ordered list numbers
    .replace(/^[ \t]*\d+[.)]\s+/gm, "")
    // Paragraph breaks → spoken pause
    .replace(/\n{2,}/g, ". ")
    // Remaining newlines → space
    .replace(/\n/g, " ")
    // Collapse extra spaces
    .replace(/ {2,}/g, " ")
    // Expand Vietnamese banking abbreviations so TTS reads them naturally
    .replace(/CMND\s*\/\s*CCCD/gi, "chứng minh nhân dân hoặc căn cước công dân")
    .replace(/CCCD\s*\/\s*CMND/gi, "căn cước công dân hoặc chứng minh nhân dân")
    .replace(/\bCMND\b/g, "chứng minh nhân dân")
    .replace(/\bCCCD\b/g, "căn cước công dân")
    .trim();
}

let recognition = null;
let recognitionFinalText = '';
let audioCtxForMic = null;
let waveformAnimId = null;
let silenceStart = null;
const SILENCE_THRESHOLD = 8;      // RMS amplitude 0-100; below = silence
const SILENCE_DURATION_MS = 3000; // stop after 3s of silence

function _startWaveform(stream) {
  try {
    audioCtxForMic = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtxForMic.createMediaStreamSource(stream);
    const analyser = audioCtxForMic.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.85;
    source.connect(analyser);

    const canvas = $("waveform-canvas");
    canvas.width = (canvas.parentElement.clientWidth || 300) - 60;
    const ctx2d = canvas.getContext("2d");
    const bufLen = analyser.frequencyBinCount;
    const data = new Uint8Array(bufLen);
    silenceStart = null;

    const draw = () => {
      analyser.getByteTimeDomainData(data);

      // RMS for silence detection
      let sum = 0;
      for (let i = 0; i < bufLen; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / bufLen) * 100;

      const dot = $("silence-dot");
      const statusEl = $("stt-status");
      if (rms < SILENCE_THRESHOLD) {
        if (!silenceStart) silenceStart = Date.now();
        const elapsed = Date.now() - silenceStart;
        if (statusEl) statusEl.textContent = "Đang nghe…";
        if (dot) dot.style.background = `hsl(${Math.round(40 * (1 - elapsed / SILENCE_DURATION_MS))}, 80%, 50%)`;
        if (elapsed >= SILENCE_DURATION_MS) {
          if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
          return;
        }
      } else {
        silenceStart = null;
        if (dot) dot.style.background = "#ef4444";
        if (statusEl) statusEl.textContent = "Đang nghe…";
      }

      // Draw waveform
      ctx2d.clearRect(0, 0, canvas.width, canvas.height);
      ctx2d.strokeStyle = "#3b82f6";
      ctx2d.lineWidth = 2;
      ctx2d.beginPath();
      const sw = canvas.width / bufLen;
      let x = 0;
      for (let i = 0; i < bufLen; i++) {
        const y = (data[i] / 256) * canvas.height;
        i === 0 ? ctx2d.moveTo(x, y) : ctx2d.lineTo(x, y);
        x += sw;
      }
      ctx2d.stroke();

      waveformAnimId = requestAnimationFrame(draw);
    };
    draw();
  } catch (e) { /* AudioContext unavailable */ }
}

function _stopWaveform() {
  if (waveformAnimId) { cancelAnimationFrame(waveformAnimId); waveformAnimId = null; }
  if (audioCtxForMic) { audioCtxForMic.close().catch(() => {}); audioCtxForMic = null; }
  silenceStart = null;
  $("waveform-bar").classList.add("hidden");
}

async function toggleRecording() {
  // Barge-in: stop bot speech/stream when user presses mic
  if (syncedPlayer.isPlaying || streamController) {
    syncedPlayer.stop();
    if (streamController) { streamController.abort(); streamController = null; }
    await hideTyping();
    isSending = false;
    refreshChatStatus();
    updateSendState();
  }
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    addMessage("system", `Không truy cập được micro: ${error.message}`);
    return;
  }

  // Real-time interim display via Web Speech API (Chrome/Edge)
  recognitionFinalText = "";
  $("input").value = "";
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SR) {
    recognition = new SR();
    recognition.lang = "vi-VN";
    recognition.interimResults = true;
    recognition.continuous = true;
    recognition.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) recognitionFinalText += cleanStt(event.results[i][0].transcript);
        else interim = event.results[i][0].transcript;
      }
      $("input").value = recognitionFinalText + interim;
      updateSendState();
    };
    recognition.onerror = () => {};
    try { recognition.start(); } catch (e) {}
  }

  // Waveform visualizer + silence detection
  _startWaveform(stream);
  $("waveform-bar").classList.remove("hidden");

  recordedChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => { if (e.data.size) recordedChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    $("mic-btn").classList.remove("recording");
    clearTimeout(recordingTimer);
    _stopWaveform();
    if (recognition) { try { recognition.stop(); } catch (e) {} recognition = null; }
    handleRecording(new Blob(recordedChunks, { type: mediaRecorder.mimeType }));
  };
  mediaRecorder.start();
  $("mic-btn").classList.add("recording");
  recordingTimer = setTimeout(() => {
    if (mediaRecorder && mediaRecorder.state === "recording") mediaRecorder.stop();
  }, RECORDING_MAX_MS);
}

async function handleRecording(blob) {
  const mic = $("mic-btn");
  mic.disabled = true;
  const original = mic.textContent;
  mic.textContent = "⏳";
  try {
    let audio_b64, mime_type;
    try {
      // Try WAV conversion (Chrome/Edge/Firefox)
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const audioCtx = new AudioCtx();
      await audioCtx.resume();
      const audioBuffer = await audioCtx.decodeAudioData(await blob.arrayBuffer());
      const wav = encodeWav(audioBuffer);
      audioCtx.close();
      audio_b64 = await blobToBase64(wav);
      mime_type = "audio/wav";
    } catch {
      // Fallback for Safari: send raw audio (mp4/aac) directly
      audio_b64 = await blobToBase64(blob);
      mime_type = blob.type || "audio/mp4";
    }
    const data = await postJson("/api/stt", { audio_b64, mime_type });
    if (data.text) {
      $("input").value = data.text;
      $("input").focus();
      updateSendState();
    } else if (!$("input").value) {
      addMessage("system", "Không nhận dạng được giọng nói, anh/chị vui lòng gõ câu hỏi giúp.");
    }
  } catch (error) {
    if (!$("input").value) {
      addMessage("system", `Xử lý giọng nói thất bại: ${error.message}`);
    }
  } finally {
    mic.disabled = false;
    mic.textContent = original;
  }
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
    if (cards.tail) body.appendChild(bubble(cards.tail));
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

  // Read-aloud is offered per reply, not as a global auto-play toggle: a
  // customer glancing at their phone in public may not want every answer
  // spoken, so playback stays an explicit choice made bubble by bubble.
  if (role === "assistant") {
    const speak = el("button", "icon-btn ghost-ic speak-btn", "🔊");
    speak.type = "button";
    speak.title = "Đọc câu trả lời";
    speak.disabled = !ttsAvailable;
    speak.onclick = () => playTts(text, speak);
    body.appendChild(speak);
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

/* ---------- streaming message helpers ---------- */

function addStreamingMessage() {
  const wrap = el("div", "msg assistant");
  wrap.setAttribute("aria-label", "Trợ lý đang trả lời");
  wrap.appendChild(avatarFor("assistant"));
  const body = el("div", "msg-body");
  wrap.appendChild(body);
  const bubbleEl = el("div", "bubble");
  body.appendChild(bubbleEl);
  body.appendChild(el("div", "msg-time", clockNow()));
  $("messages").appendChild(wrap);
  scrollTranscript();
  return { wrap, body, bubbleEl };
}

function finalizeStreamingMessage(wrap, body, fullText, meta) {
  const note = meta && meta.debug ? meta.debug.note : null;
  const cards = accountCards(fullText, note);
  const bubbleEl = body.querySelector(".bubble");

  if (cards) {
    wrap.classList.add("has-cards");
    if (bubbleEl) bubbleEl.innerHTML = render(cards.intro);
    const timeEl = body.querySelector(".msg-time");
    body.insertBefore(cards.node, timeEl);
    if (cards.tail) {
      const tailEl = el("div", "bubble");
      tailEl.innerHTML = render(cards.tail);
      body.insertBefore(tailEl, timeEl);
    }
  } else if (bubbleEl) {
    bubbleEl.innerHTML = render(fullText);
  }

  const speak = el("button", "icon-btn ghost-ic speak-btn", "🔊");
  speak.type = "button";
  speak.title = "Đọc câu trả lời";
  speak.disabled = !ttsAvailable;
  speak.onclick = () => playTts(fullText, speak);
  body.appendChild(speak);
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
  let lastMatch = first;
  for (let i = first; i < lines.length; i++) {
    if (pattern.test(lines[i])) lastMatch = i;
  }
  return {
    intro: lines.slice(0, first).join("\n").trim(),
    rows: lines.slice(first, lastMatch + 1).map((line) => line.match(pattern)).filter(Boolean),
    tail: lines.slice(lastMatch + 1).join("\n").trim(),
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

  return { intro: parsed.intro, node: cards, tail: parsed.tail };
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

  return { intro: parsed.intro, node: card, tail: parsed.tail };
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
  if (voiceEnabled && ttsAvailable) _unlockAudio();
  addMessage("customer", message);
  $("input").value = "";
  $("send").disabled = true;
  showTyping();
  isSending = true;
  refreshChatStatus();

  streamController = new AbortController();
  let streamMsg = null;
  let fullText = "";
  let metaReceived = false;

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message }),
      signal: streamController.signal,
    });
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      throw new Error(d.error || `${resp.status}`);
    }

    await hideTyping();
    streamMsg = addStreamingMessage();
    const useVoice = voiceEnabled && ttsAvailable;
    if (useVoice) streamMsg.bubbleEl.innerHTML = '<div class="typing-bubble"><i></i><i></i><i></i></div>';

    // Word-by-word text reveal at 250ms/word
    // Track position in fullText (not a rebuilt string) so newlines and markdown
    // markers are preserved — the bubble shows a prefix of fullText, which matches
    // exactly what finalizeStreamingMessage renders for the final state.
    let revealedChars = 0;
    let pendingWordCount = 0;
    let wordRevealTimer = null;
    let pendingFinalMeta = null;

    function doFinalizeMsg(m) {
      if (m.route === "agent") {
        streamMsg.wrap.remove();
        addMessage("system", m.handled_by
          ? `Đã gửi đến ${m.handled_by}.`
          : "Đã gửi vào hàng chờ chuyên viên - sẽ có người tiếp nhận sớm.");
      } else {
        finalizeStreamingMessage(streamMsg.wrap, streamMsg.body, fullText, m);
      }
    }

    function _advanceWord(text, pos) {
      while (pos < text.length && /\s/.test(text[pos])) pos++;
      while (pos < text.length && !/\s/.test(text[pos])) pos++;
      return pos;
    }
    function revealNext() {
      if (!pendingWordCount) {
        wordRevealTimer = null;
        if (pendingFinalMeta) { const m = pendingFinalMeta; pendingFinalMeta = null; doFinalizeMsg(m); }
        return;
      }
      pendingWordCount--;
      revealedChars = _advanceWord(fullText, revealedChars);
      if (streamMsg && streamMsg.bubbleEl) {
        streamMsg.bubbleEl.innerHTML = render(fullText.slice(0, revealedChars));
        scrollTranscript();
      }
      wordRevealTimer = setTimeout(revealNext, 250);
    }
    function queueText(text) {
      const words = text.trim().split(/\s+/).filter(Boolean);
      if (!words.length) return;
      pendingWordCount += words.length;
      if (!wordRevealTimer) revealNext();
    }

    function finalizeMeta(m) {
      updateInspector(m);
      setRailUser(m.verified);
      renderChatActions(m);
      renderHandoff(m.in_handoff ? { handled_by: m.handled_by || null } : null);
      if (m.in_handoff) startPolling();

      if (m.route === "agent") {
        clearTimeout(wordRevealTimer); wordRevealTimer = null; pendingWordCount = 0;
        doFinalizeMsg(m);
      } else if (useVoice) {
        // Voice on: text deferred — revealNext calls doFinalizeMsg when words drain
        pendingFinalMeta = m;
      } else if (pendingWordCount || wordRevealTimer) {
        pendingFinalMeta = m;
        setTimeout(() => {
          if (pendingFinalMeta === m) {
            clearTimeout(wordRevealTimer); wordRevealTimer = null; pendingWordCount = 0;
            pendingFinalMeta = null; doFinalizeMsg(m);
          }
        }, 30000);
      } else {
        doFinalizeMsg(m);
      }
    }

    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let sseBuf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      sseBuf += dec.decode(value, { stream: true });
      const parts = sseBuf.split("\n\n");
      sseBuf = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }

        if (ev.token !== undefined) {
          fullText += ev.token;
          if (!useVoice) queueText(ev.token);
        } else if (ev.meta) {
          metaReceived = true;
          const m = ev.meta;
          sessionId = m.session_id;
          applyRawMode(Boolean(m.raw_mode));
          finalizeMeta(m);

          if (useVoice && fullText.trim() && m.route !== "agent") {
            if (streamingAudio) { streamingAudio.pause(); streamingAudio.src = ""; streamingAudio = null; }
            if (ttsPlayingBtn) { ttsPlayer.pause(); resetSpeakBtn(ttsPlayingBtn); ttsPlayingBtn = null; }
            fetch("/api/tts", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text: stripMarkdownForTts(fullText.trim()) }),
            })
              .then((r) => (r.ok ? r.blob() : null))
              .then((b) => (b ? URL.createObjectURL(b) : null))
              .then((url) => {
                if (!url) { queueText(fullText); return; }
                const audio = new Audio();
                streamingAudio = audio;
                audio.onloadedmetadata = () => {
                  audio.play().catch((e) => console.warn("audio.play() blocked:", e.message));
                  queueText(fullText);
                };
                audio.onended = () => { URL.revokeObjectURL(url); if (streamingAudio === audio) streamingAudio = null; };
                audio.onerror = () => {
                  URL.revokeObjectURL(url);
                  if (streamingAudio === audio) streamingAudio = null;
                  if (!revealedChars) queueText(fullText);
                };
                audio.src = url;
              })
              .catch(() => { if (!revealedChars) queueText(fullText); });
          }
        } else if (ev.error) {
          throw new Error(ev.error);
        }
      }
    }
  } catch (error) {
    if (streamingAudio) { streamingAudio.pause(); streamingAudio.src = ""; streamingAudio = null; }
    if (error.name === "AbortError") return;
    await hideTyping();
    if (streamMsg && !fullText) streamMsg.wrap.remove();
    addMessage("system", `Không gửi được: ${error.message}`);
  } finally {
    streamController = null;
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

function updateTtsVoiceFieldVisibility() {
  $("tts-voice-field").classList.toggle("hidden", $("tts-provider").value !== "vbee");
}

async function refreshTtsStatus() {
  const data = await api("/api/tts/provider");
  // Đồng bộ cả Settings panel lẫn chat bar
  $("tts-provider").value = data.provider;
  if (data.vbee_voice) $("tts-voice").value = data.vbee_voice;
  updateTtsVoiceFieldVisibility();
  // Chat bar
  $("tts-bar-provider").value = data.provider;
  $("tts-bar-voice").classList.toggle("hidden", data.provider !== "vbee");
  if (data.vbee_voice) $("tts-bar-voice").value = data.vbee_voice;

  const parts = [];
  parts.push(data.vbee_available ? "Voice 2: đã cấu hình key" : "Voice 2: chưa có key");
  parts.push(data.gtts_available ? "Voice 1: đã cài" : "Voice 1: chưa cài (pip install gtts)");
  $("tts-status").textContent = parts.join(" · ");
}

async function saveTtsFromChatBar() {
  try {
    await postJson("/api/tts/provider", {
      provider: $("tts-bar-provider").value,
      vbee_voice: $("tts-bar-voice").value,
    });
    await refreshTtsStatus();
  } catch { /* silent — chat bar không có chỗ hiển thị lỗi */ }
}

async function saveTtsConfig() {
  clearError($("tts-error"));
  $("tts-save").disabled = true;
  try {
    await postJson("/api/tts/provider", {
      provider: $("tts-provider").value,
      vbee_voice: $("tts-voice").value,
    });
    await refreshTtsStatus();
    await refreshHealth();
  } catch (error) {
    showError($("tts-error"), error.message);
  } finally {
    $("tts-save").disabled = false;
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

  ttsAvailable = Boolean(h.tts_available);
  sttAvailable = Boolean(h.stt_available);
  updateVoiceUI();
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

// Starting over drops the server-side session too. Clearing only the visible
// transcript would leave the customer talking to a conversation they cannot
// see - including any pending verification or handoff.
function newConversation() {
  syncedPlayer.stop();
  if (streamController) { streamController.abort(); streamController = null; }
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

$("tts-save").onclick = saveTtsConfig;
$("tts-provider").onchange = updateTtsVoiceFieldVisibility;
$("tts-bar-provider").onchange = () => {
  $("tts-bar-voice").classList.toggle("hidden", $("tts-bar-provider").value !== "vbee");
  saveTtsFromChatBar();
};
$("tts-bar-voice").onchange = saveTtsFromChatBar;
$("mic-btn").onclick = toggleRecording;
$("voice-toggle").onclick = () => {
  voiceEnabled = !voiceEnabled;
  localStorage.setItem("voiceEnabled", voiceEnabled);
  if (voiceEnabled) _unlockAudio();
  else syncedPlayer.stop();
  updateVoiceUI();
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

/* ================================================================
   CALL MODE — liên tục nói chuyện bằng giọng nói với chatbot.
   Vòng lặp: Nghe → STT → gửi chat → TTS → Nghe lại.
   Text vẫn cập nhật đầy đủ trong khung chat bình thường.
   ================================================================ */

let callModeActive = false;
let callState      = "idle";
let callTimer      = null;
let callSeconds    = 0;
let callRecorder   = null;
let callChunks     = [];
let callSilenceAnimId = null;
let callAudioCtx   = null;
let callMuted      = false;

const CALL_SILENCE_MS = 1500;   // dừng thu âm sau 1.5s im lặng (ngắn hơn chế độ thủ công 3s)
const CALL_MAX_MS     = 10000;  // giới hạn an toàn tối đa mỗi lượt ghi

function _setCallState(state) {
  callState = state;
  const wrap  = $("call-avatar-wrap");
  const label = $("call-state-label");
  if (!wrap || !label) return;
  wrap.dataset.state = state;
  label.textContent = {
    connecting: "Đang kết nối…",
    listening:  "Đang nghe…",
    processing: "Đang xử lý…",
    speaking:   "Đang trả lời…",
  }[state] || "";
}

function _callTimerStart() {
  callSeconds = 0;
  $("call-timer").textContent = "00:00";
  callTimer = setInterval(() => {
    callSeconds++;
    const m = String(Math.floor(callSeconds / 60)).padStart(2, "0");
    const s = String(callSeconds % 60).padStart(2, "0");
    $("call-timer").textContent = `${m}:${s}`;
  }, 1000);
}
function _callTimerStop() { clearInterval(callTimer); callTimer = null; }

// ---- public: start / stop ----

async function startCall() {
  if (!sttAvailable || !canRecord || callModeActive) return;
  _unlockAudio();
  callModeActive = true;
  callMuted      = false;

  // Ensure TTS auto-plays during call
  if (!voiceEnabled) {
    voiceEnabled = true;
    localStorage.setItem("voiceEnabled", "true");
    updateVoiceUI();
  }

  $("call-mute-btn").querySelector(".vs-icon-active").classList.remove("hidden");
  $("call-mute-btn").querySelector(".vs-icon-muted").classList.add("hidden");
  $("call-mute-btn").classList.remove("muted");
  $("call-overlay").classList.remove("hidden");
  const lmEl = $("call-last-msg");
  if (lmEl) { lmEl.textContent = ""; lmEl.classList.add("hidden"); }
  _setCallState("connecting");
  _callTimerStart();

  await new Promise(r => setTimeout(r, 350)); // let overlay render
  if (callModeActive) _callStartListening();
}

function stopCall() {
  callModeActive = false;
  callMuted      = false;

  if (callRecorder && callRecorder.state === "recording") callRecorder.stop();
  callRecorder = null; callChunks = [];

  if (callSilenceAnimId) { cancelAnimationFrame(callSilenceAnimId); callSilenceAnimId = null; }
  if (callAudioCtx)      { callAudioCtx.close().catch(() => {}); callAudioCtx = null; }

  // Stop any TTS that was playing for the call
  if (streamingAudio) { streamingAudio.pause(); streamingAudio.src = ""; streamingAudio = null; }

  _callTimerStop();
  $("call-overlay").classList.add("hidden");
  _setCallState("idle");
}

// ---- recording loop ----

async function _callStartListening() {
  if (!callModeActive || callMuted) return;
  _setCallState("listening");

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    stopCall();
    addMessage("system", "Không truy cập được micro — cuộc gọi kết thúc.");
    return;
  }

  // Voice-activity detection (silence → auto-stop)
  try {
    callAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source   = callAudioCtx.createMediaStreamSource(stream);
    const analyser = callAudioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.85;
    source.connect(analyser);
    const bufLen = analyser.frequencyBinCount;
    const data   = new Uint8Array(bufLen);
    let localSilence = null;

    const tick = () => {
      if (!callModeActive) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < bufLen; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / bufLen) * 100;

      // Animate the inner avatar ring with live voice amplitude
      const inner = document.querySelector(".call-avatar-circle");
      if (inner) inner.style.transform = callState === "listening"
        ? `scale(${1 + Math.min(rms / 120, 0.18)})` : "";

      if (rms < SILENCE_THRESHOLD) {
        if (!localSilence) localSilence = Date.now();
        if (Date.now() - localSilence >= CALL_SILENCE_MS) {
          if (callRecorder && callRecorder.state === "recording") callRecorder.stop();
          return; // don't schedule next frame
        }
      } else {
        localSilence = null;
      }
      callSilenceAnimId = requestAnimationFrame(tick);
    };
    callSilenceAnimId = requestAnimationFrame(tick);
  } catch (_) { /* no VAD — rely on max-duration timeout */ }

  callChunks  = [];
  callRecorder = new MediaRecorder(stream);
  callRecorder.ondataavailable = (e) => { if (e.data.size) callChunks.push(e.data); };
  callRecorder.onstop = () => {
    stream.getTracks().forEach(t => t.stop());
    if (callSilenceAnimId) { cancelAnimationFrame(callSilenceAnimId); callSilenceAnimId = null; }
    if (callAudioCtx)      { callAudioCtx.close().catch(() => {}); callAudioCtx = null; }
    const inner = document.querySelector(".call-avatar-circle");
    if (inner) inner.style.transform = "";

    if (!callModeActive || callMuted) return; // stopped intentionally
    const blob = new Blob(callChunks, { type: callRecorder.mimeType });
    _callHandleBlob(blob);
  };
  callRecorder.start();
  setTimeout(() => {
    if (callRecorder && callRecorder.state === "recording") callRecorder.stop();
  }, CALL_MAX_MS);
}

async function _callHandleBlob(blob) {
  if (!callModeActive) return;
  _setCallState("processing");

  try {
    let audio_b64, mime_type;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx();
      await ctx.resume();
      const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
      const wav = encodeWav(buf);
      ctx.close();
      audio_b64 = await blobToBase64(wav);
      mime_type  = "audio/wav";
    } catch {
      audio_b64 = await blobToBase64(blob);
      mime_type  = blob.type || "audio/mp4";
    }

    const sttData = await postJson("/api/stt", { audio_b64, mime_type });
    const text    = cleanStt(sttData.text || "");

    if (!text || !callModeActive) {
      // Silence or empty — restart listening immediately (no chat bubble)
      if (callModeActive) _callStartListening();
      return;
    }

    await _callSendMessage(text);
  } catch (_) {
    if (callModeActive) { _setCallState("listening"); _callStartListening(); }
  }
}

async function _callSendMessage(text) {
  if (!text.trim() || !callModeActive) return;
  addMessage("customer", text);
  showTyping();

  let fullText = "";
  let meta     = null;

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    if (!resp.ok) throw new Error(`${resp.status}`);

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const ev = JSON.parse(line.slice(6));
          if (ev.token) {
            fullText += ev.token;
          } else if (ev.meta) {
            meta = ev.meta;
            sessionId = meta.session_id;
            applyRawMode(Boolean(meta.raw_mode));
            setRailUser(meta.verified);
            renderChatActions(meta);
            renderHandoff(meta.in_handoff ? { handled_by: meta.handled_by || null } : null);
            if (meta.in_handoff) startPolling();
          } else if (ev.error) {
            throw new Error(ev.error);
          }
        } catch (_) {}
      }
    }
  } catch (_) {
    await hideTyping();
    if (callModeActive) { _setCallState("listening"); _callStartListening(); }
    return;
  }

  await hideTyping();

  if (fullText.trim()) {
    addMessage("assistant", fullText.trim(), meta);
    updateInspector(meta);

    // Show a short preview of the reply inside the voice overlay
    const lmEl = $("call-last-msg");
    if (lmEl) {
      const preview = fullText.trim().replace(/\s+/g, " ");
      lmEl.textContent = preview.slice(0, 160) + (preview.length > 160 ? "…" : "");
      lmEl.classList.remove("hidden");
    }

    if (ttsAvailable && callModeActive) {
      _setCallState("speaking");
      await _playTtsForCall(stripMarkdownForTts(fullText.trim()));
    }
  }

  if (callModeActive) {
    _setCallState("listening");
    _callStartListening();
  }
}

// Returns a promise that resolves only after TTS audio has finished playing.
async function _playTtsForCall(text) {
  return new Promise((resolve) => {
    if (streamingAudio) { streamingAudio.pause(); streamingAudio.src = ""; streamingAudio = null; }
    fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
      .then(r => (r.ok ? r.blob() : null))
      .then(b => (b ? URL.createObjectURL(b) : null))
      .then(url => {
        if (!url) { resolve(); return; }
        const audio = new Audio();
        streamingAudio = audio;
        const finish = () => {
          URL.revokeObjectURL(url);
          if (streamingAudio === audio) streamingAudio = null;
          resolve();
        };
        audio.onended = finish;
        audio.onerror = finish;
        audio.src = url;
        audio.play().catch(finish);
      })
      .catch(() => resolve());
  });
}

// ---- mute toggle ----

function _callToggleMute() {
  callMuted = !callMuted;
  const btn = $("call-mute-btn");
  btn.querySelector(".vs-icon-active").classList.toggle("hidden", callMuted);
  btn.querySelector(".vs-icon-muted").classList.toggle("hidden", !callMuted);
  btn.title = callMuted ? "Bật tiếng" : "Tắt tiếng";
  btn.setAttribute("aria-label", callMuted ? "Bật tiếng" : "Tắt tiếng");
  btn.classList.toggle("muted", callMuted);
  const lbl = $("call-mute-label");
  if (lbl) lbl.textContent = callMuted ? "Bật tiếng" : "Tắt tiếng";

  if (callMuted) {
    // Stop current recording — onstop will skip re-start because callMuted=true
    if (callRecorder && callRecorder.state === "recording") callRecorder.stop();
    _setCallState("listening"); // keep visual state coherent
  } else if (callModeActive && callState === "listening") {
    _callStartListening();
  }
}

// ---- wire up buttons ----
$("call-btn").addEventListener("click", startCall);
$("call-end-btn").addEventListener("click", stopCall);
$("call-mute-btn").addEventListener("click", _callToggleMute);

(async function init() {
  updateSendState();
  refreshChatStatus();
  await refreshHealth();
  await refreshStaff();
  startTranscript();
  if (staff) { showScreen("agent"); showPanel("dashboard"); }
})();
