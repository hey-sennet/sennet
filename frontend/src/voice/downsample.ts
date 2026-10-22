// Device-rate audio to the 16 kHz the server takes (docs/voice-protocol.md §3), done once
// in the capture worklet. A windowed-sinc low-pass removes everything above 7.2 kHz so
// nothing aliases, then each output sample is read between two filtered input samples.
// Pure, so it runs the same in the worklet and in unit tests.

export const TARGET_RATE = 16000
export const FRAME_SAMPLES = 320 // 20 ms at 16 kHz
const CUTOFF_HZ = 7200

export class Downsampler {
  readonly inputRate: number
  readonly ratio: number
  private readonly taps: Float32Array
  private readonly ring: Float32Array // the last taps.length inputs, newest at `head`
  private head = 0
  private previous = 0 // filtered value of the last input sample
  private index = -1 // index of the last input sample
  private next = 0 // input position of the next output sample

  constructor(inputRate: number) {
    this.inputRate = inputRate
    this.ratio = inputRate / TARGET_RATE
    this.taps = this.ratio === 1 ? Float32Array.of(1) : lowPass(CUTOFF_HZ / inputRate, 21 * Math.ceil(this.ratio))
    this.ring = new Float32Array(this.taps.length)
  }

  /** Feed input samples; returns the 16 kHz samples they complete. */
  push(input: Float32Array): Float32Array {
    if (this.ratio === 1) return input.slice() // already 16 kHz
    const out = new Float32Array(Math.ceil(input.length / this.ratio) + 1)
    let written = 0
    for (let i = 0; i < input.length; i++) {
      this.head = (this.head + 1) % this.ring.length
      this.ring[this.head] = input[i]
      const filtered = this.filter()
      this.index++
      while (this.next <= this.index) {
        // `next` lies between the previous input sample and this one.
        const frac = this.next - (this.index - 1)
        out[written++] = this.index === 0 ? filtered : this.previous + (filtered - this.previous) * frac
        this.next += this.ratio
      }
      this.previous = filtered
    }
    return out.subarray(0, written)
  }

  private filter(): number {
    const { taps, ring } = this
    let sum = 0
    let at = this.head
    for (let k = 0; k < taps.length; k++) {
      sum += taps[k] * ring[at]
      at = at === 0 ? ring.length - 1 : at - 1
    }
    return sum
  }
}

/** Blackman-windowed sinc low-pass with unity gain; `cutoff` is a fraction of the sample rate. */
function lowPass(cutoff: number, length: number): Float32Array {
  const n = length | 1 // odd, so the filter is symmetric about a whole sample
  const taps = new Float32Array(n)
  const middle = (n - 1) / 2
  let sum = 0
  for (let i = 0; i < n; i++) {
    const x = i - middle
    const sinc = x === 0 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x)
    const window = 0.42 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1)) + 0.08 * Math.cos((4 * Math.PI * i) / (n - 1))
    taps[i] = sinc * window
    sum += taps[i]
  }
  for (let i = 0; i < n; i++) taps[i] /= sum
  return taps
}

/** Cuts a stream of 16 kHz samples into 20 ms PCM16 frames. */
export class Framer {
  private readonly emit: (frame: Int16Array) => void
  private frame = new Int16Array(FRAME_SAMPLES)
  private filled = 0

  constructor(emit: (frame: Int16Array) => void) {
    this.emit = emit
  }

  push(samples: Float32Array) {
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]))
      this.frame[this.filled++] = s < 0 ? s * 32768 : s * 32767
      if (this.filled === FRAME_SAMPLES) {
        this.emit(this.frame)
        this.frame = new Int16Array(FRAME_SAMPLES)
        this.filled = 0
      }
    }
  }

  /** The partial frame, if any: the end of a turn. The server accepts a short last frame. */
  flush() {
    if (this.filled) {
      this.emit(this.frame.slice(0, this.filled))
      this.filled = 0
    }
  }
}
