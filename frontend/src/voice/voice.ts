// Push-to-talk, outside React: opens the microphone on the first press, streams 20 ms frames
// to /api/voice while a turn is open, and releases the microphone after a quiet minute.
// Replies play through `playback`, dropping any generation older than the floor (§7).
// React subscribes to the snapshot; Strata reads `source` and `playback.source` each frame.
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { MicError, Microphone } from './mic'
import { Playback, parseFrame } from './playback'
import { VoiceSocket, type SpeechStatus, type VoiceMessage, type VoiceState } from './socket'

const RELEASE_MIC_AFTER_MS = 60_000
const WORDS_LINGER_MS = 8_000 // the last words stay this long once the session is quiet
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }

export type Target = { task_id: number } | { project_id: number } | Record<string, never>

/** The words under the disc: yours while you talk, the agent's as it speaks them. */
export interface Words {
  speaker: 'user' | 'agent'
  committed: string
  provisional: string
}

export interface VoiceSnapshot {
  connected: boolean
  stt: SpeechStatus | null       // null until the server says
  listening: boolean             // a turn is open on this device
  opening: boolean               // the microphone is starting: listening, but nothing captured yet
  speaking: boolean              // a reply is playing on this device
  state: VoiceState              // the server's voice_state for this session
  words: Words | null
}

export class Voice {
  readonly socket = new VoiceSocket()
  readonly playback = new Playback()
  private mic: Microphone | null = null
  private opening: Promise<Microphone> | null = null
  private releaseTimer: ReturnType<typeof setTimeout> | null = null
  private turn: string | null = null
  private ending: string | null = null // released, its last frame still on the way
  private turns = 0
  private outputRate = 24000
  private latest = 0 // the highest playback generation seen
  private floor = 0  // frames below this generation are stale
  // Each reply's segment texts, in arrival order; frames belong to the latest `speech` message.
  private replies = 0
  private readonly replyText = new Map<number, string[]>()
  private heard: { reply: number; segment: number } | null = null
  private wordsTimer: ReturnType<typeof setTimeout> | null = null
  private snapshot: VoiceSnapshot = {
    connected: false, stt: null, listening: false, opening: false, speaking: false, state: 'idle', words: null,
  }
  private readonly subscribers = new Set<() => void>()
  private readonly errorSubscribers = new Set<(message: string) => void>()

  constructor() {
    this.socket.onOpenChange = (connected) => {
      // A closed socket ends its session: the rest of the reply isn't spoken (§6).
      if (!connected) this.playback.silence()
      this.update({ connected, ...(connected ? {} : { stt: null, state: 'idle' }) })
    }
    this.socket.on((message) => this.receive(message))
    this.socket.onAudio((data) => this.hear(data))
    this.playback.onPlayingChange = (speaking) => this.update({ speaking })
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
    this.playback.unlock() // still inside the press's gesture, so replies can play later
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
    this.interrupt()
    this.turn = turn
    this.holdMic()
    this.update({ listening: true, words: { speaker: 'user', committed: '', provisional: '' } })
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

  /** Stop speaking. Agent work carries on. */
  stop() {
    this.socket.send({ type: 'stop' })
    this.interrupt()
  }

  /** Silence playback now, without waiting for the server's `stopped`: it applies the same +1. */
  private interrupt() {
    this.floor = this.latest + 1
    this.playback.silence()
    this.replyText.clear()
    this.heard = null
    const words = this.snapshot.words
    if (words?.speaker === 'agent' && words.provisional) this.update({ words: { ...words, provisional: '' } })
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
    if (message.type === 'ready') {
      // A new session: generations start again from its own.
      this.latest = this.floor = message.generation
      this.outputRate = message.output.rate
    }
    if (message.type === 'ready' || message.type === 'speech_status') this.update({ stt: message.speech.stt })
    if (message.type === 'speech' || message.type === 'speech_end' || message.type === 'stopped') {
      this.latest = Math.max(this.latest, message.generation)
    }
    if (message.type === 'stopped') this.floor = Math.max(this.floor, message.generation)
    if (message.type === 'voice_state') this.update({ state: message.state })
    if (message.type === 'caption' && message.turn === this.turn) {
      this.update({ words: { speaker: 'user', committed: message.committed, provisional: message.provisional } })
    }
    if (message.type === 'final') {
      this.update({ words: message.text ? { speaker: 'user', committed: message.text, provisional: '' } : null })
    }
    if (message.type === 'speech' && message.generation >= this.floor) {
      if (message.segment === 0) this.replyText.set(++this.replies, [])
      this.replyText.get(this.replies)?.push(message.text)
      this.heard = { reply: this.replies, segment: message.segment }
    }
    if (message.type === 'voice_error') {
      if (message.turn && message.turn === this.turn) this.endTurn()
      this.fail(message.message)
    }
  }

  private hear(data: ArrayBuffer) {
    const frame = parseFrame(data)
    if (!frame) return
    this.latest = Math.max(this.latest, frame.generation)
    if (frame.generation < this.floor) return
    // The first frame of a segment shows its words as it starts playing.
    const heard = this.heard
    const first = heard && heard.segment === frame.segment ? heard : null
    if (first) this.heard = null
    this.playback.play(frame.samples, this.outputRate, first ? () => this.spoken(first.reply, first.segment) : undefined)
  }

  /** A segment started playing: its reply so far is said, the segments queued behind it are not yet. */
  private spoken(reply: number, segment: number) {
    for (const old of this.replyText.keys()) if (old < reply) this.replyText.delete(old)
    const segments = this.replyText.get(reply) ?? []
    this.update({ words: { speaker: 'agent', committed: segments.slice(0, segment + 1).join(' '),
      provisional: segments.slice(segment + 1).join(' ') } })
  }

  private fail(message: string) {
    for (const listener of this.errorSubscribers) listener(message)
  }

  private update(change: Partial<VoiceSnapshot>) {
    this.snapshot = { ...this.snapshot, ...change }
    const { listening, speaking, state, words } = this.snapshot
    const quiet = !listening && !speaking && state === 'idle'
    if (quiet && words && !this.wordsTimer) {
      this.wordsTimer = setTimeout(() => {
        this.wordsTimer = null
        this.update({ words: null })
      }, WORDS_LINGER_MS)
    } else if (!quiet && this.wordsTimer) {
      clearTimeout(this.wordsTimer)
      this.wordsTimer = null
    }
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
