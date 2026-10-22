// Small helpers for Strata's drawing: deterministic 1D noise, easing and colour.

export const fract = (x: number) => x - Math.floor(x)
const hash = (n: number) => fract(Math.sin(n * 127.1 + 311.7) * 43758.5453)
const smooth = (f: number) => f * f * (3 - 2 * f)

/** Smooth value noise in [0, 1], the same for the same input on every run. */
export function noise(x: number) {
  const i = Math.floor(x)
  return hash(i) + (hash(i + 1) - hash(i)) * smooth(x - i)
}

export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x))
export const smoothstep = (a: number, b: number, x: number) => smooth(clamp((x - a) / (b - a)))
export const bell = (x: number, width: number) => Math.exp(-((x / width) ** 2))

export type Rgb = [number, number, number]

export function hexToRgb(hex: string, fallback: Rgb): Rgb {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return fallback
  const n = parseInt(match[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

export const rgba = ([r, g, b]: Rgb, a = 1) => `rgba(${r | 0},${g | 0},${b | 0},${a})`
