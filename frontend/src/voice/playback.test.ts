import { describe, expect, it } from 'vitest'
import { parseFrame } from './playback'

function frame(version: number, kind: number, segment: number, generation: number, samples: number[]) {
  const data = new DataView(new ArrayBuffer(8 + samples.length * 2))
  data.setUint8(0, version)
  data.setUint8(1, kind)
  data.setUint16(2, segment, true)
  data.setUint32(4, generation, true)
  samples.forEach((s, i) => data.setInt16(8 + i * 2, s, true))
  return data.buffer
}

describe('parseFrame', () => {
  it('reads the header and PCM16 audio', () => {
    const parsed = parseFrame(frame(1, 1, 3, 70000, [0, 16384, -32768, 32767]))!
    expect(parsed.segment).toBe(3)
    expect(parsed.generation).toBe(70000)
    expect(Array.from(parsed.samples)).toEqual([0, 0.5, -1, 32767 / 32768])
  })

  it('drops frames it does not know', () => {
    expect(parseFrame(frame(2, 1, 0, 0, [1]))).toBeNull()
    expect(parseFrame(frame(1, 2, 0, 0, [1]))).toBeNull()
    expect(parseFrame(new ArrayBuffer(4))).toBeNull()
  })
})
