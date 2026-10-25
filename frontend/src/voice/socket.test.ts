import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceSocket } from './socket'

// A stand-in WebSocket: each one starts connecting, and the test opens, answers or closes it.
class FakeSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static all: FakeSocket[] = []
  readyState = FakeSocket.CONNECTING
  binaryType = ''
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor() {
    FakeSocket.all.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  open() {
    this.readyState = FakeSocket.OPEN
    this.onopen?.()
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
  close() {
    if (this.readyState === FakeSocket.CLOSED) return
    this.readyState = FakeSocket.CLOSED
    this.onclose?.({ code: 1006 })
  }
}

const sockets = () => FakeSocket.all

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.stubGlobal('location', { protocol: 'http:', host: 'sennet.test' })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the voice socket', () => {
  it('drops a connection that never opens, and tries again', () => {
    new VoiceSocket()
    vi.advanceTimersByTime(5_000)
    expect(sockets()[0].readyState).toBe(FakeSocket.CLOSED)
    vi.advanceTimersByTime(500)
    expect(sockets()).toHaveLength(2)
  })

  it('keeps a socket that answers when the page comes back', () => {
    const voice = new VoiceSocket()
    sockets()[0].open()
    voice.check()
    expect(sockets()[0].sent).toEqual([JSON.stringify({ type: 'ping' })])
    sockets()[0].receive({ type: 'pong' })
    vi.advanceTimersByTime(10_000)
    expect(sockets()).toHaveLength(1)
    expect(voice.open).toBe(true)
  })

  it('replaces a socket that looks open but doesn\'t answer', () => {
    const voice = new VoiceSocket()
    const changes: boolean[] = []
    voice.onOpenChange = (open) => changes.push(open)
    sockets()[0].open()
    voice.check()
    vi.advanceTimersByTime(3_000)
    expect(sockets()).toHaveLength(2)
    expect(sockets()[0].readyState).toBe(FakeSocket.CLOSED)
    sockets()[1].open()
    expect(changes).toEqual([true, false, true])
  })

  it('connects at once when the page comes back to a closed one, without waiting out the backoff', () => {
    const voice = new VoiceSocket()
    for (let i = 0; i < 4; i++) {
      sockets().at(-1)!.close()
      vi.runOnlyPendingTimers()
    }
    sockets().at(-1)!.close() // the next try is 8 s away
    const before = sockets().length
    voice.check()
    expect(sockets()).toHaveLength(before + 1)
  })
})
