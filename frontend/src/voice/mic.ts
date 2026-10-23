// The microphone, outside React: one AudioContext at the device rate feeding the capture
// worklet (16 kHz frames for the server) and an analyser (Strata's user source).
import type { AudioSource } from '@/strata/engine'
import { BANDS } from '@/strata/field'
import { FFT_SIZE, SMOOTHING, micSignal } from './bands'
import workletUrl from './capture.worklet.ts?worker&url'

export class MicError extends Error {}

export class Microphone {
  private readonly stream: MediaStream
  private readonly context: AudioContext
  private readonly capture: AudioWorkletNode
  private readonly analyser: AnalyserNode
  private flushed: (() => void)[] = []

  private constructor(stream: MediaStream, context: AudioContext, capture: AudioWorkletNode, analyser: AnalyserNode) {
    this.stream = stream
    this.context = context
    this.capture = capture
    this.analyser = analyser
  }

  /**
   * Asks for the microphone. Echo cancellation and gain control are on; noise suppression is
   * off, because it damaged speech enough to change words (HLD §6.1) and Parakeet copes with noise.
   */
  static async open(onFrame: (frame: Int16Array<ArrayBuffer>) => void): Promise<Microphone> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new MicError(window.isSecureContext
        ? 'This browser has no microphone access.'
        : 'The microphone needs HTTPS. Open Sennet through `tailscale serve`, or on this computer at localhost.')
    }
    // Build the audio graph while the browser asks for permission, so capture runs the moment
    // the stream exists: on a first press, anything said before that is lost. The context is
    // created here, still inside the press's user gesture, so it may start.
    const context = new AudioContext()
    const resumed = context.resume()
    const loaded = context.audioWorklet.addModule(workletUrl)
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: false, autoGainControl: true },
      })
    } catch (error) {
      await context.close()
      const name = error instanceof DOMException ? error.name : ''
      throw new MicError(name === 'NotAllowedError' ? 'Microphone access was blocked. Allow it in the browser to talk.'
        : name === 'NotFoundError' ? 'No microphone was found.' : `Could not open the microphone: ${String(error)}`)
    }
    try {
      await loaded
      const source = context.createMediaStreamSource(stream)
      const capture = new AudioWorkletNode(context, 'sennet-capture', { numberOfOutputs: 1 })
      let mic: Microphone | null = null
      capture.port.onmessage = (event) => {
        if (event.data === 'flushed') mic?.flushed.shift()?.()
        else onFrame(event.data as Int16Array<ArrayBuffer>)
      }
      // A silent path to the output keeps the worklet running in every browser.
      const mute = context.createGain()
      mute.gain.value = 0
      source.connect(capture).connect(mute).connect(context.destination)
      const analyser = context.createAnalyser()
      analyser.fftSize = FFT_SIZE
      analyser.smoothingTimeConstant = SMOOTHING
      source.connect(analyser)
      await resumed
      mic = new Microphone(stream, context, capture, analyser)
      return mic
    } catch (error) {
      stream.getTracks().forEach((t) => t.stop())
      await context.close()
      throw new MicError(`Could not start audio capture: ${String(error)}`)
    }
  }

  /** Send what is left of the last 20 ms frame; resolves once it has been handed over. */
  flush(): Promise<void> {
    return new Promise((resolve) => {
      this.flushed.push(resolve)
      this.capture.port.postMessage('flush')
    })
  }

  /** Loudness and bands for Strata, read each animation frame. */
  readonly source: AudioSource = (() => {
    const bands = new Float32Array(BANDS)
    let spectrum: Uint8Array<ArrayBuffer> | null = null
    return {
      read: () => {
        spectrum ??= new Uint8Array(this.analyser.frequencyBinCount)
        this.analyser.getByteFrequencyData(spectrum)
        return micSignal(spectrum, bands)
      },
    }
  })()

  async close() {
    this.stream.getTracks().forEach((t) => t.stop())
    await this.context.close()
  }
}
