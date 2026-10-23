// The /api/voice socket (docs/voice-protocol.md): JSON controls and messages, binary audio.
// Reconnects by itself; each new connection is a new session, starting at generation 0.

import { markUnpaired, UNPAIRED } from '@/lib/pairing'

export type SpeechStatus = 'ready' | 'loading' | 'unavailable'
export type VoiceState = 'idle' | 'open' | 'listening' | 'transcribing' | 'speaking'
export type SpeechStatuses = { stt: SpeechStatus; tts: SpeechStatus; turn?: SpeechStatus }

export type VoiceMessage =
  | { type: 'ready'; session: string; generation: number; output: { rate: number }; codecs?: string[]; speech: SpeechStatuses }
  | { type: 'speech_status'; speech: SpeechStatuses }
  | { type: 'voice_state'; state: VoiceState; turn: string | null }
  | { type: 'caption'; turn: string; committed: string; provisional: string }
  | { type: 'final'; turn: string; text: string; outcome: string; task_id?: number; approval_id?: number;
      decision?: string | null; confirm?: boolean }
  | { type: 'speech'; generation: number; segment: number; text: string; task_id: number | null; approval_id: number | null
      cue?: boolean }
  | { type: 'asking'; approval_id: number }
  | { type: 'speech_end'; generation: number }
  | { type: 'stopped'; generation: number }
  | { type: 'barge_in'; generation: number }
  | { type: 'mic'; open: boolean; reason?: 'closed' | 'idle' | 'error' }
  | { type: 'turn'; turn: string; manual?: boolean }
  | { type: 'voice_error'; turn: string | null; code: string; message: string }

type TargetFields = { task_id?: number | null; project_id?: number | null; model?: string | null; effort?: string | null }

export type Control =
  | ({ type: 'start'; turn: string } & TargetFields)
  | { type: 'end' | 'cancel'; turn: string }
  | { type: 'stop' }
  | ({ type: 'open_mic' | 'target' } & TargetFields) // a conversation (docs/voice-protocol.md §12)
  | { type: 'close_mic' }
  | { type: 'codecs'; uplink: Codec; downlink: Codec } // §3: PCM unless the client asks for Opus

export type Codec = 'pcm_s16le' | 'opus'

type Listener = (message: VoiceMessage) => void

export class VoiceSocket {
  private socket: WebSocket | null = null
  private retry = 0
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

  private connect() {
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/voice`
    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'
    this.socket = socket
    socket.onopen = () => {
      this.retry = 0
      this.onOpenChange(true)
    }
    socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        for (const listener of this.audioListeners) listener(event.data)
        return
      }
      const message = JSON.parse(event.data) as VoiceMessage
      for (const listener of this.listeners) listener(message)
    }
    socket.onclose = ({ code }) => {
      this.onOpenChange(false)
      if (this.closed) return
      if (code === UNPAIRED) return markUnpaired() // pairing reloads the page, and with it this socket
      const delay = Math.min(10_000, 500 * 2 ** this.retry++)
      setTimeout(() => this.connect(), delay)
    }
  }
}
