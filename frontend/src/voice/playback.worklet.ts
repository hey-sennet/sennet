// The playback worklet: runs on the audio thread and plays the page's speech chunks as one
// continuous stream (stream.ts). Loaded by playback.ts with ?worker&url.
import { SpeechStream } from './stream'

declare const sampleRate: number
declare const currentFrame: number
declare function registerProcessor(name: string, processor: unknown): void
declare class AudioWorkletProcessor {
  readonly port: MessagePort
}

export type PlaybackMessage =
  | { type: 'push'; samples: Float32Array; mark?: number }
  | { type: 'clear'; epoch: number }
  | { type: 'hold'; frames: number }

class PlaybackProcessor extends AudioWorkletProcessor {
  private readonly stream = new SpeechStream(sampleRate)
  private epoch = 0

  constructor() {
    super()
    this.port.onmessage = (event: MessageEvent<PlaybackMessage>) => {
      const message = event.data
      if (message.type === 'push') this.stream.push(message.samples, message.mark)
      else if (message.type === 'hold') this.stream.hold(message.frames)
      else {
        this.epoch = message.epoch
        this.stream.clear()
      }
    }
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const channels = outputs[0]
    this.stream.render(channels[0], currentFrame)
    for (let c = 1; c < channels.length; c++) channels[c].set(channels[0])
    const events = this.stream.take()
    if (events.length) this.port.postMessage({ epoch: this.epoch, events })
    return true
  }
}

registerProcessor('sennet-playback', PlaybackProcessor)
