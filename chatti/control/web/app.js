import { createFace } from '/static/eyes.js';

const face = createFace(document.getElementById('stage'));
const $ = (id) => document.getElementById(id);

let catalogData = { llms: [], voices: [], asr: [] };
let currentConfig = null;
let pollTimer = null;
let conversationsSignature = '';

/* ---------- helpers ---------- */

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.classList.toggle('err', isError);
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 4000);
}

async function api(path, options) {
  const r = await fetch(path, options);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return r;
}

function formatTime(epochSeconds) {
  // The transcript stores epoch seconds precisely so this conversion happens
  // here, in the viewer's timezone — the container most likely runs on UTC.
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay
    ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('en-GB', { day: '2-digit', month: '2-digit',
                                  hour: '2-digit', minute: '2-digit' });
}

/* ---------- status ---------- */

// Labels per row. The model is not a service that runs, it is memory that is
// occupied — calling that "Stop" would read as switching something off.
const ACTIONS = {
  model:    { start: 'Load',  stop: 'Unload', busyStart: 'Loading…',  busyStop: 'Unloading…' },
  _default: { start: 'Start', stop: 'Stop',   busyStart: 'Starting…', busyStop: 'Stopping…' },
};

// "busy" is up, only too occupied to answer a health check — offering "Start"
// for it would be wrong. For the model only a plain "ok" counts as loaded;
// "busy" there means LM Studio is working and the load state is unknown.
function isUp(key, state) {
  return key === 'model' ? state === 'ok' : (state === 'ok' || state === 'busy');
}

function renderServices(services, job) {
  const jobRunning = job.state === 'running';
  for (const li of document.querySelectorAll('.services li')) {
    const key = li.dataset.key;
    const step = jobRunning ? (job.steps || []).find((s) => s.key === key) : null;
    const service = services[key] || {};
    // While a job runs, the step is the more informative source.
    const state = step && step.state !== 'pending' ? step.state : service.state;
    const detail = step && step.state !== 'pending' && step.detail
      ? step.detail
      : (service.detail || '');

    const dot = li.querySelector('.dot');
    dot.className = 'dot ' + ({ running: 'starting', done: 'ok', failed: 'error',
                                pending: 'off' }[state] || state || 'off');
    li.classList.toggle('running', state === 'running');
    li.querySelector('.detail').textContent = detail;

    // The button always offers the opposite of what the service does now. It
    // reads the *service* state, not the step: mid-job the step says "running",
    // which is about the job, not about whether the thing is up.
    const button = li.querySelector('.svc-btn');
    const labels = ACTIONS[key] || ACTIONS._default;
    const action = isUp(key, service.state) ? 'stop' : 'start';
    button.dataset.action = action;
    button.textContent = step
      ? (job.mode === 'stop' ? labels.busyStop : labels.busyStart)
      : labels[action];
    // Disabled during *any* job: the server refuses a second one with 409, and
    // a button that looks clickable and is not is worse than a greyed-out one.
    button.disabled = jobRunning;
  }
}

// The big button offers the opposite of what the stack is doing — the same rule
// the row buttons follow. "Everything is up" counts the model in: with it
// unloaded the next question pays the loading time, so the stack is not ready.
function renderStartButton(services, job) {
  const rows = [...document.querySelectorAll('.services li')];
  const allUp = rows.length > 0
    && rows.every((li) => isUp(li.dataset.key, (services[li.dataset.key] || {}).state));
  const action = allUp ? 'stop' : 'start';

  const button = $('start-btn');
  button.dataset.action = action;
  // A running job only speaks for this button when it is *this* button's job.
  // A single row being stopped has one step, and used to make the big button
  // claim the whole stack was moving.
  const wholeStack = job.state === 'running' && (job.steps || []).length === rows.length;
  button.textContent = wholeStack
    ? (job.mode === 'stop' ? 'Stopping…' : 'Starting…')
    : (action === 'stop' ? 'Stop everything' : 'Start everything');
  // Shutting the whole stack down should not wear the page's most inviting
  // colour. Same reasoning as the quiet row buttons: starting is the offer,
  // stopping is merely available.
  button.classList.toggle('primary', action === 'start');
  button.classList.toggle('ghost', action === 'stop');
}

function ago(epochSeconds) {
  const seconds = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (seconds < 90) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return formatTime(epochSeconds);
}

function renderDevice(device, services, job) {
  const line = $('device-line');
  const text = $('device-text');
  const note = $('device-note');
  const dot = $('device-dot');
  note.textContent = '';
  note.className = 'device-note';

  // The dot answers one question only: is the chatti reachable right now.
  // Green yes, red no, amber "cannot tell" — amber rather than red because a
  // red light for an unanswered question reads as a fault report.
  const light = (kind) => { dot.className = 'dot ' + kind; };

  if (job.state === 'running') {
    face.setState('thinking');
    light('starting');
    line.className = 'device-line busy';
    text.textContent = 'Chatti is starting…';
    return;
  }

  // "busy" is up, just occupied — during a conversation the server has no time
  // for a health check, and reporting that as "not running" was plain wrong.
  const serverUp = services.xiaozhi
    && (services.xiaozhi.state === 'ok' || services.xiaozhi.state === 'busy');
  const address = (device && device.address) || {};
  const presence = (device && device.present) || {};
  const seen = device && device.last_seen;
  const heard = seen && seen.last_seen ? `Last heard ${ago(seen.last_seen)}.` : '';

  // The firmware only opens the WebSocket while a conversation runs, so a live
  // connection means "in a conversation" — never "switched on". Anything else
  // would show "not connected" almost all the time and read like a fault.
  if (device && device.talking) {
    // An open channel covers the whole exchange — listening, recognising,
    // thinking, answering. Which of those is currently happening is not
    // something the server exposes, so do not claim one of them.
    face.setState('idle');
    light('ok');
    line.className = 'device-line connected';
    text.textContent = 'in a conversation';
    note.textContent = services.xiaozhi && services.xiaozhi.state === 'busy'
      ? 'The server is processing right now — that takes about 45 seconds.' : '';
    return;
  }

  if (!serverUp) {
    face.setState('connecting');
    light('error');
    line.className = 'device-line';
    text.textContent = 'Server is not running';
    return;
  }

  if (address.ok === false) {
    // The failure that looks like a dead device but is a network problem.
    face.setState('connecting');
    light('error');
    line.className = 'device-line';
    text.textContent = 'not reachable for the Chatti';
    note.textContent = address.detail || '';
    note.className = 'device-note warn';

    if (address.suggested) {
      const button = document.createElement('button');
      button.className = 'ghost fix-address';
      button.textContent = `Switch to ${address.suggested}`;
      button.addEventListener('click', () => fixAddress(address.suggested, button));
      note.append(document.createElement('br'), button);
    }
    return;
  }

  // Everything on the PC side is fine from here on, so the remaining question is
  // about the device itself — and it has three answers, not two. "I cannot tell"
  // is not the same as "it is off" and must not be shown as one.
  if (presence.state === 'on') {
    face.setState('idle');
    light('ok');
    line.className = 'device-line connected';
    text.textContent = 'Start a new conversation';
    // Since 2026-08-19 the whole face is the talk button; BOOT only brings the
    // speaker and subtitle icons back for a moment.
    note.textContent = 'Tap the Chatti, speak, then tap it again. '
                     + (heard || 'The answer takes about 45 seconds.');
    return;
  }

  if (presence.state === 'off') {
    face.setState('off');
    light('error');
    line.className = 'device-line off';
    text.textContent = 'Chatti is off';
    note.textContent = 'The PC is ready. Plug the Chatti in, or press PWR briefly.'
                     + (heard ? ' ' + heard : '');
    return;
  }

  // unknown: asleep and therefore silent, never seen on this LAN, or the
  // lookup itself failed. All three are "cannot tell" — and while the firmware
  // sleeps its radio this is the *normal* resting answer, not an incident, so
  // the line leads with what is certain (the PC side is ready) instead of with
  // the doubt.
  face.setState('connecting');
  light('warn');
  line.className = 'device-line';
  text.textContent = 'ready';
  note.textContent = (presence.detail
    ? `Whether the Chatti is on right now cannot be told from here: ${presence.detail}. `
    : 'Whether the Chatti is on right now cannot be told from here. ')
    + (heard || 'Tap the Chatti to start a conversation.');
}

const CONTROLS = ['sel-llm', 'sel-voice', 'sel-asr', 'sel-prompt'];

function renderConfig(config) {
  if (!config || config.error) return;

  // Rebuilding a <select> replaces its options and throws away whatever the
  // user had picked but not yet applied. With a 3 s poll that silently undid
  // every choice made more slowly than that — and doing it to an open dropdown
  // is what made the lists feel stuck. So the poll only refreshes these fields
  // while nobody is working in them and nothing is waiting to be saved.
  const pending = isDirty();                       // still against the old config
  currentConfig = config;
  const active = document.activeElement;
  if (pending || (active && CONTROLS.includes(active.id))) return;
  fillSelects();
}

async function poll() {
  try {
    const status = await (await api('/api/status')).json();
    if (status.app !== 'chatti-control') {
      toast('Something else is answering on this port.', true);
      return;
    }
    renderServices(status.services, status.job);
    renderDevice(status.services.device, status.services, status.job);
    renderConfig(status.config);

    const busy = status.job.state === 'running' || status.restart.state === 'running';
    $('start-btn').disabled = busy;
    renderStartButton(status.services, status.job);
    $('save-btn').disabled = busy || !isDirty();
    // The restart carries its own progress text because applying a new LLM does
    // not end with the container: the model still has to be loaded, and that is
    // the part that takes minutes. A fixed "restarting…" would look frozen.
    const restartText = {
      running: status.restart.detail || 'Server is restarting…',
      done: status.restart.detail === 'ready'
        ? 'Server is ready again.'
        : `Ready — ${status.restart.detail}`,
      failed: `Failed: ${status.restart.detail}`,
    }[status.restart.state];
    if (restartText) $('save-hint').textContent = restartText;

    renderFlash(status.flash);
    // The address to type in by hand when discovery is blocked. Taken from the
    // server's own configuration, not assembled here — that is exactly the
    // string the device has to reach.
    if (status.config.ota) $('manual-ota').textContent = status.config.ota;
    const ann = status.announce;
    $('announce-line').textContent = ann.active
      ? `The server announces itself on the network at ${ann.addresses.join(', ')} — `
        + 'a new Chatti finds it on its own.'
      : `Announcement on the network not active${ann.error ? ': ' + ann.error : ''}. `
        + 'The server address then has to be entered on the device itself.';

    const signature = `${status.conversations.files}:${status.conversations.bytes}`;
    if (signature !== conversationsSignature) {
      conversationsSignature = signature;
      loadConversations();
    }

    schedulePoll(busy ? 1500 : 3000);
  } catch (e) {
    schedulePoll(5000);
  }
}

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  if (document.hidden) return;   // no point polling a background tab
  pollTimer = setTimeout(poll, delay);
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) poll();
});

/* ---------- catalog and selection ---------- */

function option(value, label, selected) {
  const o = document.createElement('option');
  o.value = value;
  o.textContent = label;
  o.selected = selected;
  return o;
}

function fillSelects() {
  if (!currentConfig) return;

  const llm = $('sel-llm');
  llm.replaceChildren();
  const llms = catalogData.llms.length
    ? catalogData.llms
    : [{ id: currentConfig.llm, label: currentConfig.llm, loaded: false }];
  for (const m of llms) {
    if (!m.id) continue;
    llm.append(option(m.id, m.label + (m.loaded ? '  (loaded)' : ''),
                      m.id === currentConfig.llm));
  }
  $('hint-llm').textContent = catalogData.llms.length
    ? 'From LM Studio. A newly chosen model is loaded with the first question.'
    : 'LM Studio is not answering — only the configured value can be chosen.';

  const voice = $('sel-voice');
  voice.replaceChildren();
  const currentVoiceId = `${currentConfig.tts_model}|${currentConfig.tts_voice}`;
  const voices = catalogData.voices.length
    ? catalogData.voices
    : [{ id: currentVoiceId, label: currentConfig.tts_model || '—' }];
  for (const v of voices) voice.append(option(v.id, v.label, v.id === currentVoiceId));

  const asr = $('sel-asr');
  asr.replaceChildren();
  const asrs = catalogData.asr.length
    ? catalogData.asr
    : [{ id: currentConfig.asr, label: currentConfig.asr || '—' }];
  for (const a of asrs) asr.append(option(a.id, a.label, a.id === currentConfig.asr));

  if (document.activeElement !== $('sel-prompt')) {
    $('sel-prompt').value = (currentConfig.prompt || '').trim();
  }
}

function isDirty() {
  if (!currentConfig) return false;
  const voiceId = `${currentConfig.tts_model}|${currentConfig.tts_voice}`;
  return $('sel-llm').value !== (currentConfig.llm || '')
      || $('sel-voice').value !== voiceId
      || $('sel-asr').value !== (currentConfig.asr || '')
      || $('sel-prompt').value.trim() !== (currentConfig.prompt || '').trim();
}

async function loadCatalog() {
  try {
    catalogData = await (await api('/api/catalog')).json();
    fillSelects();
  } catch (e) {
    toast('Model lists not reachable: ' + e.message, true);
  }
}

/* ---------- conversations ---------- */

async function loadConversations() {
  try {
    const list = await (await api('/api/conversations?limit=20')).json();
    const box = $('conversations');
    box.replaceChildren();
    $('conv-count').textContent = list.length ? `${list.length} most recent` : '';

    if (!list.length) {
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'No conversations recorded yet.';
      box.append(p);
      return;
    }

    for (const conv of list) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');

      const title = document.createElement('span');
      title.className = 'conv-title';
      title.textContent = conv.title || '(no text)';

      const time = document.createElement('span');
      time.className = 'conv-time';
      time.textContent = formatTime(conv.started);

      summary.append(title, time);
      details.append(summary);

      const turns = document.createElement('div');
      turns.className = 'turns';
      for (const t of conv.turns) {
        const p = document.createElement('p');
        p.className = 'turn ' + t.role;
        const who = document.createElement('span');
        who.className = 'who';
        who.textContent = t.role === 'user' ? 'You' : 'Chatti';
        const what = document.createElement('span');
        what.className = 'what';
        what.textContent = t.text;
        p.append(who, what);
        turns.append(p);
      }
      details.append(turns);
      box.append(details);
    }
  } catch (e) {
    /* leave the previous list standing rather than blanking it */
  }
}

/* ---------- first-run checklist ---------- */

// Not part of the 3 s poll on purpose: this shells out to docker and netsh and
// takes seconds. It runs once at load and again after a fix.
async function loadSetup() {
  const btn = $('setup-btn');
  btn.disabled = true;
  btn.textContent = 'Checking…';
  try {
    const data = await (await api('/api/setup')).json();
    $('setup-summary').textContent = data.ready
      ? 'Everything is there — the Chatti can get going.'
      : `${data.open} item${data.open === 1 ? '' : 's'} open.`;
    renderChecklist(data.items);
  } catch (e) {
    $('setup-summary').textContent = `Check failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check';
  }
}

function renderChecklist(items) {
  const list = $('checklist');
  list.replaceChildren();
  for (const item of items) {
    const li = document.createElement('li');
    li.className = item.ok ? 'ok' : (item.optional ? 'warn' : 'bad');

    const dot = document.createElement('span');
    dot.className = 'dot';
    const label = document.createElement('b');
    label.textContent = item.label;
    const detail = document.createElement('span');
    detail.className = 'detail';
    detail.textContent = item.detail;
    li.append(dot, label, detail);

    if (item.fix) {
      const button = document.createElement('button');
      button.className = 'ghost';
      button.textContent = 'Fix';
      button.addEventListener('click', () => runFix(item.fix, button));
      li.append(button);
    }
    if (item.hint) {
      const hint = document.createElement('p');
      hint.className = 'hint sub';
      // Kept as text, never innerHTML: the firewall hint is a command line.
      hint.textContent = item.hint;
      li.append(hint);
    }
    list.append(li);
  }
}

async function runFix(action, button) {
  button.disabled = true;
  button.textContent = 'Running…';
  try {
    await api('/api/setup/fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    // The work runs in the background; ask again until it settles rather than
    // holding the request open for what may be a multi-gigabyte download.
    const watch = setInterval(async () => {
      try {
        const data = await (await api('/api/setup')).json();
        button.textContent = data.job.detail || 'Running…';
        if (data.job.state !== 'running') {
          clearInterval(watch);
          if (data.job.state === 'failed') toast(data.job.error, true);
          loadSetup();
        }
      } catch { /* keep watching */ }
    }, 2000);
  } catch (e) {
    toast(e.message, true);
    button.disabled = false;
    button.textContent = 'Fix';
  }
}

/* ---------- device: ports and flashing ---------- */

let flashWasRunning = false;
// The other COM ports are hidden while the Chatti is recognised; this is the
// escape hatch for a board that reports an unexpected USB id.
let showAllPorts = false;
// What the dropdown had before this poll, so a reload does not throw away a
// choice that was made deliberately. Empty until the first render, when the
// backend's default (English) wins.
let firmwareChoice = '';
let firmwareVariants = [];

function portToggle(label, value) {
  const a = document.createElement('button');
  a.className = 'linkish';
  a.textContent = label;
  a.addEventListener('click', () => { showAllPorts = value; loadFlash(); });
  return a;
}

async function loadFlash() {
  try {
    const data = await (await api('/api/flash')).json();
    const select = $('sel-port');
    const previous = select.value;
    const esp = data.ports.find((p) => p.is_esp);

    // Only the Chatti, unless there is none or the user asked for the rest.
    // A PC typically carries a mainboard serial port and two Bluetooth ones;
    // offering those as choices next to "Flash firmware" reads like four
    // equal options when exactly one is right.
    const others = data.ports.filter((p) => !p.is_esp);
    const visible = (esp && !showAllPorts) ? data.ports.filter((p) => p.is_esp) : data.ports;

    select.replaceChildren();
    for (const p of visible) {
      select.append(option(p.port, p.is_esp ? `${p.port} — Chatti` : `${p.port} — ${p.description}`,
                           p.port === previous));
    }
    if (esp && !previous) select.value = esp.port;

    const hint = $('hint-port');
    hint.replaceChildren();
    if (!esp) {
      hint.append(document.createTextNode(
        'No Espressif device found — check the cable. All ports are listed.'));
    } else if (showAllPorts) {
      hint.append(document.createTextNode('The Chatti was detected. '));
      hint.append(portToggle('show only the Chatti', false));
    } else {
      hint.append(document.createTextNode('The Chatti was detected.'));
      if (others.length) {
        hint.append(document.createTextNode(' '));
        hint.append(portToggle(`show ${others.length} more ports`, true));
      }
    }

    firmwareVariants = data.variants || [];
    renderFirmwareChoices(data.default_variant || '');

    $('flash-btn').disabled = !firmwareVariants.length || !data.ports.length
      || data.job.state === 'running';
    renderFlash(data.job);
  } catch (e) {
    $('firmware-line').textContent = `Cannot be read: ${e.message}`;
  }
}

// The language is compiled into the firmware, so picking one here is picking a
// build, not a setting - which is why the choice sits next to the port and not
// under Setup. The backend decides what "default" means (settings.py); the page
// only keeps a choice that was already made.
function renderFirmwareChoices(defaultId) {
  const select = $('sel-firmware');
  const keep = firmwareChoice
    && firmwareVariants.some((v) => v.id === firmwareChoice);
  if (!keep) firmwareChoice = defaultId || (firmwareVariants[0] || {}).id || '';

  select.replaceChildren();
  for (const v of firmwareVariants) {
    select.append(option(v.id, v.label, v.id === firmwareChoice));
  }
  select.disabled = firmwareVariants.length < 2;
  renderFirmwareLine();
}

function renderFirmwareLine() {
  const fw = firmwareVariants.find((v) => v.id === firmwareChoice);
  // Whether WLAN and server address survive is the one thing worth knowing
  // before pressing the button - a single merged image writes over NVS.
  $('firmware-line').textContent = fw
    ? `${(fw.size / 1048576).toFixed(1)} MB, built ${formatTime(fw.built_at)}`
      + (fw.keeps_settings
          ? ' — Wi-Fi and server address are kept.'
          : ' — Careful: erases Wi-Fi and server address, the device has to be set up again afterwards.')
    : 'No firmware file found. Build one with chatti\\build-releases.ps1.';
  $('firmware-line').classList.toggle('warn-text', !!fw && !fw.keeps_settings);
}

function renderFlash(job) {
  const running = job.state === 'running';
  $('flash-progress').hidden = job.state === 'idle';
  $('flash-bar').style.width = `${job.percent}%`;
  $('flash-log').hidden = job.state === 'idle';
  $('flash-log').textContent = (job.log || []).slice(-8).join('\n');

  $('flash-hint').textContent = {
    idle: 'Takes about 40 seconds. Do not unplug.',
    running: job.detail || 'Running…',
    done: 'Done — the Chatti is restarting.',
    failed: job.error || 'Failed.',
  }[job.state];

  $('flash-btn').disabled = running;
  $('flash-btn').textContent = running ? `${job.percent}%` : 'Flash firmware';

  // One toast when it ends, not one per poll.
  if (flashWasRunning && !running) {
    toast(job.state === 'done' ? 'Firmware flashed.' : (job.error || 'Flashing failed.'),
          job.state !== 'done');
  }
  flashWasRunning = running;
}

/* ---------- tabs ---------- */

// Panels are only hidden, never unmounted: the 3 s poll keeps writing into all
// of them, and re-rendering on every tab switch would throw away the flash log
// and re-fetch the conversation list for nothing.
function showTab(name) {
  for (const page of document.querySelectorAll('.page')) {
    page.hidden = page.dataset.page !== name;
  }
  for (const tab of document.querySelectorAll('.tab')) {
    tab.classList.toggle('is-active', tab.dataset.tab === name);
  }
  // Survives a reload, and makes a tab linkable.
  if (location.hash.slice(1) !== name) history.replaceState(null, '', `#${name}`);
}

for (const tab of document.querySelectorAll('.tab')) {
  tab.addEventListener('click', () => showTab(tab.dataset.tab));
}
window.addEventListener('hashchange', () => {
  const name = location.hash.slice(1);
  if (document.querySelector(`.page[data-page="${name}"]`)) showTab(name);
});

/* ---------- actions ---------- */

$('setup-btn').addEventListener('click', loadSetup);
$('ports-btn').addEventListener('click', loadFlash);

// Remembered rather than read off the element at flash time, so the next poll
// re-renders the dropdown without silently changing what is about to be
// written.
$('sel-firmware').addEventListener('change', (event) => {
  firmwareChoice = event.target.value;
  renderFirmwareLine();
});

$('flash-btn').addEventListener('click', async () => {
  const port = $('sel-port').value;
  if (!port) { toast('No port selected.', true); return; }
  $('flash-btn').disabled = true;
  try {
    await api('/api/flash', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port, variant: firmwareChoice }),
    });
    poll();
  } catch (e) {
    toast(e.message, true);
    $('flash-btn').disabled = false;
  }
});

// One listener for all five rows — the buttons carry which service and which
// direction they mean.
$('services').addEventListener('click', async (event) => {
  const button = event.target.closest('.svc-btn');
  if (!button) return;
  const key = button.closest('li').dataset.key;
  const action = button.dataset.action;
  button.disabled = true;
  try {
    await api(`/api/service/${key}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    poll();
  } catch (e) {
    toast(e.message, true);
    button.disabled = false;
  }
});

$('start-btn').addEventListener('click', async () => {
  // Read off the button rather than recomputed: what the user clicked is what
  // the label promised, even if a poll lands between render and click.
  const action = $('start-btn').dataset.action === 'stop' ? 'stop' : 'start';
  $('start-btn').disabled = true;          // against a double click inside the poll window
  try {
    await api('/api/startup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    poll();
  } catch (e) {
    toast(e.message, true);
    $('start-btn').disabled = false;
  }
});

$('save-btn').addEventListener('click', async () => {
  const [model, voice] = $('sel-voice').value.split('|');
  $('save-btn').disabled = true;
  try {
    const result = await (await api('/api/selection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        llm: $('sel-llm').value || null,
        tts_model: model || null,
        tts_voice: voice || null,
        asr: $('sel-asr').value || null,
        prompt: $('sel-prompt').value,
        restart: true,
      }),
    })).json();
    toast(result.changed.length
      ? `Saved: ${result.changed.join(', ')} — the server is restarting`
        + (result.loading_model ? ' and the language model is being loaded.' : '.')
      : 'Nothing changed.');
    poll();
  } catch (e) {
    toast(e.message, true);
    $('save-btn').disabled = false;
  }
});

$('preview-btn').addEventListener('click', async () => {
  const [model, voice] = $('sel-voice').value.split('|');
  const btn = $('preview-btn');
  btn.disabled = true;
  btn.textContent = 'Loading…';
  try {
    const r = await api('/api/tts-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, voice }),
    });
    const audio = $('preview-audio');
    audio.src = URL.createObjectURL(await r.blob());
    await audio.play();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Listen';
  }
});

async function fixAddress(host, button) {
  button.disabled = true;
  button.textContent = 'Switching over…';
  try {
    const result = await (await api('/api/fix-address', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host }),
    })).json();
    // The device dials its stored OTA address first, so fixing the server side
    // is only half the job whenever that stored value is stale as well.
    toast(`The server now listens on ${host} and is restarting. If the Chatti still `
        + 'does not show up, the old address is still stored on the device.');
    poll();
  } catch (e) {
    toast(e.message, true);
    button.disabled = false;
  }
}

$('reload-catalog').addEventListener('click', loadCatalog);

for (const id of CONTROLS) {
  $(id).addEventListener('input', () => { $('save-btn').disabled = !isDirty(); });
}

/* ---------- go ---------- */

{
  const wanted = location.hash.slice(1);
  showTab(document.querySelector(`.page[data-page="${wanted}"]`) ? wanted : 'start');
}

poll();
loadCatalog();
loadFlash();
loadSetup();
loadConversations();
