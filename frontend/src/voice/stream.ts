// The speech player's buffer, run on the audio thread by playback.worklet.ts. Queued audio
// plays as one continuous stream, so there are no joins between chunks to click. A run waits
// until `lead` is buffered before it starts; one that runs dry (an underrun) raises the lead,
// up to MAX_LEAD_S, so a jittery network gets more headroom. Each run fades in, and stopping
// or running dry fades out, so neither clicks. Pure, so unit tests can drive it.

export const LEAD_S = 0.1
export const MAX_LEAD_S = 0.4
const STEP_S = 0.05
const RAMP = 64        // samples of fade-in at the start of a run
const DECAY = 0.9      // per sample, from the last sample played to silence: about 64 samples
const UNDERRUN_S = 1   // audio arriving this soon after a run ran dry means it ran dry too early

export type StreamEvent =
  | { type: 'started'; mark: number }     // a pushed chunk with this mark began playing
  | { type: 'drained'; consumed: number } // ran out of audio, having played `consumed` samples since `clear`
  | { type: 'underrun'; lead: number }    // ran dry mid-reply; `lead` is the new lead in seconds

interface Chunk {
  samples: Float32Array
  offset: number
  mark?: number
}

export class SpeechStream {
  private readonly rate: number
  private readonly chunks: Chunk[] = []
  private buffered = 0
  private consumed = 0
  private lead: number
  private running = false
  private ramp = 0            // samples into the current run's fade-in
  private last = 0            // the last sample output, which silence decays from
  private dryAt = -Infinity   // the frame the last run ran dry
  private pushedAt = 0        // the frame of the last push
  private holdUntil = 0       // no run starts before this frame
  private now = 0
  private events: StreamEvent[] = []

  constructor(rate: number) {
    this.rate = rate
    this.lead = Math.round(LEAD_S * rate)
  }

  push(samples: Float32Array, mark?: number) {
    if (!samples.length && mark === undefined) return
    if (!this.running && !this.buffered && this.now - this.dryAt < UNDERRUN_S * this.rate) {
      this.lead = Math.min(this.lead + Math.round(STEP_S * this.rate), Math.round(MAX_LEAD_S * this.rate))
      this.events.push({ type: 'underrun', lead: this.lead / this.rate })
    }
    this.dryAt = -Infinity
    this.chunks.push({ samples, offset: 0, mark })
    this.buffered += samples.length
    this.pushedAt = this.now
  }

  /** Drop everything queued. What was playing fades out rather than stopping dead. */
  clear() {
    this.chunks.length = 0
    this.buffered = 0
    this.consumed = 0
    this.running = false
    this.dryAt = -Infinity // a stop isn't an underrun
  }

  /** The next run waits `frames` from now before it starts, e.g. for a cue to lead into it. */
  hold(frames: number) {
    this.holdUntil = Math.max(this.holdUntil, this.now + frames)
  }

  /** Fill `out` with the next block, which starts at `frame` (the audio thread's currentFrame). */
  render(out: Float32Array, frame: number) {
    this.now = frame
    let i = 0
    if (!this.running && this.chunks.length && frame >= this.holdUntil
        && (this.buffered >= this.lead || frame - this.pushedAt >= this.lead)) {
      // Enough to ride out jitter, or nothing more is coming soon (the end of a short reply).
      this.running = true
      this.ramp = 0
    }
    while (this.running && i < out.length && this.chunks.length) {
      const chunk = this.chunks[0]
      if (chunk.offset === 0 && chunk.mark !== undefined) this.events.push({ type: 'started', mark: chunk.mark })
      const take = Math.min(out.length - i, chunk.samples.length - chunk.offset)
      for (let k = 0; k < take; k++) {
        let sample = chunk.samples[chunk.offset + k]
        if (this.ramp < RAMP) sample *= ++this.ramp / RAMP
        out[i++] = sample
      }
      chunk.offset += take
      this.buffered -= take
      this.consumed += take
      if (chunk.offset === chunk.samples.length) this.chunks.shift()
    }
    if (i) this.last = out[i - 1]
    if (this.running && !this.chunks.length) {
      this.running = false
      this.dryAt = frame + i
      this.events.push({ type: 'drained', consumed: this.consumed })
    }
    for (; i < out.length; i++) out[i] = this.last *= DECAY
  }

  /** The events since the last call. */
  take(): StreamEvent[] {
    const events = this.events
    if (events.length) this.events = []
    return events
  }
}
