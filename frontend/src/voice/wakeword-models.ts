// openWakeWord's three ONNX models in onnxruntime-web, as wakeword.ts's WakeModels: the mel
// spectrogram, Google's speech embedding, and the wake word itself. One thread, so it needs no
// cross-origin isolation; the models are small enough that 80 ms of audio takes a few ms.
import * as ort from 'onnxruntime-web/wasm'
import type { WakeModels } from './wakeword'

export interface ModelFiles {
  mel: ArrayBuffer
  embed: ArrayBuffer
  wake: ArrayBuffer
}

ort.env.wasm.numThreads = 1

/** Load the models; `inputs` is how many embeddings the wake word model scores at once. */
export async function loadModels(files: ModelFiles): Promise<{ models: WakeModels; inputs: number }> {
  const options: ort.InferenceSession.SessionOptions = { executionProviders: ['wasm'] }
  const [mel, embed, wake] = await Promise.all([
    ort.InferenceSession.create(new Uint8Array(files.mel), options),
    ort.InferenceSession.create(new Uint8Array(files.embed), options),
    ort.InferenceSession.create(new Uint8Array(files.wake), options),
  ])
  const shape = (wake.inputMetadata[0] as { shape?: (number | string)[] }).shape
  const inputs = typeof shape?.[1] === 'number' ? shape[1] : 16
  const run = async (session: ort.InferenceSession, data: Float32Array, dims: number[]) => {
    const result = await session.run({ [session.inputNames[0]]: new ort.Tensor('float32', data, dims) })
    return result[session.outputNames[0]].data as Float32Array
  }
  return {
    inputs,
    models: {
      mel: (samples) => run(mel, samples, [1, samples.length]),
      embed: (window) => run(embed, window, [1, 76, 32, 1]),
      score: async (features) => (await run(wake, features, [1, inputs, 96]))[0],
    },
  }
}
