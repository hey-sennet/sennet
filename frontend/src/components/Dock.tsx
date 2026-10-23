import { type ComponentProps, useEffect, useRef, useState } from 'react'
import { ArrowUp, Keyboard, ListTodo, Square } from 'lucide-react'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'
import { useVoice } from '@/voice/usePushToTalk'
import { voice } from '@/voice/voice'
import { ModelMenu } from './ModelMenu'

const glyph = 'grid size-11 shrink-0 place-items-center rounded-full border border-border text-muted-foreground transition-[color,border-color,transform] duration-300 ease-strata hover:text-foreground active:scale-94 [&_svg]:size-5'

/**
 * Under the stage: a keyboard glyph that grows into a type bar, beside it the model glyph, stop
 * while a reply plays, and on phones the tasks glyph that opens the sheet.
 */
export function Dock({ typing, onTypingChange, placeholder, onSend, disabled, model, activeTasks, needsYou, onOpenTasks, className }: {
  typing: boolean
  onTypingChange: (typing: boolean) => void
  placeholder: string
  onSend: (text: string) => Promise<unknown>
  disabled?: boolean
  model?: Omit<ComponentProps<typeof ModelMenu>, 'className'>
  activeTasks: number
  needsYou: boolean
  onOpenTasks?: () => void
  className?: string
}) {
  const { speaking } = useVoice()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (typing) input.current?.focus()
  }, [typing])

  const send = async () => {
    if (!text.trim() || busy || disabled) return
    setBusy(true)
    try {
      await onSend(text.trim())
      setText('')
      onTypingChange(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={cn('relative flex items-center gap-3 px-5 pt-2 pb-[max(18px,env(safe-area-inset-bottom))] lg:justify-center lg:pb-7',
      typing ? 'justify-center' : 'justify-between', className)}>
      <div className={cn('flex min-w-0 items-center gap-3', typing && 'flex-1 justify-center')}>
        <form
          className={cn('flex h-11 items-center overflow-hidden rounded-[22px] border transition-[width,background-color,border-color] duration-600 ease-strata',
            typing ? 'w-[min(100%,560px)] min-w-0 border-border bg-card' : 'w-11 shrink-0 border-transparent')}
          onSubmit={(event) => {
            event.preventDefault()
            void send()
          }}
        >
          <button type="button" className={cn(glyph, typing && 'border-transparent text-foreground')}
            aria-label={typing ? 'Close typing' : 'Type instead'} title={typing ? 'Close typing' : 'Type instead'} aria-expanded={typing}
            onClick={() => onTypingChange(!typing)}>
            <Keyboard />
          </button>
          <input
            ref={input}
            value={text}
            tabIndex={typing ? 0 : -1}
            aria-hidden={!typing}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') onTypingChange(false)
            }}
            placeholder={placeholder}
            className={cn('h-full min-w-0 flex-1 bg-transparent px-1 text-[15px] outline-none placeholder:text-muted-foreground',
              'transition-opacity delay-150 duration-300', typing ? 'opacity-100' : 'pointer-events-none opacity-0')}
          />
          <button type="submit" aria-label="Send" disabled={!typing || !text.trim() || busy || disabled}
            className={cn('mr-1 grid size-[34px] shrink-0 place-items-center rounded-full bg-foreground text-background transition-[opacity,transform] delay-200 duration-450 ease-strata disabled:bg-foreground/40',
              typing ? 'opacity-100' : 'pointer-events-none scale-60 opacity-0')}>
            <ArrowUp className="size-4" />
          </button>
        </form>
        {model && <ModelMenu {...model} className={glyph} />}
      </div>

      {speaking && !typing && (
        <button type="button" className={glyph}
          aria-label="Stop speaking" title="Stop speaking" onClick={() => voice().stop()}>
          <Square className="!size-4" />
        </button>
      )}

      {onOpenTasks && (
        <button type="button" className={cn(glyph, 'relative', typing && 'pointer-events-none absolute right-5 opacity-0')}
          aria-label="Tasks" title="Tasks" onClick={onOpenTasks}>
          {activeTasks ? <span className="text-[13px] font-medium tabular-nums">{activeTasks}</span> : <ListTodo />}
          {needsYou && <span className="absolute top-0.5 right-0.5 size-2.5 rounded-full border-2 border-background bg-warn" />}
        </button>
      )}
    </div>
  )
}
