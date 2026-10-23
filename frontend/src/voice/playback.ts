// Speech playback, outside React: downlink frames (docs/voice-protocol.md §3) resampled to the
// context's rate and played as one continuous stream by a worklet (stream.ts), with an analyser
// for Strata's agent source. Scheduling each chunk as its own source crackled on phones.
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { FFT_SIZE, SMOOTHING, micSignal } from './bands'
import type { PlaybackMessage } from './playback.worklet'
import workletUrl from './playback.worklet.ts?worker&url'
import { Resampler } from './resample'
import type { StreamEvent } from './stream'

const VERSION = 1
const AUDIO = 1
const HEADER_BYTES = 8
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }
// Speech dips under a cue so the cue is heard, then comes back.
const DUCK = 0.35
const DUCK_S = 0.08

export interface AudioFrame {
  segment: number
  generation: number
  samples: Float32Array<ArrayBuffer>
}

/** Parses a downlink frame; null for a version or kind this client doesn't know. */
export function parseFrame(data: ArrayBuffer): AudioFrame | null {
  if (data.byteLength < HEADER_BYTES) return null
  const view = new DataView(data)
  if (view.getUint8(0) !== VERSION || view.getUint8(1) !== AUDIO) return null
  const count = (data.byteLength - HEADER_BYTES) >> 1
  const samples = new Float32Array(count)
  for (let i = 0; i < count; i++) samples[i] = view.getInt16(HEADER_BYTES + i * 2, true) / 32768
  return { segment: view.getUint16(2, true), generation: view.getUint32(4, true), samples }
}

export class Playback {
  private context: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private duck: GainNode | null = null
  private node: AudioWorkletNode | null = null
  private waiting: PlaybackMessage[] | null = null // posted before the worklet loaded
  private resampler: Resampler | null = null
  private epoch = 0  // each silence() starts a new one; the worklet's events name theirs
  private pushed = 0 // samples sent this epoch
  private marks = 0
  private readonly starts = new Map<number, () => void>()
  private speaking = false
  private readonly cues = new Map<string, Promise<AudioBuffer>>()
  /** Runs that ran dry mid-reply: each one is a gap in the speech. */
  underruns = 0
  onPlayingChange: (playing: boolean) => void = () => {}
  /** The context's state changed: a phone can suspend it (screen off, another app's audio). */
  onAudioState: (state: string) => void = () => {}

  get playing() {
    return this.speaking
  }

  /** Whether speech can be heard now: unlocked, and not suspended by the browser or the phone. */
  get audible() {
    return this.context?.state === 'running'
  }

  /** Try to resume a context the browser or phone suspended. Outside a gesture this works only
   * once the page has had one, so it can fail; `onAudioState` says whether it did. */
  resume() {
    if (this.context && this.context.state !== 'running' && this.context.state !== 'closed') {
      this.context.resume().catch(() => {})
    }
  }

  /** Creates or resumes the context. Call inside a user gesture, or the browser keeps it silent. */
  unlock() {
    if (!this.context) {
      const context = this.context = new AudioContext()
      context.onstatechange = () => {
        this.onAudioState(context.state)
        this.resume() // suspended mid-reply: come back on our own if the browser lets us
      }
      this.analyser = context.createAnalyser()
      this.analyser.fftSize = FFT_SIZE
      this.analyser.smoothingTimeConstant = SMOOTHING
      // Ducking comes after the analyser, so Strata still sees the speech at full level.
      this.duck = context.createGain()
      this.analyser.connect(this.duck).connect(context.destination)
      this.waiting = []
      context.audioWorklet.addModule(workletUrl).then(() => {
        const node = new AudioWorkletNode(context, 'sennet-playback',
          { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1] })
        node.port.onmessage = (event) => this.heard(event.data)
        node.connect(this.analyser!)
        for (const message of this.waiting ?? []) node.port.postMessage(message)
        this.waiting = null
        this.node = node
        console.info('Playback', { sampleRate: context.sampleRate, baseLatency: context.baseLatency,
          outputLatency: context.outputLatency })
      }, (error) => console.warn('Could not load the playback worklet; speech will be silent', error))
    }
    this.resume()
  }

  /** Queues `samples` straight after whatever is already queued; `onStart` runs as they begin. */
  play(samples: Float32Array<ArrayBuffer>, rate: number, onStart?: () => void) {
    const context = this.context
    if (!context || !samples.length) return
    if (this.resampler?.inputRate !== rate) this.resampler = new Resampler(rate, context.sampleRate)
    const out = this.resampler.push(samples)
    let mark: number | undefined
    if (onStart) this.starts.set(mark = ++this.marks, onStart)
    this.pushed += out.length
    this.post({ type: 'push', samples: out, mark }, out === samples ? [] : [out.buffer])
    if (!this.speaking) {
      this.speaking = true
      this.onPlayingChange(true)
    }
  }

  private post(message: PlaybackMessage, transfer: Transferable[] = []) {
    if (this.node) this.node.port.postMessage(message, transfer)
    else this.waiting?.push(message)
  }

  private heard({ epoch, events }: { epoch: number; events: StreamEvent[] }) {
    if (epoch !== this.epoch) return // from before the last silence()
    for (const event of events) {
      if (event.type === 'started') {
        this.starts.get(event.mark)?.()
        this.starts.delete(event.mark)
      } else if (event.type === 'drained') {
        if (event.consumed >= this.pushed && this.speaking) {
          this.speaking = false
          this.onPlayingChange(false)
        }
      } else {
        this.underruns++
        console.info('Playback ran dry mid-reply; lead is now', event.lead, 's')
      }
    }
  }

  /**
   * Plays a short sound file straight away, over any speech, which dips while it plays. It isn't
   * speech: it skips the analyser and doesn't count as playing, so Strata doesn't show it as the
   * agent talking. Silent until the context has been unlocked by a gesture. With `before`, speech
   * queued after it waits that many seconds instead, so the cue leads into it.
   */
  cue(url: string, { before }: { before?: number } = {}) {
    const context = this.context
    if (!context || context.state !== 'running') return
    if (before) this.post({ type: 'hold', frames: Math.round(before * context.sampleRate) })
    let decoded = this.cues.get(url)
    if (!decoded) {
      decoded = fetch(url).then((response) => response.arrayBuffer()).then((data) => context.decodeAudioData(data))
      decoded.catch(() => this.cues.delete(url)) // try again next time
      this.cues.set(url, decoded)
    }
    void decoded.then((buffer) => {
      const source = context.createBufferSource()
      source.buffer = buffer
      source.connect(context.destination)
      source.onended = () => source.disconnect()
      const now = context.currentTime
      const gain = this.duck!.gain
      gain.cancelScheduledValues(now)
      gain.setValueAtTime(gain.value, now)
      gain.linearRampToValueAtTime(DUCK, now + DUCK_S)
      gain.setValueAtTime(DUCK, now + buffer.duration - DUCK_S)
      gain.linearRampToValueAtTime(1, now + buffer.duration)
      source.start(now)
    }, () => {})
  }

  /** Stops everything queued, at once. */
  silence() {
    const was = this.speaking
    this.post({ type: 'clear', epoch: ++this.epoch })
    this.resampler = null
    this.pushed = 0
    this.starts.clear()
    this.speaking = false
    if (was) this.onPlayingChange(false)
  }

  /** Strata's agent source: loudness and bands of what is playing. */
  readonly source: AudioSource = (() => {
    const bands = new Float32Array(BANDS)
    let spectrum: Uint8Array<ArrayBuffer> | null = null
    return {
      read: () => {
        if (!this.analyser || !this.playing) return SILENCE
        spectrum ??= new Uint8Array(this.analyser.frequencyBinCount)
        this.analyser.getByteFrequencyData(spectrum)
        return micSignal(spectrum, bands)
      },
    }
  })()
}
