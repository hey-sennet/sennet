// Runs a Strata disc on a canvas, outside React: its own animation frame loop, state blending
// and audio smoothing. React only tells it the current state and where the audio comes from.
import { BANDS, STATES, createField, type Mix, type Palette, type Signal, type StrataState } from './field'
import { clamp, hexToRgb } from './math'

/** Something with a voice to read each frame, such as the microphone or speech playback. */
export interface AudioSource {
  read(t: number): Signal
}

/** Your voice drives listening, the agent's drives speaking. */
export interface StrataSources {
  user?: AudioSource
  agent?: AudioSource
}

export interface StrataController {
  setState(state: StrataState): void
  setSources(sources: StrataSources): void
  destroy(): void
}

const SILENCE: Signal = { level: 0, bands: new Float32Array(BANDS) }

function readPalette(canvas: HTMLCanvasElement): Palette {
  const styles = getComputedStyle(canvas)
  const token = (name: string) => styles.getPropertyValue(`--${name}`).trim()
  return {
    background: token('background') || '#100f0d',
    base: hexToRgb(token('ink'), [239, 232, 218]),
    agent: hexToRgb(token('agent'), [147, 219, 224]),
    approval: hexToRgb(token('warn'), [233, 169, 91]),
  }
}

/**
 * Draws Strata on `canvas`, sized to the element. With `frozenAt` it renders that one moment
 * (as if it had been running since t = 0) and sets `data-ready`, for screenshots and tests;
 * otherwise it animates, pausing while the page is hidden. `data-running` says which.
 */
export function createStrata(canvas: HTMLCanvasElement, options: { state: StrataState; frozenAt?: number }): StrataController {
  const ctx = canvas.getContext('2d')!
  const palette = readPalette(canvas)
  const motion = matchMedia('(prefers-reduced-motion: reduce)')
  let draw = createField()
  let state = options.state
  let sources: StrataSources = {}
  const mix = {} as Mix
  const signal: Signal = { level: 0, bands: new Float32Array(BANDS) }

  function reset() {
    draw = createField()
    for (const s of STATES) mix[s] = s === state ? 1 : 0
    signal.level = 0
    signal.bands.fill(0)
  }
  reset()

  function update(t: number, dt: number) {
    for (const s of STATES) mix[s] += ((s === state ? 1 : 0) - mix[s]) * clamp(dt * 4.5)
    const user = mix.listening > 0.001 ? sources.user?.read(t) ?? SILENCE : SILENCE
    const agent = mix.speaking > 0.001 ? sources.agent?.read(t) ?? SILENCE : SILENCE
    // Lightly averaged so single consonants do not spike; the field eases the result further.
    // Rates are per second; attack is quicker than release so speech lands.
    const target = user.level * mix.listening + agent.level * mix.speaking
    signal.level += (target - signal.level) * clamp(dt * (target > signal.level ? 14 : 6))
    for (let i = 0; i < BANDS; i++) {
      const b = user.bands[i] * mix.listening + agent.bands[i] * mix.speaking
      signal.bands[i] += (b - signal.bands[i]) * clamp(dt * (b > signal.bands[i] ? 12 : 5))
    }
  }

  function render(t: number, dt: number) {
    const dpr = Math.min(2, window.devicePixelRatio || 1)
    const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr)
    if (!w || !h) return
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w
      canvas.height = h
    }
    draw({ ctx, w, h, dpr, t, dt, mix, signal, palette, reducedMotion: motion.matches })
  }

  /** Replay the last three seconds of smoothing before drawing t, so it looks like playback. */
  function renderFrozen(t: number) {
    reset()
    for (let tw = t - 3; tw < t; tw += 1 / 60) update(tw, 1 / 60)
    update(t, 1 / 60)
    render(t, 1 / 60)
    canvas.dataset.ready = 'true'
  }

  let handle = 0
  let clock = 0
  let last = 0
  const frame = (now: number) => {
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    clock += dt
    update(clock, dt)
    render(clock, dt)
    handle = requestAnimationFrame(frame)
  }
  const run = (on: boolean) => {
    cancelAnimationFrame(handle)
    canvas.dataset.running = String(on)
    if (!on) return
    last = performance.now()
    handle = requestAnimationFrame(frame)
  }
  const onVisibility = () => run(document.visibilityState === 'visible')

  const frozen = options.frozenAt
  const resize = new ResizeObserver(() => {
    if (frozen !== undefined) renderFrozen(frozen)
  })
  resize.observe(canvas)
  if (frozen === undefined) {
    document.addEventListener('visibilitychange', onVisibility)
    onVisibility()
  }

  return {
    setState(next) {
      state = next
      if (frozen !== undefined) renderFrozen(frozen)
    },
    setSources(next) {
      sources = next
      if (frozen !== undefined) renderFrozen(frozen)
    },
    destroy() {
      run(false)
      resize.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
    },
  }
}
