// Loudness and frequency bands from the microphone, for Strata's user source. The same
// maths as micVoice() in design/harness.js, which the motion was tuned against.
import { BANDS, type Signal } from '@/strata/field'
import { clamp } from '@/strata/math'

export const FFT_SIZE = 1024
export const SMOOTHING = 0.6

/** `spectrum` is an AnalyserNode's byte frequency data; `bands` is filled in place. */
export function micSignal(spectrum: Uint8Array, bands: Float32Array): Signal {
  let total = 0
  for (let i = 0; i < BANDS; i++) {
    // Log-spaced bins, roughly 80 Hz to 8 kHz at 48 kHz.
    const lo = Math.floor(2 * 1.075 ** i)
    const hi = Math.max(lo + 1, Math.floor(2 * 1.075 ** (i + 1)))
    let sum = 0
    for (let b = lo; b < hi && b < spectrum.length; b++) sum += spectrum[b]
    bands[i] = clamp(((sum / (hi - lo)) / 255 - 0.25) * 1.6)
    total += bands[i]
  }
  return { level: clamp((total / BANDS) * 2.2), bands }
}
