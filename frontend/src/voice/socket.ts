// The /api/voice socket (docs/voice-protocol.md): JSON controls and messages, binary audio.
// Reconnects by itself; each new connection is a new session, starting at generation 0.

export type SpeechStatus = 'ready' | 'loading' | 'unavailable'
export type VoiceState = 'idle' | 'listening' | 'transcribing' | 'speaking'

export type VoiceMessage =
  | { type: 'ready'; session: string; generation: number; output: { rate: number }
      speech: { stt: SpeechStatus; tts: SpeechStatus } }
  | { type: 'speech_status'; speech: { stt: SpeechStatus; tts: SpeechStatus } }
  | { type: 'voice_state'; state: VoiceState; turn: string | null }
  | { type: 'caption'; turn: string; committed: string; provisional: string }
  | { type: 'final'; turn: string; text: string; outcome: string; task_id?: number; approval_id?: number;
      decision?: string | null; confirm?: boolean }
  | { type: 'speech'; generation: number; segment: number; text: string; task_id: number | null; approval_id: number | null }
  | { type: 'asking'; approval_id: number }
  | { type: 'speech_end'; generation: number }
  | { type: 'stopped'; generation: number }
  | { type: 'voice_error'; turn: string | null; code: string; message: string }

export type Control =
  | { type: 'start'; turn: string; task_id?: number | null; project_id?: number | null }
  | { type: 'end' | 'cancel'; turn: string }
  | { type: 'stop' }

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

  sendFrame(frame: Int16Array<ArrayBuffer>) {
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
    socket.onclose = () => {
      this.onOpenChange(false)
      if (this.closed) return
      const delay = Math.min(10_000, 500 * 2 ** this.retry++)
      setTimeout(() => this.connect(), delay)
    }
  }
}
