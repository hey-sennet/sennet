// Strata's drawing: horizontal lines through a circle, lifted by the voice like a ridgeline
// plot. Ported from design/strata.html, which is the reference for how it moves and why;
// design/README.md explains the motion and the performance settings.
import { bell, clamp, fract, noise, rgba, smoothstep, type Rgb } from './math'

export const STATES = ['idle', 'listening', 'thinking', 'speaking', 'approval'] as const
export type StrataState = (typeof STATES)[number]
/** How much each state shows, easing between 0 and 1 as the state changes. */
export type Mix = Record<StrataState, number>

export const BANDS = 64
/** Loudness and frequency bands, each 0 to 1. */
export interface Signal {
  level: number
  bands: Float32Array
}

/** Cream is your voice, glacier anything the agent does (thinking, speaking), amber needs you. */
export interface Palette {
  background: string
  base: Rgb
  agent: Rgb
  approval: Rgb
}

export interface FieldFrame {
  ctx: CanvasRenderingContext2D
  w: number
  h: number
  dpr: number
  t: number
  dt: number
  mix: Mix
  signal: Signal
  palette: Palette
  /** Only the slow breathing: no swell, ripple, wobble or drifting light. */
  reducedMotion: boolean
}

const LINES = 30
const STROKE = 1.3
const FADE = 0.65 // each line fades to nothing over this fraction of R before the circle
const HEIGHT = 0.4 // peak lift at full voice, as a fraction of R
const MARGIN = 0.92 // the disc's radius as a fraction of half the canvas, leaving room for the swell
// Points along each line: one every SPACING device pixels, so a line keeps the same detail at
// any size, from a short one near the poles to the middle of a large disc on a laptop. The
// path curves through the midpoints between them, so no corners show between points either.
// Capped so a huge window can't run away with the frame (design/README.md, Performance).
const SPACING = 4
const MIN_POINTS = 12
const MAX_POINTS = 240
const STOPS = 12 // gradient stops per line

// The swell reacts quickly but never jumps. Loudness drives a critically damped spring, so
// each change eases in and out (no corners, no overshoot) while still landing within a
// fraction of a second. Width follows the voice's brightness more slowly.
const SPRING = 15 // stiffness in rad/s: higher reacts faster, lower is calmer
const TRAVEL = 0.22 // seconds for the swell to roll out from its centre to the rim

// Opacity varies across the disc from a drifting noise field, so soft patches of light move
// through the surface. The voice drives the drift: patches creep at rest and surge as it speaks.
const SHADE = 0.35 // how far the dimmest patches fall, as a fraction of full opacity
const SHADE_REST = 0.6 // drift speed with no voice
const SHADE_VOICE = 14 // extra drift speed per unit of eased loudness

// The delayed lift depends only on distance from the centre, so each frame samples it into a
// table once instead of searching the history at every point.
const RINGS = 64

function brightnessOf(bands: Float32Array) {
  let weighted = 0, total = 0
  for (let i = 0; i < 40; i++) {
    weighted += bands[i] * i
    total += bands[i]
  }
  return total ? weighted / total / 40 : 0.3
}

/** `clock` runs faster while the voice is loud. Two crossed, noise-warped sine waves make
 *  patches that always span light and dark; the field fades out towards the rim. */
function shadeAt(xr: number, yr: number, clock: number) {
  const field = 0.5 + 0.5
    * Math.sin(xr * 5.4 + 2.5 * noise(yr * 2.4 + clock * 0.08 + 3.3) + clock * 0.21)
    * Math.sin(yr * 6.1 + 2.5 * noise(xr * 2.4 - clock * 0.06 + 11.7) - clock * 0.17)
  const centre = 1 - smoothstep(0.6, 0.95, Math.sqrt(xr * xr + yr * yr))
  return 1 - SHADE * centre * smoothstep(0.9, 0.1, field)
}

/** Thinking: rings spread from the centre like a drop in still water, fading as they widen. */
function rippleAt(r: number, t: number) {
  let a = 0
  for (const offset of [0, 0.5]) {
    const ring = fract(t / 2.6 + offset)
    a += bell(r - ring, 0.08) * (1 - ring) ** 1.5 * smoothstep(0, 0.15, ring)
  }
  return 0.07 * a
}

function blend(palette: Palette, mix: Mix): Rgb {
  const weights: [Rgb, number][] = [
    [palette.base, mix.idle + mix.listening],
    [palette.agent, mix.thinking + mix.speaking],
    [palette.approval, mix.approval],
  ]
  let r = 0, g = 0, b = 0, total = 0
  for (const [[cr, cg, cb], weight] of weights) {
    r += cr * weight
    g += cg * weight
    b += cb * weight
    total += weight
  }
  return total ? [r / total, g / total, b / total] : palette.base
}

/** A line through `points + 1` heights, curving through the midpoints between them. */
function trace(ctx: CanvasRenderingContext2D, heights: Float32Array, points: number, x0: number, dx: number) {
  ctx.moveTo(x0, heights[0])
  for (let s = 1; s < points; s++) {
    ctx.quadraticCurveTo(x0 + s * dx, heights[s], x0 + (s + 0.5) * dx, (heights[s] + heights[s + 1]) / 2)
  }
  ctx.lineTo(x0 + points * dx, heights[points])
}

/** One Strata disc's drawing and the eased state it carries from frame to frame. */
export function createField() {
  let lift: number | null = null
  let velocity = 0
  let brightness = 0.3
  let shade = 0
  const history: { t: number; lift: number }[] = []
  const ringLift = new Float32Array(RINGS + 1)
  const heights = new Float32Array(MAX_POINTS + 1) // one line's points, reused

  /** The eased lift `ago` seconds back, so outer parts of the surface follow a moment later. */
  function liftAgo(ago: number) {
    const target = history[history.length - 1].t - ago
    let lo = 0, hi = history.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (history[mid].t < target) lo = mid + 1
      else hi = mid
    }
    if (lo === 0) return history[0].lift
    const a = history[lo - 1], b = history[lo]
    return a.lift + (b.lift - a.lift) * clamp((target - a.t) / (b.t - a.t || 1))
  }

  function evolve(t: number, dt: number, signal: Signal, reducedMotion: boolean) {
    if (lift === null) lift = signal.level
    // Sub-step so large frame gaps stay stable.
    const steps = Math.ceil(dt / (1 / 120))
    for (let i = 0; i < steps; i++) {
      const h = dt / steps
      velocity += (SPRING ** 2 * (signal.level - lift) - 2 * SPRING * velocity) * h
      lift += velocity * h
    }
    lift = Math.max(0, lift)
    brightness += (brightnessOf(signal.bands) - brightness) * clamp(dt * 2)
    history.push({ t, lift })
    while (history.length > 2 && history[0].t < t - TRAVEL * 1.5) history.shift()
    if (!reducedMotion) shade += dt * (SHADE_REST + SHADE_VOICE * lift)
    for (let i = 0; i <= RINGS; i++) ringLift[i] = liftAgo(TRAVEL * smoothstep(0, 1, i / RINGS))
  }

  /** The eased lift at distance r (a fraction of R), interpolated from the table. */
  function liftAt(r: number) {
    const p = Math.min(r, 1) * RINGS
    const i = Math.min(RINGS - 1, p | 0)
    return ringLift[i] + (ringLift[i + 1] - ringLift[i]) * (p - i)
  }

  return function draw({ ctx, w, h, dpr, t, dt, mix, signal, palette, reducedMotion }: FieldFrame) {
    evolve(t, dt, signal, reducedMotion)
    const level = lift ?? 0
    const cx = w / 2, cy = h / 2
    const R = (Math.min(w, h) / 2) * MARGIN
    ctx.clearRect(0, 0, w, h)
    const ink = blend(palette, mix)
    const live = reducedMotion ? 0 : mix.listening + mix.speaking
    const strength = 0.5 + 0.5 * Math.min(1, level * 2 * live + 0.4)
    const fadeLength = FADE * R
    // At rest the swell breathes: a slow, low rise and fall about every six seconds. Under
    // reduced motion it is the only movement, in every state.
    const breath = 0.035 * (0.55 + 0.45 * Math.sin(t * 2 * Math.PI / 6.2)) * (reducedMotion ? 1 : mix.idle)
    const thinking = !reducedMotion && mix.thinking > 0.001
    const approval = reducedMotion ? 0 : mix.approval
    const driftClock = reducedMotion ? 0 : t
    const shapeWidth = 0.28 + brightness * 0.7
    ctx.lineWidth = STROKE * dpr
    ctx.lineJoin = 'round'
    for (let k = 0; k < LINES; k++) {
      const y = cy - R + ((k + 0.5) / LINES) * 2 * R
      const yr = (y - cy) / R
      const half = Math.sqrt(Math.max(0, R * R - (y - cy) ** 2))
      if (half < 2) continue
      const lineShape = bell(yr, 0.6)
      const points = Math.min(MAX_POINTS, Math.max(MIN_POINTS, Math.ceil((2 * half) / SPACING)))
      for (let s = 0; s <= points; s++) {
        const xr = ((s / points) * 2 * half - half) / R
        const r = Math.sqrt(xr * xr + yr * yr)
        // The voice reaches the centre of the mound first and rolls outward from there.
        const swell = HEIGHT * liftAt(r) * live + breath
        // The ripple is only computed while thinking shows. Approval adds a faint wobble per line.
        const amp = R * (swell * bell(xr, shapeWidth) * lineShape
          + (thinking ? mix.thinking * rippleAt(r, t) : 0)
          + 0.03 * approval * (0.5 + 0.5 * Math.sin(t * 4 + k)) * bell(xr, 0.6))
        // A faint, slow undulation keeps the lines from ever looking ruled.
        const drift = R * 0.009 * (noise(xr * 1.8 + driftClock * 0.23 + k * 0.27) - 0.5) * 2
        heights[s] = y - Math.max(0, amp) - drift
      }
      const x0 = cx - half, dx = (2 * half) / points
      // Fill under each line with the background so nearer lines occlude farther ones.
      ctx.beginPath()
      trace(ctx, heights, points, x0, dx)
      ctx.lineTo(cx + half, y + R * 0.08)
      ctx.lineTo(x0, y + R * 0.08)
      ctx.closePath()
      ctx.fillStyle = palette.background
      ctx.fill()

      ctx.beginPath()
      trace(ctx, heights, points, x0, dx)
      // Opacity eases to zero where the line meets the circle. The fade length is absolute
      // (a fraction of R), so short lines near the poles never reach full strength, which
      // gives the disc a soft spherical falloff. Along the way the shade field dims patches.
      const gradient = ctx.createLinearGradient(cx - half, 0, cx + half, 0)
      const span = Math.min(0.5, fadeLength / (2 * half))
      for (let i = 0; i <= STOPS; i++) {
        const p = i / STOPS
        const xr = (p * 2 - 1) * half / R
        const edge = smoothstep(0, 1, Math.min(p, 1 - p) / span) * Math.min(1, half / fadeLength)
        gradient.addColorStop(p, rgba(ink, strength * edge * shadeAt(xr, yr, shade)))
      }
      ctx.strokeStyle = gradient
      ctx.stroke()
    }
  }
}
