import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { Voice as VoiceClass } from './voice'

// Voice opens its socket on construction; a stand-in WebSocket lets the test speak for the server.
class FakeSocket {
  static OPEN = 1
  static last: FakeSocket
  readyState = 1
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor() {
    FakeSocket.last = this
  }
  send(data: unknown) {
    this.sent.push(data)
  }
  close() {}
}

let Voice: typeof VoiceClass

beforeAll(async () => {
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('location', { protocol: 'http:', host: 'sennet.test' })
  vi.stubGlobal('document', { addEventListener() {}, hidden: false })
  ;({ Voice } = await import('./voice'))
})

function server(message: object) {
  FakeSocket.last.onmessage!({ data: JSON.stringify(message) })
}

function audio(generation: number, segment = 0) {
  const data = new DataView(new ArrayBuffer(8 + 4))
  data.setUint8(0, 1)
  data.setUint8(1, 1)
  data.setUint16(2, segment, true)
  data.setUint32(4, generation, true)
  FakeSocket.last.onmessage!({ data: data.buffer })
}

function voiceWithPlayback() {
  const voice = new Voice()
  const played = vi.fn()
  voice.playback.play = played
  server({ type: 'ready', session: 's', generation: 0, output: { rate: 24000 }, speech: { stt: 'ready', tts: 'ready', turn: 'ready' } })
  return { voice, played }
}

describe('playback generations', () => {
  it('drops the reply a barge-in stopped, and plays the next one', () => {
    const { played } = voiceWithPlayback()
    server({ type: 'speech', generation: 0, segment: 0, text: 'A long reply.', task_id: 1 })
    audio(0)
    expect(played).toHaveBeenCalledTimes(1)

    server({ type: 'barge_in', generation: 1 })
    audio(0) // still in flight from the stopped reply
    expect(played).toHaveBeenCalledTimes(1)

    server({ type: 'speech', generation: 1, segment: 0, text: 'The answer.', task_id: 1 })
    audio(1)
    expect(played).toHaveBeenCalledTimes(2)
  })

  it('after stop, plays the generation the server confirms', () => {
    const { voice, played } = voiceWithPlayback()
    voice.stop()
    server({ type: 'stopped', generation: 1 })
    audio(0)
    server({ type: 'speech', generation: 1, segment: 0, text: 'Next.', task_id: 1 })
    audio(1)
    expect(played).toHaveBeenCalledTimes(1)
  })
})

describe('words under the disc', () => {
  it('clear once an open conversation is quiet, so an approval or progress can show', () => {
    vi.useFakeTimers()
    try {
      const { voice } = voiceWithPlayback()
      server({ type: 'turn', turn: 's1' })
      server({ type: 'final', turn: 's1', text: 'run the tests', outcome: 'dispatched', task_id: 1 })
      server({ type: 'voice_state', state: 'open', turn: null })
      expect(voice.getSnapshot().words?.committed).toBe('run the tests')
      vi.advanceTimersByTime(8_000)
      expect(voice.getSnapshot().words).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })
})


describe('leaving a task', () => {
  function playing(taskId: number) {
    const { voice, played } = voiceWithPlayback()
    server({ type: 'speech', reply: 1, generation: 0, segment: 0, text: 'A long reply.', task_id: taskId })
    audio(0)
    played.mock.calls[0][2]() // its first audio starts playing
    voice.playback.onPlayingChange(true)
    return voice
  }
  const stops = () => FakeSocket.last.sent.filter((m) => typeof m === 'string' && JSON.parse(m).type === 'stop').length

  it('silences what was already sent about the task left', () => {
    playing(1).leave(null)
    expect(stops()).toBe(1)
  })

  it('lets a reply about the task being opened play on', () => {
    playing(2).leave(2)
    expect(stops()).toBe(0)
  })
})
