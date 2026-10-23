// The capture worklet: runs on the audio thread, mixes the microphone to mono, downsamples
// it to 16 kHz and posts 20 ms PCM16 frames to the page. Loaded by mic.ts with ?worker&url.
import { Downsampler, Framer } from './downsample'

declare const sampleRate: number
declare function registerProcessor(name: string, processor: unknown): void
declare class AudioWorkletProcessor {
  readonly port: MessagePort
}

class CaptureProcessor extends AudioWorkletProcessor {
  private readonly down = new Downsampler(sampleRate)
  private readonly framer = new Framer((frame) => this.port.postMessage(frame, [frame.buffer]))
  private readonly mono = new Float32Array(128)

  constructor() {
    super()
    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.framer.flush()
        this.port.postMessage('flushed') // after the last frame, so the page knows it has everything
      }
    }
  }

  process(inputs: Float32Array[][]): boolean {
    const channels = inputs[0]
    if (!channels?.length) return true
    const length = channels[0].length
    const mono = length === this.mono.length ? this.mono : new Float32Array(length)
    mono.set(channels[0])
    for (let c = 1; c < channels.length; c++) {
      for (let i = 0; i < length; i++) mono[i] += channels[c][i]
    }
    if (channels.length > 1) for (let i = 0; i < length; i++) mono[i] /= channels.length
    this.framer.push(this.down.push(mono))
    return true
  }
}

registerProcessor('sennet-capture', CaptureProcessor)
