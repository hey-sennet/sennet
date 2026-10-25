// The /api/voice socket (docs/voice-protocol.md): JSON controls and messages, binary audio.
// Reconnects by itself; each new connection is a new session, starting at generation 0.
// A phone can leave a backgrounded page's socket looking open, or a connection attempt hanging,
// with nothing getting through: `check` finds out when the page comes back.

import { markUnpaired, UNPAIRED } from '@/lib/pairing'

const CONNECT_TIMEOUT_MS = 5_000 // a connection not open by then is dropped and tried again
const PING_TIMEOUT_MS = 3_000    // a socket that doesn't answer `ping` by then is replaced

export type SpeechStatus = 'ready' | 'loading' | 'unavailable'
export type VoiceState = 'idle' | 'open' | 'listening' | 'transcribing' | 'speaking'
export type SpeechStatuses = { stt: SpeechStatus; tts: SpeechStatus; turn?: SpeechStatus }

export type VoiceMessage =
  | { type: 'ready'; session: string; generation: number; output: { rate: number }; codecs?: string[]; speech: SpeechStatuses }
  | { type: 'speech_status'; speech: SpeechStatuses }
  | { type: 'voice_state'; state: VoiceState; turn: string | null }
  | { type: 'caption'; turn: string; committed: string; provisional: string }
  | { type: 'final'; turn: string; text: string; outcome: string; task_id?: number }
  | { type: 'speech'; reply: number; generation: number; segment: number; text: string; task_id: number | null; cue?: boolean }
  | { type: 'unheard'; task_id: number } // a reply about a task not on screen, not spoken
  | { type: 'speech_end'; reply: number; generation: number }
  | { type: 'stopped'; generation: number }
  | { type: 'barge_in'; generation: number }
  | { type: 'mic'; open: boolean; reason?: 'closed' | 'idle' | 'error' }
  | { type: 'turn'; turn: string; manual?: boolean }
  | { type: 'voice_error'; turn: string | null; code: string; message: string }
  | { type: 'pong' }

type TargetFields = { task_id?: number | null; project_id?: number | null; model?: string | null; effort?: string | null }

export type Control =
  | ({ type: 'start'; turn: string } & TargetFields)
  | { type: 'end' | 'cancel'; turn: string }
  | { type: 'stop' }
  | ({ type: 'open_mic' | 'target' } & TargetFields) // a conversation, and the task on screen (docs/voice-protocol.md §9, §12)
  | { type: 'close_mic' }
  | { type: 'codecs'; uplink: Codec; downlink: Codec } // §3: PCM unless the client asks for Opus
  | { type: 'played' | 'drained'; reply: number; segment: number } // §4: began playing; ran out after it
  | { type: 'audio'; state: string; hidden: boolean }       // §5: playback was suspended or resumed
  | { type: 'ping' }                                         // §4: is this socket still working?

export type Codec = 'pcm_s16le' | 'opus'

type Listener = (message: VoiceMessage) => void

export class VoiceSocket {
  private socket: WebSocket | null = null
  private retry = 0
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setTimeout> | null = null
  private closed = false
  private readonly listeners = new Set<Listener>()
  private readonly audioListeners = new Set<(frame: ArrayBuffer) => void>()
  onOpenChange: (open: boolean) => void = () => {}

  constructor() {
    this.connect()
  }

  get open() {
    return this.socket?.readyState === WebSocket.OPEN
  }

  on(listener: Listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  onAudio(listener: (frame: ArrayBuffer) => void) {
    this.audioListeners.add(listener)
    return () => this.audioListeners.delete(listener)
  }

  send(control: Control): boolean {
    if (!this.open) return false
    this.socket!.send(JSON.stringify(control))
    return true
  }

  sendFrame(frame: Int16Array<ArrayBuffer> | ArrayBuffer) {
    if (this.open) this.socket!.send(frame)
  }

  close() {
    this.closed = true
    this.socket?.close()
  }

  /** The page came back: make sure the socket works, connecting again now if it is gone or silent. */
  check() {
    if (this.closed || this.pingTimer) return
    if (!this.open) return this.reconnect()
    this.send({ type: 'ping' })
    this.pingTimer = setTimeout(() => {
      this.pingTimer = null
      this.reconnect()
    }, PING_TIMEOUT_MS)
  }

  private reconnect() {
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.retry = 0
    const old = this.socket
    this.socket = null
    if (old) {
      const wasOpen = old.readyState === WebSocket.OPEN
      old.onopen = old.onmessage = old.onclose = null
      old.close()
      if (wasOpen) this.onOpenChange(false)
    }
    this.connect()
  }

  private connect() {
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/voice`
    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'
    this.socket = socket
    const opening = setTimeout(() => {
      if (socket.readyState === WebSocket.CONNECTING) socket.close() // its onclose tries again
    }, CONNECT_TIMEOUT_MS)
    socket.onopen = () => {
      clearTimeout(opening)
      this.retry = 0
      this.onOpenChange(true)
    }
    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        for (const listener of this.audioListeners) listener(event.data)
        return
      }
      const message = JSON.parse(event.data) as VoiceMessage
      if (message.type === 'pong' && this.pingTimer) {
        clearTimeout(this.pingTimer)
        this.pingTimer = null
      }
      for (const listener of this.listeners) listener(message)
    }
    socket.onclose = ({ code }) => {
      clearTimeout(opening)
      if (this.pingTimer) clearTimeout(this.pingTimer)
      this.pingTimer = null
      this.onOpenChange(false)
      if (this.closed) return
      if (code === UNPAIRED) return markUnpaired() // pairing reloads the page, and with it this socket
      const delay = Math.min(10_000, 500 * 2 ** this.retry++)
      this.retryTimer = setTimeout(() => this.connect(), delay)
    }
  }
}
