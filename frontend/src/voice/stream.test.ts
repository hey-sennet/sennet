import { describe, expect, it } from 'vitest'
import { LEAD_S, SpeechStream } from './stream'

const RATE = 1000 // samples a second, so the lead is 100 samples
const BLOCK = 10

/** Renders blocks until `frames` have passed, returning everything played and every event. */
function run(stream: SpeechStream, from: number, frames: number) {
  const played: number[] = []
  const events = []
  for (let frame = from; frame < from + frames; frame += BLOCK) {
    const out = new Float32Array(BLOCK)
    stream.render(out, frame)
    played.push(...out)
    events.push(...stream.take())
  }
  return { played, events }
}

const ones = (n: number) => new Float32Array(n).fill(1)

describe('SpeechStream', () => {
  it('waits for the lead, then plays chunks back to back with no gap', () => {
    const stream = new SpeechStream(RATE)
    stream.push(ones(60), 1)
    expect(run(stream, 0, 20).played.every((s) => s === 0)).toBe(true) // 60 buffered: waiting
    stream.push(ones(60), 2)
    const { played, events } = run(stream, 20, 120)
    const start = played.findIndex((s) => s !== 0)
    expect(start).toBe(0)
    // After the fade-in, every one of the 120 samples is played, with no break at the join.
    expect(played.slice(64, 120).every((s) => s === 1)).toBe(true)
    expect(events.filter((e) => e.type === 'started').map((e) => 'mark' in e && e.mark)).toEqual([1, 2])
    expect(events).toContainEqual({ type: 'drained', consumed: 120 })
  })

  it('plays a short reply once nothing more has come for the lead', () => {
    const stream = new SpeechStream(RATE)
    stream.push(ones(30))
    const { played } = run(stream, 0, LEAD_S * RATE + 50)
    expect(played.slice(0, LEAD_S * RATE).every((s) => s === 0)).toBe(true)
    expect(played.some((s) => s > 0)).toBe(true)
  })

  it('fades out to silence when stopped, instead of dropping to zero', () => {
    const stream = new SpeechStream(RATE)
    stream.push(ones(500))
    run(stream, 0, 200)
    stream.clear()
    const { played, events } = run(stream, 200, 100)
    expect(played[0]).toBeGreaterThan(0.5)
    expect(played[0]).toBeLessThan(1)
    expect(Math.abs(played[99])).toBeLessThan(0.001)
    expect(events).toEqual([])
  })

  it('raises the lead after running dry mid-reply', () => {
    const stream = new SpeechStream(RATE)
    stream.push(ones(100))
    run(stream, 0, 200)
    stream.push(ones(100)) // arrives soon after it ran dry
    expect(stream.take()).toEqual([{ type: 'underrun', lead: 0.15 }])
    // The next run waits for the new lead: 100 samples isn't enough to start straight away.
    expect(run(stream, 200, 50).played.every((s) => s < 0.001)).toBe(true)
  })

  it('holds the next run for a cue', () => {
    const stream = new SpeechStream(RATE)
    stream.hold(300)
    stream.push(ones(200))
    expect(run(stream, 0, 300).played.every((s) => s === 0)).toBe(true)
    expect(run(stream, 300, 100).played.some((s) => s > 0)).toBe(true)
  })
})
