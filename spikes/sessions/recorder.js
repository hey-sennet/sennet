// The microphone for both pages: opened with the product's constraints (frontend/src/voice/mic.ts)
// unless `aec` is off, it taps raw samples at the device rate and keeps them while recording.
// The server resamples to 16 kHz, so the browser's own resampling isn't part of what's measured.

const TAP = `
class Tap extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0]
    if (channel) this.port.postMessage(channel.slice(0))
    return true
  }
}
registerProcessor('tap', Tap)`

export class Recorder {
  static async open({ aec = true, context = null } = {}) {
    const own = !context
    context ??= new AudioContext()
    const resumed = context.resume()
    const url = URL.createObjectURL(new Blob([TAP], { type: 'text/javascript' }))
    await context.audioWorklet.addModule(url)
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: aec, noiseSuppression: false, autoGainControl: true },
    })
    const source = context.createMediaStreamSource(stream)
    const tap = new AudioWorkletNode(context, 'tap', { numberOfOutputs: 1 })
    const mute = context.createGain()
    mute.gain.value = 0
    source.connect(tap).connect(mute).connect(context.destination)
    await resumed
    return new Recorder(stream, context, tap, own, aec)
  }

  constructor(stream, context, tap, own, aec) {
    this.stream = stream
    this.context = context
    this.own = own
    this.aec = aec
    this.received = 0   // samples since the mic opened
    this.chunks = null  // while recording
    this.startedAt = 0  // `received` when recording started
    this.trials = 0     // recordings since the mic opened: echo cancellers take time to adapt
    this.onChunk = null // (samples) => void, for pages that watch the level
    tap.port.onmessage = (event) => {
      this.received += event.data.length
      this.chunks?.push(event.data)
      this.onChunk?.(event.data)
    }
  }

  get rate() {
    return this.context.sampleRate
  }

  /** What the browser applied, which may differ from what was asked for. */
  get settings() {
    const s = this.stream.getAudioTracks()[0].getSettings()
    return { echoCancellation: s.echoCancellation, noiseSuppression: s.noiseSuppression,
      autoGainControl: s.autoGainControl, sampleRate: s.sampleRate, deviceId: s.deviceId, label: this.stream.getAudioTracks()[0].label }
  }

  /** The sample index, counted from the start of this recording, that is being captured now. */
  get now() {
    return this.received - this.startedAt
  }

  start() {
    this.chunks = []
    this.startedAt = this.received
  }

  stop() {
    const chunks = this.chunks ?? []
    this.chunks = null
    this.trials += 1
    const audio = new Float32Array(chunks.reduce((n, c) => n + c.length, 0))
    let at = 0
    for (const c of chunks) {
      audio.set(c, at)
      at += c.length
    }
    return audio
  }

  async close() {
    this.stream.getTracks().forEach((t) => t.stop())
    if (this.own) await this.context.close()
  }
}

export async function upload(meta, audio) {
  const response = await fetch('/api/recordings', {
    method: 'POST',
    headers: { 'x-meta': JSON.stringify(meta), 'content-type': 'application/octet-stream' },
    body: audio.buffer,
  })
  if (!response.ok) throw new Error(`upload failed: ${response.status}`)
  return response.json()
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export function stored(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

export function store(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {}
}
