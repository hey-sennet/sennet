// Push-to-talk, outside React: opens the microphone on the first press, streams 20 ms frames
// to /api/voice while a turn is open, and releases the microphone after a quiet minute.
// A conversation keeps it open and streaming, and the server opens and ends turns (§12).
// Replies play through `playback`, dropping any generation older than the floor (§7).
// React subscribes to the snapshot; Strata reads `source` and `playback.source` each frame.
import type { ModelChoice } from '@/lib/api'
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { MicError, Microphone } from './mic'
import chime from './approval-chime.mp3'
import { Playback, parseFrame } from './playback'
import { VoiceSocket, type SpeechStatus, type VoiceMessage, type VoiceState } from './socket'

const RELEASE_MIC_AFTER_MS = 60_000
const WORDS_LINGER_MS = 8_000 // the last words stay this long once the session is quiet
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }
const CHIME_S = 1.4 // approval-chime.mp3

/** The selected task, or the project a new task starts in, on its own model if chosen. */
export type Target = { task_id: number } | ({ project_id: number } & Partial<ModelChoice>) | Record<string, never>

/** The words under the disc: yours while you talk, the agent's as it speaks them. */
export interface Words {
  speaker: 'user' | 'agent'
  committed: string
  provisional: string
}

export interface VoiceSnapshot {
  connected: boolean
  stt: SpeechStatus | null       // null until the server says
  turnReady: boolean             // end-of-turn detection: conversations can open
  conversation: boolean          // the microphone is open and the server finds the turns
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
    connected: false, stt: null, turnReady: false, conversation: false, listening: false, opening: false,
    speaking: false, state: 'idle', words: null,
  }
  private wakeLock: WakeLockSentinel | null = null
  private readonly subscribers = new Set<() => void>()
  private readonly errorSubscribers = new Set<(message: string) => void>()

  constructor() {
    this.socket.onOpenChange = (connected) => {
      // A closed socket ends its session: the rest of the reply isn't spoken (§6).
      if (!connected) {
        this.playback.silence()
        if (this.snapshot.conversation) this.endConversation() // a new session starts without one
      }
      this.update({ connected, ...(connected ? {} : { stt: null, state: 'idle' }) })
    }
    this.socket.on((message) => this.receive(message))
    this.socket.onAudio((data) => this.hear(data))
    this.playback.onPlayingChange = (speaking) => this.update({ speaking })
    // Any first gesture unlocks audio, so the approval chime plays before the first push-to-talk.
    for (const type of ['pointerdown', 'keydown']) {
      document.addEventListener(type, () => this.playback.unlock(), { once: true, capture: true })
    }
    // Phones suspend a hidden page's microphone anyway: end the conversation cleanly instead.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.snapshot.conversation) this.closeConversation()
    })
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

  /**
   * Open a conversation: the microphone stays live and the server decides where turns start
   * and end. Must be called inside a gesture, for the microphone and playback.
   */
  async openConversation(target: Target) {
    if (this.snapshot.conversation) return
    this.playback.unlock()
    if (this.snapshot.stt !== 'ready' || !this.snapshot.turnReady) {
      this.fail(this.snapshot.connected ? 'Conversations need speech recognition and turn detection. Run `sennet setup`.'
        : 'Voice is not connected yet.')
      return
    }
    this.holdMic()
    try {
      await this.openMic()
    } catch (error) {
      this.releaseMicLater()
      this.fail(error instanceof MicError ? error.message : String(error))
      return
    }
    if (!this.socket.send({ type: 'open_mic', ...target })) {
      this.releaseMicLater()
      this.fail('Voice is not connected yet.')
      return
    }
    this.update({ conversation: true })
    void this.keepAwake()
  }

  closeConversation() {
    if (!this.snapshot.conversation) return
    this.socket.send({ type: 'close_mic' })
    this.endConversation()
  }

  /** The selection changed: the next turn of an open conversation goes there. */
  retarget(target: Target) {
    if (this.snapshot.conversation) this.socket.send({ type: 'target', ...target })
  }

  /** Start a turn. Talking interrupts any speech (the server stops it on `start`). */
  async press(target: Target) {
    if (this.snapshot.conversation) {
      // Holds the turn being heard, or opens one, until release; the server says which (§12).
      this.playback.unlock()
      const turn = this.turn ?? `t${++this.turns}`
      this.socket.send({ type: 'start', turn, ...target })
      this.interrupt()
      this.turn = turn
      this.update({ listening: true, ...(this.snapshot.words?.speaker === 'user' ? {} : { words: { speaker: 'user', committed: '', provisional: '' } }) })
      return
    }
    if (this.turn) return
    this.playback.unlock() // still inside the press's gesture, so replies can play later
    if (this.snapshot.stt !== 'ready') {
      this.fail(this.snapshot.connected ? 'Speech recognition is still loading, or not set up. Run `sennet setup`.'
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

  /**
   * Silence playback now, without waiting for the server's `stopped`: it applies the same +1.
   * `floor` is given when the server chose the generation itself (a barge-in).
   */
  private interrupt(floor = this.latest + 1) {
    this.floor = floor
    this.playback.silence()
    this.replyText.clear()
    this.heard = null
    const words = this.snapshot.words
    if (words?.speaker === 'agent' && words.provisional) this.update({ words: { ...words, provisional: '' } })
  }

  private endTurn() {
    this.turn = null
    this.update({ listening: false })
    if (!this.snapshot.conversation) this.releaseMicLater()
  }

  private endConversation() {
    this.turn = null
    this.update({ conversation: false, listening: false })
    this.releaseMicLater()
    void this.wakeLock?.release().catch(() => {})
    this.wakeLock = null
  }

  private releaseMicLater() {
    this.holdMic()
    this.releaseTimer = setTimeout(() => this.closeMic(), RELEASE_MIC_AFTER_MS)
  }

  /** Keep the screen on while a conversation is open, where the browser allows it. */
  private async keepAwake() {
    try {
      this.wakeLock = await navigator.wakeLock?.request('screen') ?? null
    } catch {
      this.wakeLock = null // refused (battery saver, an older browser): the conversation still works
    }
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
      if (this.turn || this.ending || this.snapshot.conversation) this.socket.sendFrame(frame)
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
    if (message.type === 'ready' || message.type === 'speech_status') {
      this.update({ stt: message.speech.stt, turnReady: message.speech.turn === 'ready' })
    }
    if (message.type === 'mic' && !message.open && this.snapshot.conversation) {
      this.endConversation()
      // Closed by the server, not by you: say so, or the microphone just seems to stop.
      if (message.reason === 'idle') this.fail('The conversation ended after five quiet minutes.')
      if (message.reason === 'error') this.fail('The conversation stopped listening because of an error. Start it again.')
    }
    if (message.type === 'turn') {
      // A turn the server opened, or the one a press is holding: its captions are this device's.
      this.turn = message.turn
      if (!this.snapshot.listening) this.update({ listening: true, words: { speaker: 'user', committed: '', provisional: '' } })
    }
    if (message.type === 'barge_in') {
      // Talking over the reply stopped it. The server has already moved to this generation, and
      // the reply that follows comes in it: it is the floor, not one below it.
      this.latest = Math.max(this.latest, message.generation)
      this.interrupt(Math.max(this.floor, message.generation))
    }
    if (message.type === 'speech' || message.type === 'speech_end' || message.type === 'stopped') {
      this.latest = Math.max(this.latest, message.generation)
    }
    if (message.type === 'stopped') this.floor = Math.max(this.floor, message.generation)
    if (message.type === 'voice_state') this.update({ state: message.state })
    if (message.type === 'caption' && message.turn === this.turn) {
      this.update({ words: { speaker: 'user', committed: message.committed, provisional: message.provisional } })
    }
    if (message.type === 'final') {
      this.update({ words: message.text && message.outcome !== 'interrupted' && message.outcome !== 'empty'
        ? { speaker: 'user', committed: message.text, provisional: '' } : null })
      if (message.turn === this.turn) this.endTurn() // a turn the server opened and ended
    }
    if (message.type === 'speech' && message.generation >= this.floor) {
      // A reply nobody asked for (the agent's report on work it left running) chimes first.
      if (message.cue) this.playback.cue(chime, { before: CHIME_S })
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
    const quiet = !listening && !speaking && (state === 'idle' || state === 'open')
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
