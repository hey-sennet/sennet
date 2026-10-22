import type { AgentStatus } from '@/lib/api'

const select = 'rounded-md border bg-background px-2 py-1 text-xs text-foreground disabled:opacity-50'

/**
 * Model and effort, each with "Default": what `inherited` says (the project's choice, for a task),
 * else the agent's own. Choosing a model resets the effort to that model's default.
 */
export function ModelPicker({ agent, model, effort, inherited, onChange }: {
  agent?: AgentStatus
  model: string | null
  effort: string | null
  inherited?: { model: string | null; effort: string | null; from: string }
  onChange: (model: string | null, effort: string | null) => void
}) {
  const models = agent?.models ?? []
  const title = (id: string | null) => models.find((m) => m.id === id)?.title ?? id
  const fallback = inherited?.model
    ? `${title(inherited.model)}, as ${inherited.from}`
    : models.find((m) => m.default)?.title ?? `${agent?.title ?? 'the agent'}'s choice`
  const current = models.find((m) => m.id === (model ?? inherited?.model)) ?? (model || inherited?.model ? undefined : models.find((m) => m.default))
  const inheritedEffort = !model && inherited?.effort ? `${inherited.effort}, as ${inherited.from}` : null
  const efforts = current?.efforts ?? []
  if (!models.length) return null
  return (
    <span className="flex flex-wrap items-center gap-2">
      <select className={select} aria-label="Model" value={model ?? ''}
        onChange={(event) => onChange(event.target.value || null, null)}>
        <option value="">Default ({fallback})</option>
        {models.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}
      </select>
      {efforts.length > 0 && (
        <select className={select} aria-label="Effort" value={effort ?? ''}
          onChange={(event) => onChange(model, event.target.value || null)}>
          <option value="">Default effort{inheritedEffort ? ` (${inheritedEffort})` : current?.default_effort ? ` (${current.default_effort})` : ''}</option>
          {efforts.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
      )}
    </span>
  )
}
