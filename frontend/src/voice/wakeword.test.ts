/// <reference types="node" />
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { CHUNK_SAMPLES, Trigger, WakeWord, type WakeModels } from './wakeword'
import { loadModels } from './wakeword-models'

/** Models that count their calls and score by how loud the newest mel frame is. */
function fakeModels() {
  const calls = { mel: [] as number[], embed: 0, score: 0 }
  const models: WakeModels = {
    mel: async (samples) => {
      calls.mel.push(samples.length)
      return new Float32Array((samples.length / 160 - 3) * 32).fill(samples[samples.length - 1])
    },
    embed: async (window) => {
      calls.embed++
      return new Float32Array(96).fill(window[window.length - 1])
    },
    score: async (features) => {
      calls.score++
      return features[features.length - 1] > 2 ? 1 : 0
    },
  }
  return { models, calls }
}

describe('WakeWord', () => {
  it('scores each 80 ms, with the mel model given the 30 ms before it after the first', async () => {
    const { models, calls } = fakeModels()
    const wake = new WakeWord(models)
    await wake.reset(new Int16Array(16000 * 4))
    calls.mel.length = 0
    const scores = []
    for (let i = 0; i < 16; i++) scores.push(...await wake.push(new Int16Array(320))) // 20 ms frames
    expect(scores).toHaveLength(4)
    expect(calls.mel).toEqual([CHUNK_SAMPLES, CHUNK_SAMPLES + 480, CHUNK_SAMPLES + 480, CHUNK_SAMPLES + 480])
  })

  it('stays at zero for the first five scores, as openWakeWord does', async () => {
    const { models } = fakeModels()
    const wake = new WakeWord(models)
    await wake.reset(new Int16Array(16000 * 4))
    const loud = new Int16Array(CHUNK_SAMPLES * 7).fill(1000)
    expect(await wake.push(loud)).toEqual([0, 0, 0, 0, 0, 1, 1])
  })
})

describe('Trigger', () => {
  it('fires once, then waits two seconds of scores before it can fire again', () => {
    const trigger = new Trigger(0.5, 25)
    const fired = Array.from({ length: 30 }, () => trigger.hit(0.9))
    expect(fired.filter(Boolean)).toHaveLength(2)
    expect(fired[0]).toBe(true)
    expect(fired[26]).toBe(true)
  })
})

// The real models against openWakeWord's own scores (training/wakeword/reference.py). Neither the
// models nor the fixture are committed, so this runs where setup has fetched them and the fixture is made.
const modelDir = path.join(process.env.SENNET_HOME ?? path.join(homedir(), '.local/share/sennet'), 'models/openwakeword-v0.5.1')
const fixture = path.join(import.meta.dirname, 'fixtures/wakeword.json')
const real = existsSync(path.join(modelDir, 'hey_jarvis_v0.1.onnx')) && existsSync(fixture)

describe.skipIf(!real)('WakeWord with the real models', () => {
  const read = (name: string) => readFileSync(path.join(modelDir, name)).buffer as ArrayBuffer
  const reference = real ? JSON.parse(readFileSync(fixture, 'utf8')) : null

  it.each(['hey_jarvis', 'negative'])('scores the %s clip as openWakeWord does', async (name) => {
    const { audio, scores } = reference.clips[name] as { audio: number[]; scores: number[] }
    const models = await loadModels({ mel: read('melspectrogram.onnx'), embed: read('embedding_model.onnx'),
      wake: read('hey_jarvis_v0.1.onnx') })
    const wake = new WakeWord(models.models, models.inputs)
    await wake.reset(Int16Array.from({ length: 16000 * 4 }, (_, i) => ((i * 7919) % 2000) - 1000))
    const ours: number[] = []
    for (let i = 0; i < audio.length; i += 320) ours.push(...await wake.push(Int16Array.from(audio.slice(i, i + 320))))
    expect(ours).toHaveLength(scores.length)
    for (let i = 0; i < scores.length; i++) expect(ours[i]).toBeCloseTo(scores[i], 3)
  }, 30_000)
})
