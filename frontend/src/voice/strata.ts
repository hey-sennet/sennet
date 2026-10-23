// What the disc shows, from the voice session and the task it is about (docs/voice-protocol.md §10).
// An open conversation with nobody talking (`open`) rests like `idle`; the eyebrow says it's open.
import type { StrataState } from '@/strata/field'
import type { VoiceSnapshot } from './voice'

export function strataState(voice: Pick<VoiceSnapshot, 'listening' | 'opening' | 'speaking' | 'state'>,
  { taskRunning = false, approvalPending = false }: { taskRunning?: boolean; approvalPending?: boolean } = {}): StrataState {
  // Listening shows once the microphone is really capturing: the cue to start talking.
  if (voice.listening) return voice.opening ? 'idle' : 'listening'
  // Between segments the server is still speaking while nothing plays: hold the state.
  if (voice.speaking || voice.state === 'speaking') return 'speaking'
  if (voice.state === 'transcribing') return 'thinking'
  if (approvalPending) return 'approval'
  return taskRunning ? 'thinking' : 'idle'
}
