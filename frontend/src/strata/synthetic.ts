// A plausible synthetic voice, for previews and tests until real audio arrives (M2).
import { BANDS, type Signal } from './field'
import type { AudioSource } from './engine'
import { clamp, noise, smoothstep } from './math'

/** About 4.5 syllables a second with phrase gaps and formant-weighted bands. */
export function syntheticVoice(character: 'user' | 'agent'): AudioSource {
  const seed = character === 'agent' ? 17 : 0
  const bands = new Float32Array(BANDS)
  return {
    read(t): Signal {
      const syllable = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 * (character === 'agent' ? 3.6 : 4.4) + 1.7 * Math.sin(t * 1.3 + seed))
      const phrase = smoothstep(0.22, 0.55, noise(t * (character === 'agent' ? 0.9 : 1.4) + seed))
      const grain = 0.7 + 0.3 * noise(t * 11 + seed * 3)
      const level = clamp(phrase * (0.3 + 0.7 * syllable ** 1.4) * grain * 1.1)
      for (let i = 0; i < BANDS; i++) {
        const x = i / BANDS
        // Two formant humps plus a falling tilt.
        const shape = 0.55 * Math.exp(-(((x - 0.12) / 0.09) ** 2)) + 0.4 * Math.exp(-(((x - 0.32) / 0.12) ** 2)) + 0.25 * (1 - x)
        bands[i] = clamp(level * shape * (0.45 + 0.75 * noise(t * 7 + i * 0.41 + seed)) * 1.6)
      }
      return { level, bands }
    },
  }
}
