// The wake word worker: loads openWakeWord's models and the ONNX runtime's WebAssembly from the
// server (setup downloads both, pinned, so neither is in the wheel), scores the microphone's
// 16 kHz frames off the page's thread, and says when the wake word was heard. Started by wake.ts.
import * as ort from 'onnxruntime-web/wasm'
import { Trigger, WakeWord } from './wakeword'
import { loadModels } from './wakeword-models'

export type WakeIn = { type: 'load'; base: string } | { type: 'reset' } | Int16Array<ArrayBuffer>
export type WakeOut = { type: 'ready' } | { type: 'error'; message: string } | { type: 'wake'; score: number }

const post = (message: WakeOut) => postMessage(message)
const trigger = new Trigger()
let wake: WakeWord | null = null
let queue = Promise.resolve() // frames are scored in order, one at a time

async function load(base: string) {
  ort.env.wasm.wasmPaths = { wasm: new URL(base + 'ort-wasm-simd-threaded.wasm', location.origin).href }
  const fetchModel = async (name: string) => {
    const response = await fetch(base + name)
    if (!response.ok) throw new Error(`${name}: ${response.status}`)
    return response.arrayBuffer()
  }
  const [mel, embed, model] = await Promise.all(['melspectrogram.onnx', 'embedding_model.onnx', 'wakeword.onnx'].map(fetchModel))
  const { models, inputs } = await loadModels({ mel, embed, wake: model })
  wake = new WakeWord(models, inputs)
  await wake.reset()
}

onmessage = (event: MessageEvent<WakeIn>) => {
  const message = event.data
  queue = queue.then(async () => {
    if (message instanceof Int16Array) {
      for (const score of await wake?.push(message) ?? []) if (trigger.hit(score)) post({ type: 'wake', score })
    } else if (message.type === 'reset') {
      trigger.reset()
      await wake?.reset()
    } else {
      await load(message.base)
      post({ type: 'ready' })
    }
  }).catch((error) => post({ type: 'error', message: String(error) }))
}
