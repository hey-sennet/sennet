// Shared harness for the Strata voice UI.
//
// A page calls start({ draw(frame) }). The harness runs the state machine, a synthetic voice signal (or the
// real microphone), smooth state blending, and the shared chrome: transcript, approval,
// keyboard fallback and task sheet.
//
// URL parameters:  ?state=listening   start in a state
//                  &t=3.2             render one frozen frame at t seconds (screenshots)
//                  &shot              hide the dev toolbar
//                  &panel             open the desktop task panel
//                  &perf              show frame and draw times; window.perfStats() reads them

const STATES = ['idle', 'listening', 'thinking', 'speaking', 'approval', 'typing', 'tasks']
const params = new URLSearchParams(location.search)
const frozenAt = params.has('t') ? parseFloat(params.get('t')) : null
const shot = params.has('shot')
const panelOpen = params.has('panel')
const perf = params.has('perf') ? { draw: [], gap: [] } : null

// --- Deterministic noise and a speech-like signal -------------------------------------

const fract = (x) => x - Math.floor(x)
const hash = (n) => fract(Math.sin(n * 127.1 + 311.7) * 43758.5453)
const smooth = (f) => f * f * (3 - 2 * f)
export function noise(x) {
  const i = Math.floor(x)
  return hash(i) + (hash(i + 1) - hash(i)) * smooth(x - i)
}
const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x))
const smoothstep = (a, b, x) => smooth(clamp((x - a) / (b - a)))

export const BANDS = 64

/** A plausible voice: ~4.5 syllables/s, phrase gaps, formant-weighted bands. */
function synthVoice(t, seed, character) {
  const syllable = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * (character === 'agent' ? 3.6 : 4.4) + 1.7 * Math.sin(t * 1.3 + seed))
  const phrase = smoothstep(0.22, 0.55, noise(t * (character === 'agent' ? 0.9 : 1.4) + seed))
  const grain = 0.7 + 0.3 * noise(t * 11 + seed * 3)
  const level = clamp(phrase * (0.3 + 0.7 * syllable ** 1.4) * grain * 1.1)
  const bands = new Float32Array(BANDS)
  for (let i = 0; i < BANDS; i++) {
    const x = i / BANDS
    // Two formant humps plus a falling tilt.
    const shape = 0.55 * Math.exp(-(((x - 0.12) / 0.09) ** 2)) + 0.4 * Math.exp(-(((x - 0.32) / 0.12) ** 2)) + 0.25 * (1 - x)
    bands[i] = clamp(level * shape * (0.45 + 0.75 * noise(t * 7 + i * 0.41 + seed)) * 1.6)
  }
  return { level, bands }
}

// --- Microphone ------------------------------------------------------------------------

let analyser = null
let micData = null
async function enableMic() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
  const context = new AudioContext()
  analyser = context.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.6
  context.createMediaStreamSource(stream).connect(analyser)
  micData = new Uint8Array(analyser.frequencyBinCount)
}
function micVoice() {
  analyser.getByteFrequencyData(micData)
  const bands = new Float32Array(BANDS)
  let total = 0
  for (let i = 0; i < BANDS; i++) {
    // Log-spaced bins, roughly 80 Hz to 8 kHz.
    const lo = Math.floor(2 * 1.075 ** i), hi = Math.max(lo + 1, Math.floor(2 * 1.075 ** (i + 1)))
    let sum = 0
    for (let b = lo; b < hi && b < micData.length; b++) sum += micData[b]
    bands[i] = clamp(((sum / (hi - lo)) / 255 - 0.25) * 1.6)
    total += bands[i]
  }
  return { level: clamp((total / BANDS) * 2.2), bands }
}

// --- State and scripted content --------------------------------------------------------

const CONTENT = {
  idle: { label: 'Tap to talk', lead: 'Backend tests are green. Two tasks are running.' },
  listening: { label: 'Listening', committed: 'In the backend, run the tests and', provisional: 'fix anything that fails' },
  thinking: { label: 'Working in backend', lead: 'Running the test suite', detail: 'npm test · 12s' },
  speaking: {
    label: 'Speaking',
    words: 'All forty two tests pass now. I fixed the date parser in utils, two files changed.'.split(' '),
  },
  approval: { label: 'Needs you', lead: 'Backend wants to run', command: 'git push origin main', hint: 'or say “approve”' },
  typing: { label: 'Type instead', lead: 'Backend tests are green. Two tasks are running.' },
  tasks: { label: 'Tasks', lead: '' },
}
// Each task's timeline is what the task panel (desktop) and task sheet (phone) show.
const TASKS = [
  { project: 'backend', title: 'Run the tests and fix failures', status: 'working', timeline: [
    { kind: 'you', text: 'In the backend, run the tests and fix anything that fails' },
    { kind: 'command', text: 'npm test', result: 'exit 1 · 3 failed', failed: true },
    { kind: 'files', text: 'src/utils/date.ts, tests/date.test.ts', result: '+12 −4' },
    { kind: 'command', text: 'npm test', result: '42 passed' },
    { kind: 'agent', text: 'All forty two tests pass now. I fixed the date parser in utils, two files changed.' },
  ] },
  { project: 'web', title: 'Tighten the settings dialog spacing', status: 'needs you',
    approval: { lead: 'Web wants to run', command: 'npm run build' }, timeline: [
      { kind: 'you', text: 'In web, tighten the spacing in the settings dialog' },
      { kind: 'files', text: 'src/components/SettingsDialog.tsx', result: '+6 −9' },
    ] },
  { project: 'docs', title: 'Update the install guide', status: 'done', timeline: [
    { kind: 'you', text: 'In docs, update the install guide for the new setup command' },
    { kind: 'files', text: 'docs/install.md', result: '+38 −21' },
    { kind: 'agent', text: 'The install guide now walks through sennet setup. One file changed.' },
  ] },
]
let selectedTask = Math.min(TASKS.length - 1, Math.max(0, +params.get('task') || 0))

let state = STATES.includes(params.get('state')) ? params.get('state') : 'idle'
const mix = Object.fromEntries(STATES.map((s) => [s, s === state ? 1 : 0]))

// --- DOM -------------------------------------------------------------------------------

const html = (strings, ...values) => strings.reduce((out, s, i) => out + s + (values[i] ?? ''), '')

function buildChrome(concept) {
  document.body.insertAdjacentHTML('afterbegin', html`
    <canvas id="field" aria-hidden="true"></canvas>
    <header class="top">
      <div class="mark">sennet<span>.</span></div>
      <button class="project" aria-label="Project"><i></i>backend<svg viewBox="0 0 12 12"><path d="M3 4.5 6 7.5 9 4.5"/></svg></button>
    </header>
    <aside class="rail" aria-label="Tasks">
      <p class="rail-title">Tasks</p>
      ${TASKS.map((task) => html`<div class="task" data-status="${task.status}"><i></i><div><p>${task.title}</p><small>${task.project} · ${task.status}</small></div></div>`).join('')}
    </aside>
    <aside class="panel" aria-label="Task">
      <button class="panel-toggle" aria-label="Show task" aria-expanded="false">
        <svg viewBox="0 0 12 12"><path d="M7.5 3 4.5 6 7.5 9"/></svg>
      </button>
      <div class="panel-body" data-detail></div>
    </aside>
    <main class="stage">
      <button class="orb-slot" aria-label="Tap to talk"></button>
      <div class="words">
        <p class="label"></p>
        <div class="text"></div>
      </div>
    </main>
    <footer class="dock">
      <form class="typebar" onsubmit="return false">
        <button type="button" class="glyph kbd" aria-label="Type instead">
          <svg viewBox="0 0 24 24"><rect x="3" y="6.5" width="18" height="11" rx="2.5"/><path d="M7 10h.01M10 10h.01M13 10h.01M16 10h.01M8 14h8"/></svg>
        </button>
        <input placeholder="Type a request…" aria-label="Type a request" tabindex="-1">
        <button type="button" class="send" aria-label="Send"><svg viewBox="0 0 24 24"><path d="M12 18V6M7 11l5-5 5 5"/></svg></button>
      </form>
      <button class="glyph tasks-glyph" aria-label="Tasks"><span class="count">3</span></button>
    </footer>
    <div class="sheet" aria-label="Tasks">
      <button class="grab" aria-label="Close"></button>
      <div class="sheet-list">
        ${TASKS.map((task) => html`<div class="task" data-status="${task.status}"><i></i><div><p>${task.title}</p><small>${task.project} · ${task.status}</small></div></div>`).join('')}
      </div>
      <div class="sheet-detail">
        <button class="back"><svg viewBox="0 0 12 12"><path d="M7.5 3 4.5 6 7.5 9"/></svg>Tasks</button>
        <div data-detail></div>
      </div>
    </div>
  `)
  if (!shot) {
    document.body.insertAdjacentHTML('beforeend', html`
      <nav class="devbar">${STATES.map((s) => `<button data-state="${s}">${s}</button>`).join('')}<button data-mic>mic</button></nav>`)
    document.querySelectorAll('.devbar [data-state]').forEach((b) => b.addEventListener('click', () => setState(b.dataset.state)))
    document.querySelector('.devbar [data-mic]').addEventListener('click', async (e) => {
      await enableMic()
      e.target.classList.add('on')
    })
  }
  document.querySelector('.orb-slot').addEventListener('click', () =>
    setState(state === 'listening' ? 'thinking' : state === 'thinking' ? 'speaking' : state === 'speaking' ? 'idle' : 'listening'))
  document.querySelector('.kbd').addEventListener('click', () => setState(state === 'typing' ? 'idle' : 'typing'))
  document.querySelector('.tasks-glyph').addEventListener('click', () => setState(state === 'tasks' ? 'idle' : 'tasks'))
  // Phone: the task sheet lists tasks, and opens one in place.
  const openSheetTask = (open) => document.body.classList.toggle('task-open', open)
  document.querySelector('.sheet .grab').addEventListener('click', () => { openSheetTask(false); setState('idle') })
  document.querySelector('.sheet .back').addEventListener('click', () => openSheetTask(false))
  document.querySelectorAll('.sheet-list .task').forEach((task, i) => task.addEventListener('click', () => {
    selectTask(i)
    openSheetTask(true)
  }))
  openSheetTask(params.has('task'))
  // The desktop task panel: tucked away at the right edge until you open it or pick a task.
  const toggle = document.querySelector('.panel-toggle')
  const openPanel = (open) => {
    document.body.classList.toggle('panel-open', open)
    toggle.setAttribute('aria-expanded', String(open))
    toggle.setAttribute('aria-label', open ? 'Hide task' : 'Show task')
  }
  toggle.addEventListener('click', () => openPanel(!document.body.classList.contains('panel-open')))
  document.querySelectorAll('.rail .task').forEach((task, i) => task.addEventListener('click', () => {
    selectTask(i)
    openPanel(true)
  }))
  // Open from the URL without the slide, so frozen frames draw the disc where it settles.
  document.body.style.transition = 'none'
  openPanel(panelOpen)
  document.body.offsetWidth
  document.body.style.transition = ''
  selectTask(selectedTask)
  renderWords()
  document.body.dataset.state = state
}

/** A task's approval, at the end of its own timeline. */
function approvalHtml(task) {
  return html`<div class="ask">
    <p class="ask-label">Needs you</p>
    <p class="ask-lead">${task.approval.lead}</p>
    <span class="ask-cmd">${task.approval.command}</span>
    <div class="ask-choices"><button class="approve">Approve</button><button class="decline">Decline</button></div>
  </div>`
}

function detailHtml(i) {
  const task = TASKS[i]
  return html`
    <p class="panel-meta">${task.project} · codex</p>
    <h2>${task.title}</h2>
    <p class="panel-status" data-status="${task.status}"><i></i>${task.status}</p>
    <ol class="timeline">
      ${task.timeline.map((item) => html`<li class="${item.kind}${item.failed ? ' failed' : ''}">${
        item.kind === 'command' ? html`<span class="mono">› ${item.text}</span><small>${item.result}</small>`
        : item.kind === 'files' ? html`<small>edited</small><span class="mono">${item.text}</span><small>${item.result}</small>`
        : item.text}</li>`).join('')}
      ${task.approval ? html`<li class="here">${approvalHtml(task)}</li>` : ''}
    </ol>`
}

function selectTask(i) {
  selectedTask = i
  document.querySelectorAll('.rail .task').forEach((t, j) => t.classList.toggle('on', j === i))
  document.querySelectorAll('[data-detail]').forEach((host) => { host.innerHTML = detailHtml(i) })
}

function renderWords() {
  const c = CONTENT[state]
  document.querySelector('.label').textContent = c.label
  const text = document.querySelector('.text')
  const words = (s, cls = '') => s.split(' ').map((w, i) => `<span class="w ${cls}" style="--i:${i}">${w}</span>`).join(' ')
  if (state === 'listening') {
    text.innerHTML = `<p class="said">${words(c.committed)} ${words(c.provisional, 'prov')}</p>`
  } else if (state === 'speaking') {
    text.innerHTML = `<p class="spoken">${c.words.map((w, i) => `<span class="w" data-i="${i}" style="--i:${i}">${w}</span>`).join(' ')}</p>`
  } else if (state === 'thinking') {
    text.innerHTML = `<p class="lead">${words(c.lead)}<span class="dots"><i>.</i><i>.</i><i>.</i></span></p><p class="detail">${c.detail}</p>`
  } else if (state === 'approval') {
    text.innerHTML = `<p class="lead">${words(c.lead)}</p><p class="cmd"><code>${c.command}</code></p>
      <div class="choices"><button class="approve">Approve</button><button class="decline">Decline</button></div>
      <p class="hint">${c.hint}</p>`
  } else if (c.lead) {
    text.innerHTML = `<p class="lead quiet">${words(c.lead)}</p>`
  } else {
    text.innerHTML = ''
  }
}

// Exposed for captures that film state changes.
window.setState = (next) => setState(next)

function setState(next) {
  if (next === state) return
  state = next
  document.body.dataset.state = state
  const words = document.querySelector('.words')
  words.classList.add('swap')
  setTimeout(() => {
    renderWords()
    words.classList.remove('swap')
  }, shot ? 0 : 180)
}

// --- Render loop -----------------------------------------------------------------------

export function start(concept) {
  buildChrome(concept)
  const canvas = document.getElementById('field')
  const ctx = canvas.getContext('2d')
  const slot = document.querySelector('.orb-slot')
  const level = { user: 0, agent: 0 }
  const bands = new Float32Array(BANDS)
  // Smoothed signal over the last few seconds, for concepts that draw voice history.
  const history = []
  const HISTORY_S = 4

  function update(t, dt) {
    for (const s of STATES) mix[s] += ((s === state ? 1 : 0) - mix[s]) * clamp(dt * 4.5)
    const userSignal = analyser && state === 'listening' ? micVoice() : synthVoice(t, 0, 'user')
    const agentSignal = synthVoice(t, 17, 'agent')
    const userOn = mix.listening, agentOn = mix.speaking
    const target = userSignal.level * userOn + agentSignal.level * agentOn
    // Lightly averaged so single consonants do not spike; pages ease the result further.
    // Rates are per second; attack is quicker than release so speech lands.
    level.user += (target - level.user) * clamp(dt * (target > level.user ? 14 : 6))
    for (let i = 0; i < BANDS; i++) {
      const b = userSignal.bands[i] * userOn + agentSignal.bands[i] * agentOn
      bands[i] += (b - bands[i]) * clamp(dt * (b > bands[i] ? 12 : 5))
    }
    history.push({ t, level: level.user, bands: bands.slice() })
    while (history.length && history[0].t < t - HISTORY_S) history.shift()
  }

  // Run the smoothing for a few seconds before t, so a frozen frame looks like live playback.
  function warm(t) {
    history.length = 0
    for (let tw = t - 3; tw < t; tw += 1 / 60) update(tw, 1 / 60)
  }

  // Index and blend weight of the moment `ago` seconds before the latest update.
  function locate(ago) {
    const target = history[history.length - 1].t - ago
    let lo = 0, hi = history.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (history[mid].t < target) lo = mid + 1
      else hi = mid
    }
    if (lo === 0) return [0, 0]
    const a = history[lo - 1], b = history[lo]
    return [lo, clamp((target - a.t) / (b.t - a.t || 1))]
  }

  /** The smoothed signal `ago` seconds before the latest update, interpolated between samples. */
  function past(ago) {
    if (!history.length) return { level: level.user, bands }
    const [i, f] = locate(ago)
    if (i === 0) return history[0]
    const a = history[i - 1], b = history[i]
    const mixed = new Float32Array(BANDS)
    for (let j = 0; j < BANDS; j++) mixed[j] = a.bands[j] + (b.bands[j] - a.bands[j]) * f
    return { level: a.level + (b.level - a.level) * f, bands: mixed }
  }

  /** Level only; cheap enough to call per point. */
  past.level = (ago) => {
    if (!history.length) return level.user
    const [i, f] = locate(ago)
    if (i === 0) return history[0].level
    return history[i - 1].level + (history[i].level - history[i - 1].level) * f
  }

  function step(t, dt) {
    update(t, dt)
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const w = Math.round(innerWidth * dpr), h = Math.round(innerHeight * dpr)
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w
      canvas.height = h
    }
    const rect = slot.getBoundingClientRect()
    // Follow the slot's animated size (it shrinks while typing).
    const R = (Math.min(rect.width, rect.height) / 2) * dpr
    const cx = (rect.left + rect.width / 2) * dpr, cy = (rect.top + rect.height / 2) * dpr
    const styles = getComputedStyle(document.body)
    const drawStart = performance.now()
    concept.draw({
      ctx, w, h, dpr, cx, cy, R, t, dt, state, mix, level: level.user, bands, noise, past,
      color: (name) => styles.getPropertyValue(`--${name}`).trim(),
    })
    perf?.draw.push(performance.now() - drawStart)

    if (state === 'speaking') {
      // Highlight words as they are "spoken": about 2.6 words per second.
      const spoken = Math.floor((t * 2.6) % (CONTENT.speaking.words.length + 4))
      document.querySelectorAll('.spoken .w').forEach((el) => el.classList.toggle('on', +el.dataset.i < spoken))
    }
  }

  let last = performance.now()
  const began = last
  function frame(now) {
    perf?.gap.push(now - last)
    step((now - began) / 1000, Math.min(0.05, (now - last) / 1000))
    last = now
    requestAnimationFrame(frame)
  }

  // For captures: render exact moments. renderAt continues smoothly from the previous call.
  let rendered = null
  window.renderAt = (t) => {
    if (rendered === null || t < rendered) warm(t)
    step(t, rendered === null || t < rendered ? 1 / 60 : t - rendered)
    rendered = t
  }

  if (perf) showPerf()
  document.fonts.ready.then(() => {
    if (frozenAt === null) return requestAnimationFrame(frame)
    window.renderAt(frozenAt)
    document.body.dataset.ready = '1'
  })
}

// --- Performance readout ---------------------------------------------------------------

const quantile = (values, q) => {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]
}

function summarise(gap, draw) {
  return {
    frames: gap.length,
    fps: gap.length ? 1000 / (gap.reduce((a, b) => a + b, 0) / gap.length) : 0,
    gapP95: quantile(gap, 0.95),
    drawP50: quantile(draw, 0.5),
    drawP95: quantile(draw, 0.95),
  }
}

/** Stats since the last call. Draw time is the JS that builds a frame; the frame gap also
 *  includes rasterising and compositing it. */
window.perfStats = () => {
  const stats = summarise(perf.gap, perf.draw)
  perf.draw.length = perf.gap.length = 0
  return stats
}

function showPerf() {
  const readout = document.createElement('output')
  readout.className = 'perf'
  document.body.append(readout)
  setInterval(() => {
    const s = summarise(perf.gap.slice(-120), perf.draw.slice(-120))
    readout.textContent = `${s.fps.toFixed(0)} fps · frame p95 ${s.gapP95.toFixed(1)} ms · draw p50 ${s.drawP50.toFixed(1)} p95 ${s.drawP95.toFixed(1)} ms`
  }, 1000)
}

// --- Small drawing helpers shared by concepts ------------------------------------------

export function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
export function blend(colors, mix) {
  // colors: { state: '#rrggbb' } with a 'base' fallback; returns an rgb() triple blended by state weights.
  let r = 0, g = 0, b = 0, total = 0
  for (const s of STATES) {
    const weight = mix[s]
    if (weight < 0.001) continue
    const [cr, cg, cb] = hexToRgb(colors[s] ?? colors.base)
    r += cr * weight; g += cg * weight; b += cb * weight; total += weight
  }
  return [r / total, g / total, b / total]
}
export const rgba = ([r, g, b], a = 1) => `rgba(${r | 0},${g | 0},${b | 0},${a})`
export { clamp, smoothstep }
