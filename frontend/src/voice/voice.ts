// Push-to-talk, outside React: opens the microphone on the first press, streams 20 ms frames
// to /api/voice while a turn is open, and releases the microphone after a quiet minute. Audio
// goes as Opus both ways where the browser and server can (opus.ts), else as PCM.
// A conversation keeps it open and streaming, and the server opens and ends turns (§12).
// Replies play through `playback`, dropping any generation older than the floor (§7).
// With the wake word on, the microphone stays open and its frames go only to the wake word
// detector, in the browser, until it hears the phrase and opens a conversation.
// React subscribes to the snapshot; Strata reads `source` and `playback.source` each frame.
import type { ModelChoice } from '@/lib/api'
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { MicError, Microphone } from './mic'
import chime from './approval-chime.mp3'
import wakeChime from './wake-chime.mp3'
import { OpusDownlink, OpusUplink, opusSupport, parseOpusFrame } from './opus'
import { Playback, parseFrame } from './playback'
import { WakeDetector, wakePhrase } from './wake'
import { VoiceSocket, type Codec, type SpeechStatus, type VoiceMessage, type VoiceState } from './socket'

// An open mic with echo cancellation puts a phone in call mode, quieter and duller, so it closes
// soon after a turn: long enough for the last frame to be sent, and before the reply plays.
const RELEASE_MIC_AFTER_MS = 1_000
const WORDS_LINGER_MS = 8_000 // the last words stay this long once the session is quiet
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }
const CHIME_S = 1.4 // approval-chime.mp3
const WAKE_KEY = 'sennet.wake' // the wake word setting, per device
const MUTED_KEY = 'sennet.muted' // replies muted on this device
const WAKE_QUIET_MS = 10_000 // a conversation the wake word opened closes after this long with nobody talking

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
  wakePhrase: string | null      // what the server's wake word model listens for; null without one
  wake: 'off' | 'loading' | 'on' // listening for the wake word on this device
  wakeEnabled: boolean           // Settings: listen for it on this device, from the first tap on the page
  wakePaused: boolean            // paused from the stage until tapped again, or the page reloads
  muted: boolean                 // replies play on this device unheard: captions and all, no sound
}

interface Played {
  generation: number
  onStart?: () => void
}

export class Voice {
  readonly socket = new VoiceSocket()
  readonly playback = new Playback()
  private readonly opus = opusSupport()
  private codecs: { uplink: Codec; downlink: Codec } = { uplink: 'pcm_s16le', downlink: 'pcm_s16le' }
  private uplink: OpusUplink | null = null // the open stream of Opus frames, from a turn's or conversation's first frame
  private streams = 0
  private downlink: OpusDownlink<Played> | null = null
  private mic: Microphone | null = null
  private opening: Promise<Microphone> | null = null
  private releaseTimer: ReturnType<typeof setTimeout> | null = null
  private turn: string | null = null
  private ending: string | null = null // released, its last frame still on the way
  private turns = 0
  private outputRate = 24000
  private latest = 0 // the highest playback generation seen
  private floor = 0  // frames below this generation are stale
  // Each reply's segment texts, by the server's reply id. Frames belong to the latest `speech`
  // message: the server sends a segment's `speech` and then its frames, in order, on one socket.
  private readonly replyText = new Map<number, string[]>()
  private readonly replyTask = new Map<number, number | null>() // what each reply is about
  private playingTask: number | null = null // the task of the reply last started
  private heard: { reply: number; segment: number } | null = null
  private lastStarted: { reply: number; segment: number } | null = null // reported in `drained`
  private wordsTimer: ReturnType<typeof setTimeout> | null = null
  private snapshot: VoiceSnapshot = {
    connected: false, stt: null, turnReady: false, conversation: false, listening: false, opening: false,
    speaking: false, state: 'idle', words: null, wakePhrase: null, wake: 'off',
    wakeEnabled: loadFlag(WAKE_KEY), wakePaused: false, muted: loadFlag(MUTED_KEY),
  }
  private detector: WakeDetector | null = null
  private selection: Target = {} // what is on screen: the wake word opens a conversation with it
  private woken = false // the open conversation is the wake word's: it closes once things go quiet
  private wakeTimer: ReturnType<typeof setTimeout> | null = null
  private wakeLock: WakeLockSentinel | null = null
  private muted = false // told the user to tap, since speech arrived while audio was suspended
  private readonly subscribers = new Set<() => void>()
  private readonly errorSubscribers = new Set<(message: string) => void>()

  constructor() {
    this.playback.setMuted(this.snapshot.muted)
    this.socket.onOpenChange = (connected) => {
      // A closed socket ends its session: the rest of the reply isn't spoken (§6).
      if (!connected) {
        this.playback.silence()
        this.useCodecs({ uplink: 'pcm_s16le', downlink: 'pcm_s16le' }) // until the next session agrees otherwise
        if (this.snapshot.conversation) this.endConversation() // a new session starts without one
      } else if (!this.snapshot.wakePhrase) {
        void wakePhrase().then((phrase) => this.update({ wakePhrase: phrase }))
      }
      this.update({ connected, ...(connected ? {} : { stt: null, state: 'idle' }) })
    }
    this.socket.on((message) => this.receive(message))
    this.socket.onAudio((data) => this.hear(data))
    this.playback.onPlayingChange = (speaking) => {
      // Everything sent has been heard (or silenced): the server's `playing` follows this (§7).
      if (!speaking && this.lastStarted) this.socket.send({ type: 'drained', ...this.lastStarted })
      this.update({ speaking })
    }
    // The server logs these, so a reply can be followed from queued to heard in one place.
    this.playback.onAudioState = (state) => {
      if (state === 'running') this.muted = false
      this.socket.send({ type: 'audio', state, hidden: document.hidden })
    }
    // Any gesture unlocks audio, so the approval chime plays before the first push-to-talk, and
    // resumes it if the phone suspended it since. With the wake word on, it starts listening too:
    // the microphone and audio need a gesture first, after a reload or the page coming back.
    for (const type of ['pointerdown', 'keydown']) {
      document.addEventListener(type, (event) => {
        this.playback.unlock()
        if (!(event.target instanceof Element && event.target.closest('[data-wake-control]'))) this.armWake()
      }, { capture: true })
    }
    // Phones suspend a hidden page's microphone anyway: end the conversation cleanly instead.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && this.snapshot.conversation) this.closeConversation()
      if (document.hidden) this.stopWake()
      if (!document.hidden) this.playback.resume()
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
    if (!this.beginConversation(target)) this.releaseMicLater()
  }

  private beginConversation(target: Target): boolean {
    if (!this.socket.send({ type: 'open_mic', ...target })) {
      this.fail('Voice is not connected yet.')
      return false
    }
    this.update({ conversation: true })
    if (!this.wakeLock) void this.keepAwake()
    return true
  }

  /**
   * Listen for the wake word: the microphone stays open, heard only by the detector here, and
   * the phrase opens a conversation with `target`. Must be called inside a gesture.
   */
  async listenForWake(target: Target) {
    if (this.snapshot.wake !== 'off') return
    this.selection = target
    this.playback.unlock() // replies to what's said after the wake word play without a tap
    if (this.snapshot.stt !== 'ready' || !this.snapshot.turnReady) {
      this.fail(this.snapshot.connected ? 'The wake word needs speech recognition and turn detection. Run `sennet setup`.'
        : 'Voice is not connected yet.')
      return
    }
    this.holdMic()
    this.update({ wake: 'loading' })
    const detector = new WakeDetector(() => this.woke(), (message) => {
      if (this.detector === detector && this.snapshot.wake === 'on') {
        this.stopWake()
        this.update({ wakePaused: true }) // not tried again at every tap; tapping the phrase does
        this.fail(`The wake word stopped: ${message}`)
      }
    })
    this.detector = detector
    try {
      await Promise.all([this.openMic(), detector.ready])
    } catch (error) {
      if (this.detector !== detector) return // turned off while loading
      this.stopWake()
      this.update({ wakePaused: true })
      this.fail(error instanceof MicError ? error.message : `The wake word could not start: ${String(error)}`)
      return
    }
    if (this.detector !== detector) return
    this.update({ wake: 'on' })
    void this.keepAwake()
  }

  /** The Settings choice: listen for the wake word on this device. Called inside a gesture. */
  setWakeEnabled(on: boolean) {
    saveFlag(WAKE_KEY, on)
    this.update({ wakeEnabled: on, wakePaused: false })
    if (on) void this.listenForWake(this.selection)
    else this.stopWake()
  }

  /** Mute replies on this device, or hear them again. They keep coming either way. */
  setMuted(muted: boolean) {
    saveFlag(MUTED_KEY, muted)
    this.playback.setMuted(muted)
    this.update({ muted })
  }

  /** Pause it from the stage, until it is tapped again. Called inside a gesture to resume. */
  pauseWake(paused: boolean) {
    this.update({ wakePaused: paused })
    if (paused) this.stopWake()
    else void this.listenForWake(this.selection)
  }

  /** On a gesture: start listening if it is on and not running, once speech is ready for it. */
  private armWake() {
    const { wakeEnabled, wakePaused, wake, wakePhrase, stt, turnReady } = this.snapshot
    if (wakeEnabled && !wakePaused && wake === 'off' && wakePhrase && stt === 'ready' && turnReady) {
      void this.listenForWake(this.selection)
    }
  }

  stopWake() {
    if (this.snapshot.wake === 'off') return
    this.detector?.close()
    this.detector = null
    this.update({ wake: 'off' })
    if (!this.snapshot.conversation) {
      if (!this.turn) this.releaseMicLater()
      void this.wakeLock?.release().catch(() => {})
      this.wakeLock = null
    }
  }

  /** The wake word was heard: what's said next is a conversation's first turn. */
  private woke() {
    if (this.snapshot.wake !== 'on' || this.snapshot.conversation || this.turn || this.ending) return
    if (this.snapshot.speaking) this.stop() // as talking over a reply would
    if (!this.beginConversation(this.selection)) return
    this.woken = true
    this.playback.cue(wakeChime) // so you know it's listening
  }

  closeConversation() {
    if (!this.snapshot.conversation) return
    this.socket.send({ type: 'close_mic' })
    this.endConversation()
  }

  /** The selection changed: only its task's replies are spoken here, and the next turn of an
   *  open conversation goes there. */
  retarget(target: Target) {
    this.selection = target
    this.socket.send({ type: 'target', ...target })
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
    const flushed = (this.mic ? this.mic.flush() : Promise.resolve()).then(() => this.endStream())
    void flushed.then(() => {
      if (this.ending === turn) this.ending = null
      this.socket.send({ type: 'end', turn })
    })
  }

  /** Drop the turn without a final pass, e.g. the pointer slid off the button. */
  cancel() {
    if (!this.turn) return
    this.socket.send({ type: 'cancel', turn: this.turn })
    this.dropStream()
    this.endTurn()
  }

  /** You moved to another task (`to`) or a new one (null): the words under the disc were about
   *  the one you left, so they go, unless you are still saying them. */
  leave(to: number | null) {
    if (!this.snapshot.listening && this.snapshot.words) this.update({ words: null })
    // The server says no more about a task once it isn't on screen, but what it already sent is
    // here, queued to play: stop it, unless it is about the task being opened.
    if (this.snapshot.speaking && this.playingTask !== to) this.stop()
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
    this.heard = this.lastStarted = null
    const words = this.snapshot.words
    if (words?.speaker === 'agent' && words.provisional) this.update({ words: { ...words, provisional: '' } })
  }

  private endTurn() {
    this.turn = null
    this.update({ listening: false })
    if (!this.snapshot.conversation) this.releaseMicLater()
  }

  private endConversation() {
    this.dropStream()
    this.turn = null
    this.woken = false
    this.update({ conversation: false, listening: false })
    if (this.snapshot.wake !== 'off') {
      // Back to listening for the wake word, with the microphone and screen kept on.
      this.detector?.reset()
      return
    }
    this.releaseMicLater()
    void this.wakeLock?.release().catch(() => {})
    this.wakeLock = null
  }

  /** Close the microphone soon, unless the wake word is listening on it. */
  private releaseMicLater() {
    this.holdMic()
    if (this.snapshot.wake !== 'off') return
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
      if (this.turn || this.ending || this.snapshot.conversation) this.send(frame)
      else if (this.snapshot.wake === 'on') this.detector?.push(frame)
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

  private send(frame: Int16Array<ArrayBuffer>) {
    if (this.codecs.uplink === 'pcm_s16le') return this.socket.sendFrame(frame)
    this.uplink ??= new OpusUplink(++this.streams, (data) => this.socket.sendFrame(data), (error) => {
      console.warn('Opus encoding failed; sending PCM instead', error)
      this.useCodecs({ ...this.codecs, uplink: 'pcm_s16le' }, true)
    })
    this.uplink.push(frame, this.turn !== null)
  }

  /** The end of a turn: the last packets go out before `end`, and the next turn is a new stream. */
  private async endStream() {
    const uplink = this.uplink
    this.uplink = null
    await uplink?.flush()
  }

  private dropStream() {
    this.uplink?.close()
    this.uplink = null
  }

  /** Switch codecs for what is sent and received from here on, telling the server if `tell`. */
  private useCodecs(codecs: { uplink: Codec; downlink: Codec }, tell = false) {
    if (tell) this.socket.send({ type: 'codecs', ...codecs })
    if (codecs.uplink !== this.codecs.uplink) this.dropStream()
    if (codecs.downlink !== this.codecs.downlink || codecs.downlink === 'pcm_s16le') {
      this.downlink?.close()
      this.downlink = null
    }
    this.codecs = codecs
    if (codecs.downlink === 'opus' && !this.downlink) {
      this.downlink = new OpusDownlink<Played>((samples, rate, played) => {
        if (played.generation >= this.floor) this.playback.play(samples, rate, played.onStart)
      }, (error) => {
        console.warn('Opus decoding failed; asking for PCM instead', error)
        this.useCodecs({ ...this.codecs, downlink: 'pcm_s16le' }, true)
      })
    }
  }

  /** A new session offers Opus: use it in each direction this browser can. */
  private async negotiate(offered: string[]) {
    if (!offered.includes('opus')) return
    const support = await this.opus
    if (!this.socket.open || (!support.uplink && !support.downlink)) return
    this.useCodecs({ uplink: support.uplink ? 'opus' : 'pcm_s16le', downlink: support.downlink ? 'opus' : 'pcm_s16le' },
      true)
  }

  private receive(message: VoiceMessage) {
    if (message.type === 'ready') {
      // A new session: generations start again from its own.
      this.latest = this.floor = message.generation
      this.replyText.clear() // reply ids start again with the session
      this.heard = this.lastStarted = null
      this.outputRate = message.output.rate
      this.socket.send({ type: 'target', ...this.selection }) // the new session's task on screen
      this.useCodecs({ uplink: 'pcm_s16le', downlink: 'pcm_s16le' })
      void this.negotiate(message.codecs ?? [])
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
      if (!this.playback.audible && !this.muted) {
        // Queued speech would wait silently for the next tap; say so instead.
        this.muted = true
        this.playback.resume()
        this.fail('Your phone paused Sennet\'s audio. Tap the screen to hear the reply.')
      }
      if (message.segment === 0) {
        this.replyText.set(message.reply, [])
        this.replyTask.set(message.reply, message.task_id)
      }
      this.replyText.get(message.reply)?.push(message.text)
      this.heard = { reply: message.reply, segment: message.segment }
    }
    if (message.type === 'voice_error') {
      if (message.turn && message.turn === this.turn) this.endTurn()
      this.fail(message.message)
    }
  }

  private hear(data: ArrayBuffer) {
    const frame = parseOpusFrame(data) ?? parseFrame(data)
    if (!frame) return
    this.latest = Math.max(this.latest, frame.generation)
    if (frame.generation < this.floor) return
    // The first frame of a segment shows its words as it starts playing.
    const heard = this.heard
    const first = heard && heard.segment === frame.segment ? heard : null
    if (first) this.heard = null
    const onStart = first ? () => {
      this.lastStarted = { reply: first.reply, segment: first.segment }
      this.socket.send({ type: 'played', ...this.lastStarted })
      this.spoken(first.reply, first.segment)
    } : undefined
    if ('packets' in frame) {
      this.downlink?.decode(frame.packets, (firstPacket) => ({ generation: frame.generation,
        onStart: firstPacket ? onStart : undefined }))
    } else {
      this.playback.play(frame.samples, this.outputRate, onStart)
    }
  }

  /** A segment started playing: its reply so far is said, the segments queued behind it are not yet. */
  private spoken(reply: number, segment: number) {
    for (const old of this.replyText.keys()) if (old < reply) this.replyText.delete(old)
    for (const old of this.replyTask.keys()) if (old < reply) this.replyTask.delete(old)
    this.playingTask = this.replyTask.get(reply) ?? null
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
    const settled = quiet && this.woken && this.snapshot.conversation
    if (settled && !this.wakeTimer) {
      this.wakeTimer = setTimeout(() => {
        this.wakeTimer = null
        if (this.woken) this.closeConversation()
      }, WAKE_QUIET_MS)
    } else if (!settled && this.wakeTimer) {
      clearTimeout(this.wakeTimer)
      this.wakeTimer = null
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

function loadFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === 'on'
  } catch {
    return false
  }
}

function saveFlag(key: string, on: boolean) {
  try {
    localStorage.setItem(key, on ? 'on' : 'off')
  } catch {
    // kept for this page only
  }
}
