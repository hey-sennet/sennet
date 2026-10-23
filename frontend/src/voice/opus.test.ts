import { describe, expect, it } from 'vitest'
import { HEADER_BYTES, OPUS, Quiet, UPLINK_FRAME_MS, VERSION, parseOpusFrame, uplinkFrame } from './opus'

const LAG = (16000 * UPLINK_FRAME_MS) / 1000 + 320

describe('uplinkFrame', () => {
  it('writes the header, then the packet', () => {
    const frame = new DataView(uplinkFrame(3, 70000, new Uint8Array([9, 8, 7])))
    expect([frame.getUint8(0), frame.getUint8(1), frame.getUint16(2, true), frame.getUint32(4, true)])
      .toEqual([VERSION, OPUS, 3, 70000])
    expect(frame.byteLength).toBe(HEADER_BYTES + 3)
    expect(frame.getUint8(HEADER_BYTES)).toBe(9)
  })

  it('is only the header when it says how far a silence runs', () => {
    expect(uplinkFrame(1, 5).byteLength).toBe(HEADER_BYTES)
  })
})

describe('parseOpusFrame', () => {
  function frame(segment: number, generation: number, packets: number[][]) {
    const bytes = [VERSION, OPUS, segment, 0, generation, 0, 0, 0]
    for (const packet of packets) bytes.push(packet.length, 0, ...packet)
    return new Uint8Array(bytes).buffer
  }

  it('reads the packets after the header', () => {
    const parsed = parseOpusFrame(frame(2, 5, [[1, 2], [3]]))!
    expect([parsed.segment, parsed.generation]).toEqual([2, 5])
    expect(parsed.packets.map((p) => [...p])).toEqual([[1, 2], [3]])
  })

  it('leaves PCM frames and truncated ones alone', () => {
    expect(parseOpusFrame(new Uint8Array([VERSION, 1, 0, 0, 0, 0, 0, 0, 1, 2]).buffer)).toBeNull()
    expect(parseOpusFrame(frame(0, 0, [[1, 2, 3]]).slice(0, 11))).toBeNull()
  })
})

describe('Quiet', () => {
  it('says nothing while packets keep coming', () => {
    const quiet = new Quiet()
    for (let fed = 960, now = 0; fed < 16000; fed += 960, now += 60) {
      quiet.packet(fed - 960, now)
      expect(quiet.due(fed, now, true)).toBeNull()
    }
  })

  it('says how far a silence runs, often in a turn and rarely outside one', () => {
    const quiet = new Quiet()
    quiet.packet(960, 0)
    const sent = (inTurn: boolean) => {
      const positions: number[] = []
      for (let now = 60; now <= 3000; now += 60) {
        const position = quiet.due(960 + (now / 1000) * 16000, now, inTurn)
        if (position !== null) positions.push(position)
      }
      return positions
    }
    const inTurn = sent(true)
    expect(inTurn.length).toBeGreaterThan(20)
    expect(inTurn.every((p, i) => i === 0 || p > inTurn[i - 1])).toBe(true)
    expect(inTurn.at(-1)).toBe(960 + 48000 - LAG)
    const idle = new Quiet()
    idle.packet(960, 0)
    let count = 0
    for (let now = 60; now <= 3000; now += 60) if (idle.due(960 + (now / 1000) * 16000, now, false) !== null) count++
    expect(count).toBeLessThanOrEqual(3)
  })
})
