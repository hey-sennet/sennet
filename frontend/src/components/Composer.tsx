import { useState, type ReactNode } from 'react'
import { ArrowUp } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

/** Text input that sends on Enter (Shift+Enter for a new line). Voice input arrives in M2. */
export function Composer({ placeholder, onSend, disabled, children, autoFocus }: {
  placeholder: string
  onSend: (text: string) => Promise<unknown>
  disabled?: boolean
  children?: ReactNode
  autoFocus?: boolean
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async () => {
    if (!text.trim() || busy || disabled) return
    setBusy(true)
    try {
      await onSend(text.trim())
      setText('')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-2xl border bg-card p-2 focus-within:border-ring/60">
      <Textarea
        value={text}
        autoFocus={autoFocus}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault()
            void send()
          }
        }}
        placeholder={placeholder}
        rows={2}
        className="max-h-60 min-h-12 resize-none border-0 bg-transparent px-2 text-[15px] shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center justify-between gap-2 pl-1">
        <div className="min-w-0 flex-1">{children}</div>
        <Button size="icon" className="rounded-full" aria-label="Send" disabled={!text.trim() || busy || disabled} onClick={send}>
          <ArrowUp />
        </Button>
      </div>
    </div>
  )
}
