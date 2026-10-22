// Push-to-talk, outside React: opens the microphone on the first press, streams 20 ms frames
// to /api/voice while a turn is open, and releases the microphone after a quiet minute.
// React subscribes to the snapshot; Strata reads `source` each frame.
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { MicError, Microphone } from './mic'
import { VoiceSocket, type SpeechStatus, type VoiceMessage } from './socket'

const RELEASE_MIC_AFTER_MS = 60_000
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }

export type Target = { task_id: number } | { project_id: number } | Record<string, never>

export interface VoiceSnapshot {
  connected: boolean
  stt: SpeechStatus | null       // null until the server says
  listening: boolean             // a turn is open on this device
  opening: boolean               // the microphone is starting: listening, but nothing captured yet
}

export class Voice {
  readonly socket = new VoiceSocket()
  private mic: Microphone | null = null
  private opening: Promise<Microphone> | null = null
  private releaseTimer: ReturnType<typeof setTimeout> | null = null
  private turn: string | null = null
  private ending: string | null = null // released, its last frame still on the way
  private turns = 0
  private snapshot: VoiceSnapshot = { connected: false, stt: null, listening: false, opening: false }
  private readonly subscribers = new Set<() => void>()
  private readonly errorSubscribers = new Set<(message: string) => void>()

  constructor() {
    this.socket.onOpenChange = (connected) => this.update({ connected, ...(connected ? {} : { stt: null }) })
    this.socket.on((message) => this.receive(message))
  }

  /** Strata's user source: the microphone's loudness and bands, silent while it is closed. */
  readonly source: AudioSource = {
    read: (t) => this.mic?.source.read(t) ?? SILENCE,
  }

  subscribe = (onChange: () => void) => {
    this.subscribers.add(onChange)
    return () => this.subscribers.delete(onChange)
  }

  getSnapshot = () => this.snapshot

  onError(listener: (message: string) => void) {
    this.errorSubscribers.add(listener)
    return () => this.errorSubscribers.delete(listener)
  }

  /** Start a turn. Talking interrupts any speech (the server stops it on `start`). */
  async press(target: Target) {
    if (this.turn) return
    if (this.snapshot.stt !== 'ready') {
      this.fail(this.snapshot.connected ? 'Speech recognition is still loading, or not set up. Run `agent-talk setup`.'
        : 'Voice is not connected yet.')
      return
    }
    const turn = `t${++this.turns}`
    if (!this.socket.send({ type: 'start', turn, ...target })) {
      this.fail('Voice is not connected yet.')
      return
    }
    this.turn = turn
    this.holdMic()
    this.update({ listening: true })
    try {
      await this.openMic()
    } catch (error) {
      this.socket.send({ type: 'cancel', turn })
      this.endTurn()
      this.fail(error instanceof MicError ? error.message : String(error))
    }
  }

  /** End the turn: the server runs the final pass. */
  release() {
    if (!this.turn) return
    const turn = this.turn
    this.ending = turn
    this.endTurn()
    const flushed = this.mic ? this.mic.flush() : Promise.resolve()
    void flushed.then(() => {
      if (this.ending === turn) this.ending = null
      this.socket.send({ type: 'end', turn })
    })
  }

  /** Drop the turn without a final pass, e.g. the pointer slid off the button. */
  cancel() {
    if (!this.turn) return
    this.socket.send({ type: 'cancel', turn: this.turn })
    this.endTurn()
  }

  private endTurn() {
    this.turn = null
    this.update({ listening: false })
    this.releaseTimer = setTimeout(() => this.closeMic(), RELEASE_MIC_AFTER_MS)
  }

  private holdMic() {
    if (this.releaseTimer) clearTimeout(this.releaseTimer)
    this.releaseTimer = null
  }

  private async openMic() {
    if (this.mic) return this.mic
    this.update({ opening: true })
    this.opening ??= Microphone.open((frame) => {
      // The first frame is the moment capture really started: from here, speech is heard.
      if (this.snapshot.opening) this.update({ opening: false })
      if (this.turn || this.ending) this.socket.sendFrame(frame)
    })
    try {
      this.mic = await this.opening
      return this.mic
    } catch (error) {
      this.update({ opening: false })
      throw error
    } finally {
      this.opening = null
    }
  }

  private async closeMic() {
    const mic = this.mic
    this.mic = null
    await mic?.close()
  }

  private receive(message: VoiceMessage) {
    if (message.type === 'ready' || message.type === 'speech_status') this.update({ stt: message.speech.stt })
    if (message.type === 'voice_error') {
      if (message.turn && message.turn === this.turn) this.endTurn()
      this.fail(message.message)
    }
  }

  private fail(message: string) {
    for (const listener of this.errorSubscribers) listener(message)
  }

  private update(change: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...change }
    for (const onChange of this.subscribers) onChange()
  }
}

let shared: Voice | null = null

/** The page's one voice connection, created on first use. */
export function voice(): Voice {
  shared ??= new Voice()
  return shared
}

export type { VoiceMessage }
