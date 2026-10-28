import type { AgentStatus, ModelOption } from '@/lib/api'
import { Choice } from './Choice'

export type Inherited = { model: string | null; effort: string | null; from: string }

/**
 * What "Default" means for a choice: what `inherited` says (the project's choice, for a task),
 * else the agent's own. `current` is the model in use, whose efforts are on offer.
 */
export function describeChoice(agent: AgentStatus | undefined, model: string | null, inherited?: Inherited) {
  const models = agent?.models ?? []
  const title = (id: string | null) => models.find((m) => m.id === id)?.title ?? id
  const fallback = inherited?.model
    ? `${title(inherited.model)}, as ${inherited.from}`
    : models.find((m) => m.default)?.title ?? `${agent?.title ?? 'the agent'}'s choice`
  const current: ModelOption | undefined = models.find((m) => m.id === (model ?? inherited?.model))
    ?? (model || inherited?.model ? undefined : models.find((m) => m.default))
  const effortFallback = !model && inherited?.effort ? `${inherited.effort}, as ${inherited.from}` : current?.default_effort ?? null
  return { models, fallback, current, effortFallback }
}

/** Model and effort as two menus, each with "Default"; choosing a model resets the effort to its default. */
export function ModelPicker({ agent, model, effort, inherited, onChange }: {
  agent?: AgentStatus
  model: string | null
  effort: string | null
  inherited?: Inherited
  onChange: (model: string | null, effort: string | null) => void
}) {
  const { models, fallback, current, effortFallback } = describeChoice(agent, model, inherited)
  const efforts = current?.efforts ?? []
  if (!models.length) return null
  return (
    <>
      <Choice label="Model" value={model ?? ''} onChange={(value) => onChange(value || null, null)}
        options={[{ value: '', label: `Default (${fallback})` }, ...models.map((m) => ({ value: m.id, label: m.title }))]} />
      {efforts.length > 0 && (
        <Choice label="Effort" value={effort ?? ''} onChange={(value) => onChange(model, value || null)} className="w-auto"
          options={[{ value: '', label: `Default effort${effortFallback ? ` (${effortFallback})` : ''}` }, ...efforts.map((e) => ({ value: e, label: e }))]} />
      )}
    </>
  )
}
