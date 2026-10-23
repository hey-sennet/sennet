// Speech audio (24 kHz PCM, 48 kHz Opus) to the playback context's rate, as one stream: the
// filter carries its history from chunk to chunk, so the joins between chunks are seamless.
// Windowed-sinc interpolation from a table of 512 fractional phases, each normalised to unity
// gain, with the cutoff just under the lower of the two Nyquist rates. Pure, for unit tests.

const HALF = 16      // taps each side of the output position
const PHASES = 512

export class Resampler {
  readonly inputRate: number
  readonly outputRate: number
  private readonly step: number             // input samples per output sample
  private readonly table: Float32Array      // PHASES rows of 2 * HALF taps
  private buffer = new Float32Array(2 * HALF)  // the input still needed, from HALF zeros at the start
  private position = HALF                   // where in `buffer` the next output sample falls

  constructor(inputRate: number, outputRate: number) {
    this.inputRate = inputRate
    this.outputRate = outputRate
    this.step = inputRate / outputRate
    this.table = kernel(0.5 * Math.min(1, outputRate / inputRate) * 0.95)
  }

  /** Feed input samples; returns the output samples they complete, HALF input samples behind. */
  push(input: Float32Array<ArrayBuffer>): Float32Array<ArrayBuffer> {
    if (this.inputRate === this.outputRate) return input
    const buffer = new Float32Array(this.buffer.length + input.length)
    buffer.set(this.buffer)
    buffer.set(input, this.buffer.length)
    const out = new Float32Array(Math.ceil(input.length / this.step) + 1)
    let written = 0
    let position = this.position
    while (Math.floor(position) + HALF < buffer.length) {
      const base = Math.floor(position)
      const row = Math.min(PHASES - 1, Math.round((position - base) * PHASES)) * 2 * HALF
      let sum = 0
      for (let k = 0; k < 2 * HALF; k++) sum += this.table[row + k] * buffer[base - HALF + 1 + k]
      out[written++] = sum
      position += this.step
    }
    const keep = Math.floor(position) - HALF + 1
    this.buffer = buffer.slice(keep)
    this.position = position - keep
    return out.subarray(0, written)
  }
}

/** Blackman-windowed sinc taps for each phase; `cutoff` is a fraction of the input rate. */
function kernel(cutoff: number): Float32Array {
  const table = new Float32Array(PHASES * 2 * HALF)
  for (let p = 0; p < PHASES; p++) {
    const frac = p / PHASES
    let sum = 0
    for (let k = 0; k < 2 * HALF; k++) {
      const x = frac + HALF - 1 - k // distance from the output position to tap k's input sample
      const t = 2 * cutoff * x
      const sinc = t === 0 ? 1 : Math.sin(Math.PI * t) / (Math.PI * t)
      const w = Math.abs(x) >= HALF ? 0
        : 0.42 + 0.5 * Math.cos(Math.PI * x / HALF) + 0.08 * Math.cos(2 * Math.PI * x / HALF)
      table[p * 2 * HALF + k] = sinc * w
      sum += sinc * w
    }
    for (let k = 0; k < 2 * HALF; k++) table[p * 2 * HALF + k] /= sum
  }
  return table
}
