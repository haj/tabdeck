'use strict';
const $ = (sel) => document.querySelector(sel);
const STATUS_LABEL = { needs_you: 'Needs you', working: 'Working', done: 'Done', idle: 'Idle' };
const PENDING_MS = 2500;

function load(k) { try { return localStorage.getItem('tabdeck.' + k); } catch { return null; } }
function save(k, v) { try { localStorage.setItem('tabdeck.' + k, v ?? ''); } catch { /* private mode */ } }

const S = {
  sessions: [], isLocal: false, canPair: false, iterm: true,
  selected: load('selected') || null, muted: load('muted') === '1',
  prev: {}, prevMessage: {}, readIdx: {}, chunks: {}, pending: null,
  showRaw: false, rawText: '', rawTimer: null, selectAfter: null, replyText: {},
};
const byId = (id) => S.sessions.find((s) => s.id === id);

async function api(path, { method = 'GET', json, form } = {}) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (json !== undefined) { opts.body = JSON.stringify(json); opts.headers['Content-Type'] = 'application/json'; }
  if (form) opts.body = form;
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const d = (await r.json()).detail || detail;
      detail = d && d.errors ? d.errors.join('\n') : d;  // refused settings: one reason per line
    } catch { /* not json */ }
    const err = new Error(detail); err.status = r.status; throw err;
  }
  return r.json();
}

function mk(tag, cls, text, onclick) {
  const el = document.createElement(tag);
  el.className = cls; el.textContent = text;
  if (onclick) el.onclick = onclick;
  return el;
}

function toast(msg, href) {
  const t = document.createElement(href ? 'a' : 'div');
  t.className = 'toast'; t.textContent = msg;
  if (href) { t.href = href; t.target = '_blank'; t.rel = 'noopener'; }
  document.body.append(t);
  setTimeout(() => t.remove(), href ? 8000 : 4000);
}

function setHeard(text) { $('#heard').textContent = text; }

/* ---------- speech out ---------- */
// Speech: the hub's Kokoro voice (the same one as the Mac widget), sentence by sentence with the
// next sentence fetched while one plays. The iPhone's own voice is only the fallback.
const player = new Audio();
const SILENCE = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=';
let speechUnlocked = false;
let ttsQueue = [];
let ttsPlaying = false;
let ttsPrefetch = null;
let ttsGen = 0;

function unlockSpeech() {
  // iPhones only allow sound after a tap: play silence once from inside the tap.
  if (speechUnlocked) return;
  player.src = SILENCE;
  player.play().catch(() => {});
  if ('speechSynthesis' in window) speechSynthesis.speak(new SpeechSynthesisUtterance(' '));
  speechUnlocked = true;
}

function splitSentences(text) {
  // A sentence ends at . ! ? followed by whitespace or the end ("package.json" and "v2.0" stay whole);
  // pieces under 12 characters join what follows (no choppy one-word clips). Same rule as the widget.
  const raw = [];
  let cur = '';
  for (let i = 0; i < text.length; i += 1) {
    cur += text[i];
    if ('.!?'.includes(text[i]) && (i + 1 === text.length || /\s/.test(text[i + 1]))) { raw.push(cur); cur = ''; }
  }
  raw.push(cur);
  const out = [];
  let buf = '';
  for (const piece of raw.map((p) => p.trim()).filter(Boolean)) {
    buf = buf ? `${buf} ${piece}` : piece;
    if (buf.length >= 12) { out.push(buf); buf = ''; }
  }
  if (buf) out.push(buf);
  return out;
}

function fetchTts(text) {
  return fetch(`/api/tts?text=${encodeURIComponent(text)}`, { credentials: 'same-origin' })
    .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`voice ${r.status}`))));
}

function stopSpeaking() {
  ttsGen += 1;
  ttsQueue = [];
  ttsPrefetch = null;
  ttsPlaying = false;
  player.pause();
  if ('speechSynthesis' in window) speechSynthesis.cancel();
}

async function playNext() {
  const gen = ttsGen;
  if (!ttsQueue.length) { ttsPlaying = false; return; }
  ttsPlaying = true;
  const text = ttsQueue.shift();
  const pending = ttsPrefetch && ttsPrefetch.text === text ? ttsPrefetch.promise : fetchTts(text);
  ttsPrefetch = null;
  try {
    const blob = await pending;
    if (gen !== ttsGen) return;
    if (ttsQueue.length) {
      ttsPrefetch = { text: ttsQueue[0], promise: fetchTts(ttsQueue[0]) };
      ttsPrefetch.promise.catch(() => {});
    }
    const url = URL.createObjectURL(blob);
    player.onended = () => { URL.revokeObjectURL(url); if (gen === ttsGen) playNext(); };
    player.src = url;
    await player.play();
  } catch {
    if (gen !== ttsGen) return;
    if (!('speechSynthesis' in window)) { playNext(); return; }
    const u = new SpeechSynthesisUtterance(text);  // hub voice unavailable: this sentence in the iPhone voice
    u.lang = 'en-US';
    u.onend = () => { if (gen === ttsGen) playNext(); };
    speechSynthesis.speak(u);
  }
}

function speak(text, interrupt = false) {
  if (!text || S.muted) return;
  if (interrupt) stopSpeaking();
  ttsQueue.push(...splitSentences(text));
  if (!ttsPlaying) playNext();
}
function describe(s) {
  if (s.status === 'needs_you') return `${s.name} needs you. ${s.message}`;
  if (s.status === 'done') return `${s.name} is done. ${s.message}`;
  return `${s.name} is ${STATUS_LABEL[s.status].toLowerCase()}.`;
}

async function loadReply(id) {
  if (!S.chunks[id]) {
    const r = await api(`/api/sessions/${id}/reply`);
    S.chunks[id] = r.chunks;
    S.replyText[id] = r.text || '';
  }
  return S.chunks[id];
}
const loadChunks = loadReply;
async function readChunk(id, i, prefix = '') {
  if (!byId(id)) return speak('That tab is gone.', true);
  if (i === 0) delete S.chunks[id];  // a cached reply can be from an earlier turn
  let chunks;
  try { chunks = await loadChunks(id); } catch { return speak('I could not load the reply.', true); }
  if (i >= chunks.length) {
    return speak(`${prefix} ${chunks.length ? "That's the end." : 'There is no reply to read.'}`.trim(), true);
  }
  S.readIdx[id] = i;
  speak(`${prefix} ${chunks[i]}`.trim(), true);
}

function announce(s) {
  if (s.id === S.selected) {
    if (s.status === 'done') readChunk(s.id, 0, `${s.name} is done.`);
    else speak(describe(s));
  } else {
    speak(s.status === 'done' ? `${s.name} is done.` : `${s.name} needs you.`);
  }
}

/* ---------- state ---------- */
function connect() {
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
  ws.onopen = () => $('#conn').classList.add('on');
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === 'state') onState(m); };
  ws.onclose = () => { $('#conn').classList.remove('on'); setTimeout(connect, 1500); };
}

function onState(m) {
  S.sessions = m.sessions; S.isLocal = m.is_local; S.canPair = !!m.can_pair; S.iterm = m.iterm_connected; S.source = m.source;
  for (const s of S.sessions) {
    const before = S.prev[s.id];
    if (before && before !== s.status) delete S.chunks[s.id];
    if (S.prevMessage[s.id] !== undefined && S.prevMessage[s.id] !== s.message) delete S.chunks[s.id];
    S.prevMessage[s.id] = s.message;
    if (before && before !== s.status) {
      if ((s.status === 'needs_you' || s.status === 'done') && !s.seen) announce(s);
    }
    S.prev[s.id] = s.status;
  }
  if (S.selected && !byId(S.selected)) {
    if (S.pending && S.pending.sessionId === S.selected) cancelPending('That tab is gone. Nothing was sent.');
    S.selected = null; save('selected', ''); setupRaw();
  }
  if (S.selectAfter && byId(S.selectAfter)) { const id = S.selectAfter; S.selectAfter = null; select(id); return; }
  render();
}

function ago(t) {
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

function render() {
  $('#pair').hidden = !S.canPair;  // any paired device may pair more (the state only reaches paired devices)
  const banner = $('#banner');
  banner.hidden = S.iterm;
  banner.textContent = S.source === 'tmux'
    ? 'tmux is not available on the server.'
    : 'Not connected to iTerm2. Make sure iTerm2 is running and Settings → General → Magic → "Enable Python API" is on.';
  document.body.classList.toggle('show-detail', !!S.selected);
  renderList();
  renderDetail();
}

function renderList() {
  const list = $('#list');
  list.replaceChildren();
  if (!S.sessions.length) { list.append(mk('p', 'empty', 'no sessions yet: start one with + or say “start Claude in myproject”')); return; }
  S.sessions.forEach((s, i) => {
    const b = document.createElement('button');
    b.className = `item ${s.status}${s.id === S.selected ? ' selected' : ''}${s.seen ? '' : ' unseen'}${s.offline ? ' offline' : ''}`;
    b.append(mk('span', 'num', String(i + 1)), mk('span', 'dot', ''));
    const main = mk('span', 'main', '');
    const name = mk('span', 'name', s.name);
    if (s.server) name.append(mk('span', 'server', s.server));  // which server the session runs on
    main.append(name, mk('span', 'sub', s.message || s.title));
    b.append(main, mk('span', 'when', ago(s.since)));
    b.setAttribute('aria-label', `${i + 1}. ${s.name}: ${STATUS_LABEL[s.status]}`);
    b.onclick = () => { unlockSpeech(); select(s.id); };
    list.append(b);
  });
}

function renderDetail() {
  const d = $('#detail');
  const s = byId(S.selected);
  d.replaceChildren();
  if (!s) { d.append(mk('p', 'empty', 'Select a tab, or hold the mic and say “status”.')); return; }

  const head = mk('div', 'head', '');
  head.append(mk('h2', '', s.name), mk('span', `pill ${s.status}`, STATUS_LABEL[s.status]));
  d.append(head, mk('p', 'cwd', s.cwd));

  const urls = mk('div', 'urls', '');
  s.urls.slice(0, 2).forEach((u) => {
    const row = mk('div', 'url', '');
    const a = mk('a', 'btn link', u.url.replace(/^https?:\/\//, ''));
    a.href = u.href; a.target = '_blank'; a.rel = 'noopener';
    row.append(a,
      mk('button', 'btn small', 'Pin', () => api(`/api/sessions/${s.id}/urls`, { method: 'POST', json: { action: 'pin', url: u.url } })),
      mk('button', 'btn small', 'Hide', () => api(`/api/sessions/${s.id}/urls`, { method: 'POST', json: { action: 'hide', url: u.url } })));
    urls.append(row);
  });
  if (s.urls.length > 2) {
    const more = document.createElement('details');
    more.append(mk('summary', 'muted', `${s.urls.length - 2} more`));
    s.urls.slice(2).forEach((u) => { const a = mk('a', 'btn link', u.url); a.href = u.href; a.target = '_blank'; more.append(a); });
    urls.append(more);
  }
  if (!s.urls.length) urls.append(mk('p', 'muted note', 'no app URL detected yet'));
  d.append(urls);

  const asking = s.status === 'needs_you';
  const actions = mk('div', 'actions', '');
  actions.append(
    mk('button', `btn${asking ? ' primary' : ''}`, asking ? 'Approve' : 'Enter', () => sendKey(s.id, 'enter')),
    mk('button', 'btn', asking ? 'Deny' : 'Stop', () => sendKey(s.id, 'escape')),
    mk('button', 'btn', 'Read aloud', () => { unlockSpeech(); readChunk(s.id, 0); }),
    mk('button', 'btn', S.showRaw ? 'Show reply' : 'Show terminal', () => { S.showRaw = !S.showRaw; setupRaw(); renderDetail(); }));
  d.append(actions);

  // A terminal window: the live screen, or the last reply as text.
  const term = mk('div', 'term', '');
  const bar = mk('div', 'term-bar', '');
  bar.append(mk('i', '', ''), mk('i', '', ''), mk('i', '', ''),
    mk('span', '', `${s.name} — ${S.showRaw ? 'screen' : 'last reply'}`));
  const pre = mk('pre', `reply${S.showRaw ? ' raw' : ''}`, S.showRaw ? (S.rawText || 'Loading terminal…') : 'Loading…');
  term.append(bar, pre);
  d.append(term);
  if (!S.showRaw) fillReply(s.id);
}

/* A reply as written: line breaks kept, **bold**, `code`, ``` blocks and # headings styled.
   Built from text nodes only (never innerHTML), so a reply can't inject markup. */
function renderReply(text) {
  const frag = document.createDocumentFragment();
  const inline = (line, into) => {
    line.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach((part) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) into.append(mk('b', '', part.slice(2, -2)));
      else if (/^`[^`]+`$/.test(part)) into.append(mk('code', 'inline', part.slice(1, -1)));
      else if (part) into.append(document.createTextNode(part));
    });
  };
  text.split(/```[^\n]*\n?/).forEach((block, i) => {
    if (i % 2) { frag.append(mk('span', 'codeblock', block.replace(/\n$/, ''))); return; }
    block.split('\n').forEach((line, j, lines) => {
      const h = line.match(/^#{1,4}\s+(.*)/);
      if (h) frag.append(mk('span', 'h', h[1]));
      else inline(line, frag);
      if (j < lines.length - 1) frag.append(document.createTextNode('\n'));
    });
  });
  return frag;
}

async function fillReply(id) {
  let text;
  try { await loadReply(id); text = S.replyText[id] || byId(id)?.message || 'No reply in this tab yet.'; }
  catch { text = 'Could not load the reply.'; }
  const pre = $('#detail .reply');
  if (pre && S.selected === id && !S.showRaw) pre.replaceChildren(renderReply(text));
}

function setupRaw() {
  clearInterval(S.rawTimer);
  S.rawText = '';
  if (!S.showRaw || !S.selected) return;
  const id = S.selected;
  const tick = async () => {
    try {
      S.rawText = (await api(`/api/sessions/${id}/screen`)).text;
      const pre = $('#detail .reply');
      if (pre && S.showRaw && S.selected === id) {
        const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
        pre.textContent = S.rawText;
        if (atBottom) pre.scrollTop = pre.scrollHeight;  // follow the output, like a terminal
      }
    } catch { /* tab gone; onState handles it */ }
  };
  tick();
  S.rawTimer = setInterval(tick, 2000);
}

function select(id) {
  S.selected = id; save('selected', id);
  api(`/api/sessions/${id}/seen`, { method: 'POST' }).catch(() => {});
  setupRaw();
  render();
}

async function sendKey(id, key) {
  try { await api(`/api/sessions/${id}/keys`, { method: 'POST', json: { key } }); }
  catch (e) { speak(e.status === 404 ? 'That tab is gone.' : 'Could not send that key.', true); }
}

/* ---------- pending reply ---------- */
function startPending(a) {
  cancelPending(null);
  const s = byId(a.session_id);
  if (!s) return speak('That tab is gone.', true);
  S.pending = { sessionId: a.session_id, timer: null };
  $('#pending-name').textContent = s.name;
  $('#pending-text').value = a.text;
  $('#pending').hidden = false;
  speak(a.speak, true);
  runCountdown();
}
function runCountdown() {
  const bar = $('#pending-bar');
  bar.style.transition = 'none'; bar.style.width = '100%';
  requestAnimationFrame(() => requestAnimationFrame(() => {
    bar.style.transition = `width ${PENDING_MS}ms linear`; bar.style.width = '0%';
  }));
  clearTimeout(S.pending.timer);
  S.pending.timer = setTimeout(commitPending, PENDING_MS);
}
function pausePending() {
  if (!S.pending) return;
  clearTimeout(S.pending.timer);
  const bar = $('#pending-bar');
  bar.style.width = getComputedStyle(bar).width; bar.style.transition = 'none';
}
async function sendNow(sessionId, text) {
  const s = byId(sessionId);
  if (!s) return speak('That tab is gone.', true);
  try {
    await api(`/api/sessions/${sessionId}/send`, { method: 'POST', json: { text } });
    toast(`Sent to ${s.name}.`);
  } catch (e) {
    toast(e.status === 404 ? 'That tab is gone. Nothing was sent.' : e.status === 409 ? e.message : 'Sending failed.');
  }
}

async function commitPending() {
  if (!S.pending) return;
  const { sessionId } = S.pending;
  const text = $('#pending-text').value.trim();
  clearTimeout(S.pending.timer);
  S.pending = null; $('#pending').hidden = true;
  if (!text) return;
  try {
    await api(`/api/sessions/${sessionId}/send`, { method: 'POST', json: { text } });
    speak('Sent.');
  } catch (e) {
    speak(e.status === 404 ? 'That tab is gone. Nothing was sent.' : e.status === 409 ? e.message : 'Sending failed.', true);
  }
}
function cancelPending(msg = 'Cancelled.') {
  if (!S.pending) return;
  clearTimeout(S.pending.timer);
  S.pending = null; $('#pending').hidden = true;
  if (msg) speak(msg, true);
}
$('#pending-text').addEventListener('focus', pausePending);
$('#pending-cancel').onclick = () => cancelPending();
$('#pending-send').onclick = () => commitPending();

/* ---------- actions ---------- */
function openUrl(id, index) {
  const s = byId(id);
  const u = s && s.urls[index];
  if (!u) return speak('No app URL for this tab.', true);
  const w = window.open(u.href, '_blank');
  if (!w) { toast(`Tap to open ${u.url}`, u.href); speak('Tap the link to open it.', true); }
  else speak(`Opening ${s.name}.`, true);
}

async function newSession(project, where = '', task = '', said = '', create = false) {
  try {
    const r = await api('/api/new_session', { method: 'POST', json: { project, where, task, create } });
    S.selectAfter = r.id;
    speak(said || `Starting a session in ${project}.`, true);
  } catch (e) { toast(e.message); }
}

async function handleAction(a, typed = false) {
  // Typed text needs no countdown: it is exactly what you meant. The countdown is for speech, to catch mishearing.
  if (typed && a.type === 'reply') return sendNow(a.session_id, a.text);
  switch (a.type) {
    case 'speak': case 'ask': speak(a.text, true); break;
    case 'select': select(a.session_id); speak(a.speak, true); break;
    case 'keys': await sendKey(a.session_id, a.key); speak(a.speak, true); break;
    case 'reply': startPending(a); break;
    case 'read': readChunk(a.session_id, 0); break;
    case 'read_more': readChunk(a.session_id, (S.readIdx[a.session_id] ?? -1) + 1); break;
    case 'open_url': openUrl(a.session_id, a.index); break;
    case 'new_session': await newSession(a.project, a.where || '', a.task || '', a.speak || ''); break;
    case 'cancel': S.pending ? cancelPending() : speak('Nothing to cancel.', true); break;
    case 'send': S.pending ? commitPending() : speak('Nothing to send.', true); break;
    default: speak("I'm not sure what to do.", true);
  }
}

/* ---------- voice in ---------- */
let stream = null;
let rec = null;
let recStart = 0;

function pickMime() {
  if (!window.MediaRecorder) return '';
  for (const m of ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm']) if (MediaRecorder.isTypeSupported(m)) return m;
  return '';
}
async function startRec() {
  pausePending();
  stopSpeaking();
  if (!stream) stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mime = pickMime();
  const r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const parts = [];
  const type = r.mimeType || mime || 'audio/webm';
  r.ondataavailable = (e) => { if (e.data.size) parts.push(e.data); };
  r.onstop = () => sendVoice(new Blob(parts, { type }), type);
  r.start();
  rec = r; recStart = Date.now();
  $('#talk').classList.add('rec');
  setHeard('Listening…');
}
function stopRec() {
  if (!rec) return;
  const r = rec; rec = null;
  $('#talk').classList.remove('rec');
  r.stop();
}
async function sendVoice(blob, type) {
  if (blob.size < 1000) { setHeard(''); return; }
  const form = new FormData();
  form.append('audio', blob, `speech.${type.includes('mp4') ? 'm4a' : 'webm'}`);
  form.append('selected', S.selected || '');
  setHeard('Transcribing…');
  try {
    const r = await api('/api/voice', { method: 'POST', form });
    setHeard(r.text ? `“${r.text}”` : '');
    await handleAction(r.action);
  } catch (e) {
    setHeard('');
    speak('Sorry, voice failed.', true);
    toast(e.message);
  }
}

const talk = $('#talk');
talk.addEventListener('pointerdown', async (e) => {
  e.preventDefault();
  unlockSpeech();
  if (rec) { stopRec(); return; }        // second tap in toggle mode
  try { await startRec(); } catch (err) { speak('Microphone is not available.', true); toast(err.message); }
});
function release() {
  if (rec && Date.now() - recStart >= 350) stopRec();   // held: stop on release; short tap: keep recording
}
talk.addEventListener('pointerup', release);
talk.addEventListener('pointercancel', release);
talk.addEventListener('contextmenu', (e) => e.preventDefault());

document.addEventListener('visibilitychange', () => {
  if (document.hidden && stream && !rec) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
});

/* ---------- typed commands ---------- */
$('#typed').addEventListener('submit', async (e) => {
  e.preventDefault();
  unlockSpeech();
  const input = $('#typed-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  try {
    const r = await api('/api/command', { method: 'POST', json: { text, selected: S.selected || '' } });
    setHeard(`“${r.text}”`);
    await handleAction(r.action, true);
  } catch (err) { toast(err.message); }
});

/* ---------- header buttons ---------- */
$('#back').onclick = () => { S.selected = null; save('selected', ''); setupRaw(); render(); };

const ICON_SOUND = '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/></svg>';
const ICON_MUTED = '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9l5 6M22 9l-5 6"/></svg>';
function renderMute() {
  const b = $('#mute');
  b.innerHTML = S.muted ? ICON_MUTED : ICON_SOUND;  // constant markup, never data
  b.classList.toggle('off', S.muted);
}
$('#mute').onclick = () => {
  S.muted = !S.muted; save('muted', S.muted ? '1' : '0');
  if (S.muted) stopSpeaking();
  renderMute();
};

$('#new').onclick = async () => {
  let projects;
  try { projects = (await api('/api/projects')).projects; } catch (e) { toast(e.message); return; }
  const sheet = $('#sheet');
  sheet.replaceChildren();
  const form = document.createElement('form'); form.method = 'dialog';
  const list = mk('div', 'plist', '');
  projects.forEach((p) => list.append(mk('button', 'btn', p, (ev) => { ev.preventDefault(); sheet.close(); newSession(p); })));
  if (!projects.length) list.append(mk('p', 'muted note', 'no project folders yet: create one below'));
  // A new project: its folder is created in the projects folder (Settings → Sessions), then the session starts.
  const name = document.createElement('input');
  name.placeholder = 'new-project-name'; name.autocomplete = 'off'; name.autocapitalize = 'off'; name.spellcheck = false;
  const create = mk('button', 'btn primary', 'Create & start', (ev) => {
    ev.preventDefault();
    const project = name.value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(project)) { toast('Use letters, digits, . _ - (no spaces or slashes).'); return; }
    sheet.close();
    newSession(project, '', '', `Creating ${project} and starting a session.`, true);
  });
  const box = document.createElement('fieldset');
  box.className = 'newproj';
  box.append(mk('legend', '', 'New project'), name, create);
  // Any existing folder on the hub, under its user's home (e.g. ~/work/api).
  const path = document.createElement('input');
  path.placeholder = '~/work/api'; path.autocomplete = 'off'; path.autocapitalize = 'off'; path.spellcheck = false;
  const open = mk('button', 'btn primary', 'Start here', async (ev) => {
    ev.preventDefault();
    const where = path.value.trim();
    if (!where) return;
    try {
      const r = await api('/api/new_session', { method: 'POST', json: { project: '', path: where } });
      sheet.close(); S.selectAfter = r.id; speak(`Starting a session in ${where}.`, true);
    } catch (e) { toast(e.message); }
  });
  const folder = document.createElement('fieldset');
  folder.className = 'newproj';
  folder.append(mk('legend', '', 'Open a folder'), path, open);
  form.append(mk('h2', '', 'New session'), list, box, folder, mk('button', 'btn', 'Close'));
  sheet.append(form);
  sheet.showModal();
};

$('#pair').onclick = async () => {
  let r;
  try { r = await api('/api/pair', { method: 'POST' }); } catch (e) { toast(e.message); return; }
  const sheet = $('#sheet');
  sheet.replaceChildren();
  const form = document.createElement('form'); form.method = 'dialog';
  const qr = mk('div', 'qr', ''); qr.innerHTML = r.svg;   // svg generated by our own server
  form.append(mk('h2', '', 'Pair your phone'),
    mk('p', '', 'Connect the phone to your private network (NetBird, Tailscale…), then scan with the Camera app. The link works once, for 10 minutes.'),
    qr, mk('p', 'muted', r.url),
    mk('button', 'btn', 'Unpair all phones', async (ev) => {
      ev.preventDefault();
      try { await api('/api/revoke', { method: 'POST' }); sheet.close(); toast('All phones unpaired.'); }
      catch (e) { toast(e.message); }
    }),
    mk('button', 'btn', 'Done'));
  sheet.append(form);
  sheet.showModal();
};

/* ---------- settings (shared by every device; changes apply at once) ---------- */
$('#settings').onclick = async () => {
  let s, voices;
  try { [s, { voices }] = await Promise.all([api('/api/settings'), api('/api/tts/voices')]); }
  catch (e) { toast(e.message); return; }
  const sheet = $('#sheet');
  sheet.replaceChildren();
  const form = document.createElement('form'); form.method = 'dialog'; form.className = 'settings';
  const inputs = {};
  const field = (f) => {
    const label = mk('label', '', f.label);
    let input;
    const value = f.key === 'llm_key' ? '' : String(s.values[f.key] ?? '');
    if ((f.key === 'tts_voice' && voices.length) || f.key === 'llm_api') {
      input = document.createElement('select');
      const options = f.key === 'llm_api' ? ['ollama', 'openai'] : voices;
      options.forEach((o) => { const opt = mk('option', '', o); opt.value = o; input.append(opt); });
    } else {
      input = document.createElement('input');
      input.type = f.key === 'llm_key' ? 'password' : 'text';
      input.autocomplete = f.key === 'llm_key' ? 'new-password' : 'off';  // never autofilled
      if (f.key === 'llm_key') input.placeholder = s.values.llm_key ? 'set — type to replace' : 'none';
    }
    input.value = value;
    input.dataset.initial = value;
    inputs[f.key] = input;
    label.append(input);
    return label;
  };
  form.append(mk('h2', '', 'Settings'));
  [...new Set(s.fields.map((f) => f.group))].forEach((g) => {
    const box = document.createElement('fieldset');
    box.append(mk('legend', '', g));
    s.fields.filter((f) => f.group === g).forEach((f) => box.append(field(f)));
    form.append(box);
  });
  const test = document.createElement('input'); test.placeholder = `e.g. “${s.wake_phrase}, status”`;
  const testOut = mk('p', 'muted', '');
  const testBox = document.createElement('fieldset');
  testBox.append(mk('legend', '', 'Test the wake word'), test, mk('button', 'btn', 'Test', async (ev) => {
    ev.preventDefault();
    try {
      const r = await api('/api/settings/test-wake', { method: 'POST', json: { text: test.value } });
      testOut.textContent = r.matches ? `✓ Heard. Command: “${r.rest}”` : '✗ No wake word at the start';
    } catch (e) { testOut.textContent = e.message; }
  }), testOut);
  const ro = document.createElement('fieldset');
  ro.append(mk('legend', '', 'Set when deploying (tabdeck setup)'));
  Object.entries(s.read_only).forEach(([k, v]) => ro.append(mk('p', 'muted', `${k.replace(/_/g, ' ')}: ${v}`)));
  const status = mk('p', 'muted', '');
  const save = mk('button', 'btn primary', 'Save', async (ev) => {
    ev.preventDefault();
    const changes = {};
    Object.entries(inputs).forEach(([k, el]) => {
      if (el.value !== el.dataset.initial) changes[k] = /timeout$/.test(k) ? Number(el.value) : el.value;
    });
    if (!Object.keys(changes).length) { status.textContent = 'Nothing changed.'; return; }
    try { await api('/api/settings', { method: 'POST', json: changes }); sheet.close(); toast('Saved. In use now.'); }
    catch (e) { status.textContent = e.message; }
  });
  const reload = mk('button', 'btn', 'Reload config', async (ev) => {
    ev.preventDefault();
    try {
      const r = await api('/api/settings/reload', { method: 'POST' });
      sheet.close();
      toast(r.restart_needed.length ? `Reloaded. Restart needed for: ${r.restart_needed.join(', ')}` : 'Reloaded.');
    } catch (e) { status.textContent = e.message; }
  });
  form.append(testBox, ro, status, save, reload, mk('button', 'btn', 'Close'));
  sheet.append(form);
  sheet.showModal();
};

renderMute();
render();
connect();
setInterval(renderList, 30000);
