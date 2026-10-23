import { describe, expect, it } from 'vitest'
import { Resampler } from './resample'

function tone(hz: number, rate: number, seconds: number) {
  return Float32Array.from({ length: Math.round(rate * seconds) }, (_, i) => 0.5 * Math.sin(2 * Math.PI * hz * i / rate))
}

/** Feeds `input` in chunks of `size` and joins what comes out. */
function chunked(resampler: Resampler, input: Float32Array, size: number) {
  const out: number[] = []
  for (let i = 0; i < input.length; i += size) out.push(...resampler.push(input.slice(i, i + size)))
  return Float32Array.from(out)
}

describe('Resampler', () => {
  it('passes audio through when the rates match', () => {
    const input = tone(440, 48000, 0.01)
    expect(new Resampler(48000, 48000).push(input)).toBe(input)
  })

  it('gives the same stream whatever the chunk size, so chunk joins are seamless', () => {
    const input = tone(440, 24000, 0.5)
    const whole = new Resampler(24000, 48000).push(input)
    const pieces = chunked(new Resampler(24000, 48000), input, 480) // 20 ms
    expect(pieces.length).toBe(whole.length)
    for (let i = 0; i < whole.length; i++) expect(pieces[i]).toBeCloseTo(whole[i], 6)
  })

  it.each([[24000, 48000], [24000, 44100], [48000, 44100]])('keeps a tone clean from %i to %i Hz', (from, to) => {
    const out = chunked(new Resampler(from, to), tone(440, from, 0.5), 2400)
    expect(Math.abs(out.length - to * 0.5)).toBeLessThan(40) // the filter holds back 16 input samples
    // Compare with the ideal tone at the new rate, after the filter's delay of 16 input samples.
    const delay = 16 * to / from
    let worst = 0
    for (let i = 200; i < out.length - 200; i++) {
      worst = Math.max(worst, Math.abs(out[i] - 0.5 * Math.sin(2 * Math.PI * 440 * (i - delay) / to)))
    }
    expect(worst).toBeLessThan(0.01)
  })
})
