// Speech playback, outside React: downlink frames (docs/voice-protocol.md §3) scheduled back
// to back on one AudioContext, with an analyser for Strata's agent source.
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { FFT_SIZE, SMOOTHING, micSignal } from './bands'

const VERSION = 1
const AUDIO = 1
const HEADER_BYTES = 8
// Headroom before the first chunk of a run, so network jitter between chunks doesn't gap.
const LEAD_S = 0.08
const SILENCE = { level: 0, bands: new Float32Array(BANDS) }

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
  private readonly sources = new Set<AudioBufferSourceNode>()
  private readonly timers = new Set<ReturnType<typeof setTimeout>>()
  private next = 0 // when the next chunk starts, in context time
  onPlayingChange: (playing: boolean) => void = () => {}

  get playing() {
    return this.sources.size > 0
  }

  /** Creates or resumes the context. Call inside a user gesture, or the browser keeps it silent. */
  unlock() {
    if (!this.context) {
      this.context = new AudioContext()
      this.analyser = this.context.createAnalyser()
      this.analyser.fftSize = FFT_SIZE
      this.analyser.smoothingTimeConstant = SMOOTHING
      this.analyser.connect(this.context.destination)
    }
    if (this.context.state === 'suspended') void this.context.resume()
  }

  /** Queues `samples` straight after whatever is already queued; `onStart` runs as they begin. */
  play(samples: Float32Array<ArrayBuffer>, rate: number, onStart?: () => void) {
    const context = this.context
    if (!context || !samples.length) return
    const buffer = context.createBuffer(1, samples.length, rate)
    buffer.copyToChannel(samples, 0)
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyser!)
    const at = Math.max(this.next, context.currentTime + (this.sources.size ? 0 : LEAD_S))
    source.start(at)
    this.next = at + buffer.duration
    if (onStart) {
      const timer = setTimeout(() => {
        this.timers.delete(timer)
        onStart()
      }, (at - context.currentTime) * 1000)
      this.timers.add(timer)
    }
    source.onended = () => {
      source.disconnect()
      if (this.sources.delete(source) && !this.sources.size) this.onPlayingChange(false)
    }
    this.sources.add(source)
    if (this.sources.size === 1) this.onPlayingChange(true)
  }

  /** Stops everything queued, at once. */
  silence() {
    const was = this.playing
    for (const source of this.sources) {
      source.onended = null
      source.stop()
      source.disconnect()
    }
    this.sources.clear()
    for (const timer of this.timers) clearTimeout(timer)
    this.timers.clear()
    this.next = 0
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
