// Opus on the voice socket through WebCodecs (docs/voice-protocol.md §3, docs/performance.md
// step 5). Uplink: the microphone's 16 kHz frames are encoded with DTX, so silence costs almost
// nothing. Downlink: the server's packets are decoded for playback. Where the browser lacks
// WebCodecs Opus, the socket stays on PCM.

export const VERSION = 1
export const OPUS = 2
export const HEADER_BYTES = 8
const RATE = 16000
const BITRATE = 24000
// 60 ms packets: a third of the packets of 20 ms, each with ~135 bytes of headers over Tailscale,
// for 16 ms more on a barge-in on average (spikes/perf/opus_eval.py; performance.md step 6).
export const UPLINK_FRAME_MS = 60
const FRAME_SAMPLES = (RATE * UPLINK_FRAME_MS) / 1000
// While the encoder is in DTX nothing is sent, so the server is told how far the silence runs:
// often while a turn is open (its end is found in that silence), rarely otherwise.
const QUIET_IN_TURN_MS = 100
const QUIET_IDLE_MS = 1000
// How far behind the input a packet can still come out of the encoder: the frame being filled,
// and 20 ms for the output to reach the page. A header that claims too much does no harm: the
// server takes a packet that overlaps it as it comes.
const ENCODER_LAG = FRAME_SAMPLES + 320
const DOWNLINK_PACKET_US = 20_000

// `application` and `signal` are in the WebCodecs Opus registration but not yet in TypeScript's DOM types.
const encoderConfig = (): AudioEncoderConfig => ({
  codec: 'opus', sampleRate: RATE, numberOfChannels: 1, bitrate: BITRATE,
  opus: { frameDuration: UPLINK_FRAME_MS * 1000, usedtx: true, application: 'voip', signal: 'voice' } as OpusEncoderConfig,
})
const decoderConfig: AudioDecoderConfig = { codec: 'opus', sampleRate: 48000, numberOfChannels: 1 }

/** Which directions this browser can do in Opus. */
export async function opusSupport(): Promise<{ uplink: boolean; downlink: boolean }> {
  const check = async (test: () => Promise<{ supported?: boolean }>) => {
    try {
      return Boolean((await test()).supported)
    } catch {
      return false
    }
  }
  return {
    uplink: typeof AudioEncoder !== 'undefined' && await check(() => AudioEncoder.isConfigSupported(encoderConfig())),
    downlink: typeof AudioDecoder !== 'undefined' && await check(() => AudioDecoder.isConfigSupported(decoderConfig)),
  }
}

/** An uplink frame: the header, then one packet, or nothing to say "silent up to here". */
export function uplinkFrame(stream: number, position: number, packet?: Uint8Array): ArrayBuffer {
  const frame = new Uint8Array(HEADER_BYTES + (packet?.length ?? 0))
  const view = new DataView(frame.buffer)
  view.setUint8(0, VERSION)
  view.setUint8(1, OPUS)
  view.setUint16(2, stream & 0xffff, true)
  view.setUint32(4, position >>> 0, true)
  if (packet) frame.set(packet, HEADER_BYTES)
  return frame.buffer
}

/** A downlink Opus frame's header and packets; null if it isn't one. */
export function parseOpusFrame(data: ArrayBuffer): { segment: number; generation: number; packets: Uint8Array[] } | null {
  if (data.byteLength < HEADER_BYTES) return null
  const view = new DataView(data)
  if (view.getUint8(0) !== VERSION || view.getUint8(1) !== OPUS) return null
  const packets: Uint8Array[] = []
  for (let at = HEADER_BYTES; at + 2 <= data.byteLength;) {
    const size = view.getUint16(at, true)
    if (at + 2 + size > data.byteLength) return null
    packets.push(new Uint8Array(data, at + 2, size))
    at += 2 + size
  }
  return { segment: view.getUint16(2, true), generation: view.getUint32(4, true), packets }
}

/**
 * When to tell the server how far a silence runs. `fed` is how many samples went into the
 * encoder; `sent` is where the last packet or header ended.
 */
export class Quiet {
  sent = 0
  private lastAt = -Infinity

  /** The position to send in a header-only frame now, or null. */
  due(fed: number, now: number, inTurn: boolean): number | null {
    const position = fed - ENCODER_LAG
    if (position <= this.sent || now - this.lastAt < (inTurn ? QUIET_IN_TURN_MS : QUIET_IDLE_MS)) return null
    this.sent = position
    this.lastAt = now
    return position
  }

  packet(end: number, now: number) {
    this.sent = Math.max(this.sent, end)
    this.lastAt = now
  }
}

/** One stream of microphone audio as Opus: from the first frame of a turn or conversation to its flush. */
export class OpusUplink {
  private readonly encoder: AudioEncoder
  private readonly quiet = new Quiet()
  private fed = 0
  private readonly stream: number
  private readonly send: (frame: ArrayBuffer) => void

  constructor(stream: number, send: (frame: ArrayBuffer) => void, onError: (error: DOMException) => void) {
    this.stream = stream
    this.send = send
    this.encoder = new AudioEncoder({
      output: (chunk) => {
        if (chunk.byteLength <= 2) return // DTX: nothing worth sending
        const packet = new Uint8Array(chunk.byteLength)
        chunk.copyTo(packet)
        const position = Math.round((chunk.timestamp * RATE) / 1e6)
        this.quiet.packet(position + Math.round(((chunk.duration ?? 0) * RATE) / 1e6), performance.now())
        this.send(uplinkFrame(this.stream, position, packet))
      },
      error: onError,
    })
    this.encoder.configure(encoderConfig())
  }

  push(frame: Int16Array<ArrayBuffer>, inTurn: boolean) {
    if (this.encoder.state !== 'configured') return
    const data = new AudioData({ format: 's16', sampleRate: RATE, numberOfFrames: frame.length, numberOfChannels: 1,
      timestamp: (this.fed * 1e6) / RATE, data: frame })
    this.encoder.encode(data)
    data.close()
    this.fed += frame.length
    if (this.encoder.encodeQueueSize) return // packets may still be on their way
    const silentUntil = this.quiet.due(this.fed, performance.now(), inTurn)
    if (silentUntil !== null) this.send(uplinkFrame(this.stream, silentUntil))
  }

  /** Send everything encoded so far, the partial last packet included. The stream is over after this. */
  async flush() {
    if (this.encoder.state === 'configured') await this.encoder.flush().catch(() => {})
    this.close()
  }

  close() {
    if (this.encoder.state !== 'closed') this.encoder.close()
  }
}

interface Tag<T> {
  index: number
  value: T
}

/**
 * Downlink packets → 48 kHz float audio, in order. Each packet carries a tag (its frame's
 * generation and segment), handed back with its audio.
 */
export class OpusDownlink<T> {
  private decoder: AudioDecoder
  private readonly tags: Tag<T>[] = []
  private index = 0
  private readonly onAudio: (samples: Float32Array<ArrayBuffer>, rate: number, tag: T) => void
  private readonly onError: (error: DOMException) => void

  constructor(onAudio: (samples: Float32Array<ArrayBuffer>, rate: number, tag: T) => void,
              onError: (error: DOMException) => void) {
    this.onAudio = onAudio
    this.onError = onError
    this.decoder = this.create()
  }

  private create() {
    const decoder = new AudioDecoder({
      output: (data) => {
        // Packets come out in order; the timestamp says which, in case one gave no audio.
        const index = Math.round(data.timestamp / DOWNLINK_PACKET_US)
        let tag = this.tags.shift()
        while (tag && tag.index < index && this.tags.length) tag = this.tags.shift()
        const samples = new Float32Array(data.numberOfFrames)
        data.copyTo(samples, { planeIndex: 0, format: 'f32-planar' })
        const rate = data.sampleRate
        data.close()
        if (tag) this.onAudio(samples, rate, tag.value)
      },
      error: this.onError,
    })
    decoder.configure(decoderConfig)
    return decoder
  }

  decode(packets: Uint8Array[], tag: (first: boolean) => T) {
    if (this.decoder.state !== 'configured') return
    packets.forEach((packet, i) => {
      const index = this.index++
      this.tags.push({ index, value: tag(i === 0) })
      this.decoder.decode(new EncodedAudioChunk({ type: 'key', timestamp: index * DOWNLINK_PACKET_US, data: packet }))
    })
  }

  close() {
    this.tags.length = 0
    if (this.decoder.state !== 'closed') this.decoder.close()
  }
}
