import { Popover } from '@base-ui/react/popover'
import { Check, Sparkles } from 'lucide-react'
import type { AgentStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { describeChoice, type Inherited } from './ModelPicker'

const row = 'flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-secondary'
const chip = 'rounded-full border px-2.5 py-1 text-xs transition-colors hover:text-foreground'

/**
 * The dock's model glyph and the popover it opens: the model and effort for the selected task, or
 * for the next one started. A dot on the glyph marks a choice of its own.
 */
export function ModelMenu({ agent, model, effort, inherited, heading, note, onChange, className }: {
  agent?: AgentStatus
  model: string | null
  effort: string | null
  inherited?: Inherited
  heading: string
  note?: string
  onChange: (model: string | null, effort: string | null) => void
  className?: string
}) {
  const { models, fallback, current, effortFallback } = describeChoice(agent, model, inherited)
  if (!models.length) return null
  const efforts = current?.efforts ?? []
  const label = `Model: ${model ? current?.title ?? model : `Default (${fallback})`}${effort ? `, ${effort} effort` : ''}`
  return (
    <Popover.Root>
      <Popover.Trigger aria-label={label} title={label} className={cn('relative', className)}>
        <Sparkles />
        {(model || effort) && <span className="absolute top-0.5 right-0.5 size-2.5 rounded-full border-2 border-background bg-foreground/70" />}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="top" sideOffset={10} className="z-50">
          <Popover.Popup className={cn('w-[min(288px,calc(100vw-32px))] rounded-2xl border bg-popover p-2 text-popover-foreground shadow-lg outline-none',
            'origin-(--transform-origin) transition-[opacity,scale] duration-200 ease-strata data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:scale-95 data-ending-style:opacity-0')}>
            <Popover.Title className="eyebrow px-2.5 pt-1.5 pb-1 text-muted-foreground">{heading}</Popover.Title>
            <div role="radiogroup" aria-label="Model">
              <Option checked={!model} onSelect={() => onChange(null, null)}>
                Default <span className="text-muted-foreground">· {fallback}</span>
              </Option>
              {models.map((m) => (
                <Option key={m.id} checked={model === m.id} onSelect={() => onChange(m.id, null)}>{m.title}</Option>
              ))}
            </div>
            {efforts.length > 0 && (
              <>
                <p className="eyebrow mt-2 border-t px-2.5 pt-3 pb-2 text-muted-foreground">Effort</p>
                <div role="radiogroup" aria-label="Effort" className="flex flex-wrap gap-1.5 px-2.5 pb-1.5">
                  {[null, ...efforts].map((e) => (
                    <button key={e ?? ''} type="button" role="radio" aria-checked={effort === e}
                      title={e === null && effortFallback ? `Default: ${effortFallback}` : undefined}
                      className={cn(chip, effort === e ? 'border-foreground bg-foreground text-background hover:text-background' : 'text-muted-foreground')}
                      onClick={() => onChange(model, e)}>
                      {e ?? 'Default'}
                    </button>
                  ))}
                </div>
              </>
            )}
            {note && <p className="px-2.5 pt-1 pb-1.5 text-xs text-muted-foreground">{note}</p>}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}

function Option({ checked, onSelect, children }: { checked: boolean; onSelect: () => void; children: React.ReactNode }) {
  return (
    <button type="button" role="radio" aria-checked={checked} className={row} onClick={onSelect}>
      <span className="min-w-0 truncate">{children}</span>
      <Check className={cn('size-3.5 shrink-0', !checked && 'invisible')} />
    </button>
  )
}
