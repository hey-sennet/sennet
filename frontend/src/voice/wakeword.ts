// openWakeWord's streaming pipeline (openwakeword/utils.py AudioFeatures and model.py predict),
// over the capture worklet's 16 kHz frames. Every 80 ms: the newest audio becomes 8 mel frames,
// the last 76 mel frames become one 96-wide speech embedding, and the last 16 embeddings get a
// score from the wake word model. The models are injected, so this runs the same in the worker
// (onnxruntime-web) and in unit tests.

export const CHUNK_SAMPLES = 1280 // 80 ms at 16 kHz
const MEL_CONTEXT = 160 * 3       // the mel model needs 3 frames' more audio than it covers
const MEL_BINS = 32
const MEL_WINDOW = 76             // mel frames per embedding
const EMBEDDING = 96
const WARMUP = 5                  // the first predictions are zero while the buffers fill

export interface WakeModels {
  /** Mel frames for these samples (PCM16 values as floats): frames × 32, before the transform. */
  mel(samples: Float32Array): Promise<Float32Array>
  /** One embedding (96) for 76 × 32 mel values. */
  embed(window: Float32Array): Promise<Float32Array>
  /** The wake word's score, 0 to 1, for the last `inputs` embeddings. */
  score(features: Float32Array): Promise<number>
}

export class WakeWord {
  private readonly models: WakeModels
  private readonly inputs: number
  private pending = new Float32Array(CHUNK_SAMPLES)
  private filled = 0
  private context = new Float32Array(0) // the audio before the current chunk, up to MEL_CONTEXT
  private mel = new Float32Array(MEL_WINDOW * MEL_BINS)
  private features = new Float32Array(0)
  private predictions = 0

  constructor(models: WakeModels, inputs = 16) {
    this.models = models
    this.inputs = inputs
  }

  /**
   * Empty the buffers, as openWakeWord starts: mel frames of ones, and embeddings of 4 s of
   * low noise. `noise` is for tests to match the reference exactly.
   */
  async reset(noise?: Int16Array) {
    this.filled = 0
    this.context = new Float32Array(0)
    this.mel = new Float32Array(MEL_WINDOW * MEL_BINS).fill(1)
    this.predictions = 0
    const audio = noise ?? Int16Array.from({ length: 16000 * 4 }, () => Math.floor(Math.random() * 2000) - 1000)
    const spec = transform(await this.models.mel(Float32Array.from(audio)))
    const frames = spec.length / MEL_BINS
    const embeddings: Float32Array[] = []
    for (let i = 0; i + MEL_WINDOW <= frames; i += 8) {
      embeddings.push(await this.models.embed(spec.subarray(i * MEL_BINS, (i + MEL_WINDOW) * MEL_BINS)))
    }
    this.features = concat(embeddings)
  }

  /** Feed 16 kHz PCM16; returns a score for each 80 ms it completes. */
  async push(frame: Int16Array): Promise<number[]> {
    const scores: number[] = []
    for (let i = 0; i < frame.length; i++) {
      this.pending[this.filled++] = frame[i]
      if (this.filled === CHUNK_SAMPLES) {
        this.filled = 0
        scores.push(await this.chunk(this.pending))
      }
    }
    return scores
  }

  private async chunk(chunk: Float32Array): Promise<number> {
    const audio = concat([this.context, chunk])
    this.context = audio.slice(-MEL_CONTEXT)
    const spec = transform(await this.models.mel(audio))
    this.mel = concat([this.mel, spec]).slice(-MEL_WINDOW * MEL_BINS)
    const embedding = await this.models.embed(this.mel)
    this.features = concat([this.features, embedding]).slice(-this.inputs * EMBEDDING)
    const score = await this.models.score(this.features)
    return this.predictions++ < WARMUP ? 0 : score
  }
}

/** openWakeWord's fix-up, bringing the ONNX mel model close to Google's TensorFlow one. */
function transform(spec: Float32Array): Float32Array {
  return spec.map((x) => x / 10 + 2)
}

function concat(parts: Float32Array[]): Float32Array<ArrayBuffer> {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const part of parts) {
    out.set(part, at)
    at += part.length
  }
  return out
}

/** Fires once per utterance: a score over the threshold, then quiet until scores fall back. */
export class Trigger {
  private readonly threshold: number
  private readonly refractory: number
  private cooling = 0

  constructor(threshold = 0.5, refractoryChunks = 25) { // 25 chunks: 2 s
    this.threshold = threshold
    this.refractory = refractoryChunks
  }

  hit(score: number): boolean {
    if (this.cooling > 0) {
      this.cooling--
      return false
    }
    if (score < this.threshold) return false
    this.cooling = this.refractory
    return true
  }

  reset() {
    this.cooling = 0
  }
}
