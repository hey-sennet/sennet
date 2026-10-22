import { describe, expect, it } from 'vitest'
import { BANDS } from '@/strata/field'
import { micSignal } from './bands'
import { Downsampler, FRAME_SAMPLES, Framer, TARGET_RATE } from './downsample'

function tone(hz: number, rate: number, seconds: number, amplitude = 0.5) {
  const out = new Float32Array(Math.round(rate * seconds))
  for (let i = 0; i < out.length; i++) out[i] = amplitude * Math.sin((2 * Math.PI * hz * i) / rate)
  return out
}

/** Push in 128-sample blocks, as the audio thread does. */
function downsample(input: Float32Array, rate: number) {
  const down = new Downsampler(rate)
  const parts: Float32Array[] = []
  for (let i = 0; i < input.length; i += 128) parts.push(down.push(input.subarray(i, i + 128)))
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const p of parts) {
    out.set(p, at)
    at += p.length
  }
  return out
}

/** RMS of the steady middle, past the filter's start-up. */
function rms(samples: Float32Array) {
  const middle = samples.subarray(Math.floor(samples.length * 0.2), Math.floor(samples.length * 0.8))
  return Math.sqrt(middle.reduce((s, x) => s + x * x, 0) / middle.length)
}

/** Strength of one frequency in a 16 kHz signal (a single DFT bin). */
function strength(samples: Float32Array, hz: number) {
  let re = 0, im = 0
  for (let i = 0; i < samples.length; i++) {
    re += samples[i] * Math.cos((2 * Math.PI * hz * i) / TARGET_RATE)
    im -= samples[i] * Math.sin((2 * Math.PI * hz * i) / TARGET_RATE)
  }
  return (2 * Math.hypot(re, im)) / samples.length
}

describe('Downsampler', () => {
  it.each([48000, 44100, 96000])('gives 16 kHz from %i Hz', (rate) => {
    const out = downsample(tone(440, rate, 1), rate)
    expect(Math.abs(out.length - TARGET_RATE)).toBeLessThanOrEqual(1)
  })

  it.each([48000, 44100])('keeps speech frequencies at %i Hz', (rate) => {
    for (const hz of [200, 1000, 3000, 6000]) {
      const out = downsample(tone(hz, rate, 1), rate)
      expect(rms(out)).toBeCloseTo(0.5 / Math.SQRT2, 1)
      expect(strength(out, hz)).toBeGreaterThan(0.45)
    }
  })

  it('removes what would alias', () => {
    // 10 kHz can't exist at 16 kHz; unfiltered it would fold down to 6 kHz.
    const out = downsample(tone(10000, 48000, 1), 48000)
    expect(rms(out)).toBeLessThan(0.01)
    expect(strength(out, 6000)).toBeLessThan(0.01)
  })

  it('passes 16 kHz input through', () => {
    const input = tone(440, 16000, 0.1)
    expect(Array.from(downsample(input, 16000))).toEqual(Array.from(input))
  })
})

describe('Framer', () => {
  it('cuts 20 ms PCM16 frames and flushes a short last one', () => {
    const frames: Int16Array[] = []
    const framer = new Framer((f) => frames.push(f))
    framer.push(new Float32Array(700).fill(0.5))
    expect(frames.map((f) => f.length)).toEqual([FRAME_SAMPLES, FRAME_SAMPLES])
    framer.flush()
    expect(frames.map((f) => f.length)).toEqual([FRAME_SAMPLES, FRAME_SAMPLES, 60])
    expect(frames[0][0]).toBe(16383) // 0.5 × 32767, truncated as Int16Array does
  })

  it('clips instead of wrapping', () => {
    const frames: Int16Array[] = []
    const framer = new Framer((f) => frames.push(f))
    framer.push(Float32Array.of(2, -2))
    framer.flush()
    expect(Array.from(frames[0])).toEqual([32767, -32768])
  })
})

describe('micSignal', () => {
  it('is silent for silence and loud for a loud spectrum', () => {
    const bands = new Float32Array(BANDS)
    expect(micSignal(new Uint8Array(512), bands).level).toBe(0)
    const loud = micSignal(new Uint8Array(512).fill(255), bands)
    expect(loud.level).toBe(1)
    expect(Math.min(...loud.bands)).toBeCloseTo(1, 5)
  })

  it('puts low frequencies in low bands', () => {
    const spectrum = new Uint8Array(512)
    spectrum.fill(255, 2, 6) // about 90 to 280 Hz at 48 kHz
    const { bands } = micSignal(spectrum, new Float32Array(BANDS))
    expect(bands[0]).toBeGreaterThan(0.5)
    expect(bands[BANDS - 1]).toBe(0)
  })
})
