// The page's side of the wake word worker. The phrase comes from the server, which has a model
// only once `sennet setup` has downloaded one and the ONNX runtime it runs on.
import type { WakeIn, WakeOut } from './wakeword.worker'

const BASE = '/api/wakeword/'

/** The phrase the wake word model listens for, or null when the server has none. */
export async function wakePhrase(): Promise<string | null> {
  try {
    const response = await fetch('/api/wakeword')
    return response.ok ? ((await response.json()) as { phrase: string | null }).phrase : null
  } catch {
    return null
  }
}

export class WakeDetector {
  /** Resolves once the models are loaded and frames are being scored. */
  readonly ready: Promise<void>
  private readonly worker = new Worker(new URL('./wakeword.worker.ts', import.meta.url), { type: 'module' })

  constructor(onWake: () => void, onError: (message: string) => void) {
    this.ready = new Promise((resolve, reject) => {
      this.worker.onmessage = (event: MessageEvent<WakeOut>) => {
        const message = event.data
        if (message.type === 'ready') resolve()
        if (message.type === 'wake') onWake()
        if (message.type === 'error') {
          reject(new Error(message.message))
          onError(message.message)
        }
      }
      this.worker.onerror = (event) => reject(new Error(event.message || 'The wake word worker failed to start.'))
    })
    this.send({ type: 'load', base: BASE })
  }

  /** A copy goes to the worker: the frame may still be on its way to the server. */
  push(frame: Int16Array<ArrayBuffer>) {
    const copy = frame.slice()
    this.worker.postMessage(copy satisfies WakeIn, [copy.buffer])
  }

  /** Forget what was heard, so the end of one conversation can't wake the next. */
  reset() {
    this.send({ type: 'reset' })
  }

  close() {
    this.worker.terminate()
  }

  private send(message: WakeIn) {
    this.worker.postMessage(message)
  }
}
