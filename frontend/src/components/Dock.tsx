import { useEffect, useRef, useState } from 'react'
import { ArrowUp, AudioLines, ImagePlus, Keyboard, ListTodo, Plus, Volume2, VolumeOff } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { addImages, tray, useTray } from '@/lib/tray'
import { cn } from '@/lib/utils'
import { AttachButton, TrayStrip } from './Attach'
import { useVoice } from '@/voice/usePushToTalk'
import { voice, type Target } from '@/voice/voice'

const glyph = 'grid size-11 shrink-0 place-items-center rounded-full border border-border text-muted-foreground transition-[color,border-color,transform] duration-300 ease-strata hover:text-foreground active:scale-94 [&_svg]:size-5'

/**
 * Under the stage: on the left the new-task glyph; in the middle the conversation glyph, the mute
 * glyph, the image glyph and a keyboard glyph that grows into a type bar; on the right, on phones,
 * the tasks glyph that opens the sheet. A reply is stopped from the disc, and the wake word is a
 * setting. Images waiting to go sit above it all (TrayStrip).
 */
export function Dock({ typing, onTypingChange, placeholder, onSend, disabled, images, target, onNewTask, activeTasks, needsYou, replyElsewhere, onOpenTasks, className }: {
  typing: boolean
  onTypingChange: (typing: boolean) => void
  placeholder: string
  onSend: (text: string, attachments: number[]) => Promise<unknown>
  disabled?: boolean
  images: boolean // the agent your words go to takes images
  target: Target // where a conversation's turns go
  onNewTask?: () => void // set while following up a task: what's said next starts a new one instead
  activeTasks: number
  needsYou: boolean
  replyElsewhere: boolean // a task not on screen has a reply that wasn't spoken
  onOpenTasks?: () => void
  className?: string
}) {
  const { conversation, stt, turnReady, muted } = useVoice()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const pending = useTray()
  const attached = pending.some((item) => item.state === 'ready')
  const uploading = pending.some((item) => item.state === 'uploading')

  // An image pasted, or dropped anywhere on the page, joins the tray; a pasted text is left alone.
  useEffect(() => {
    if (!images || disabled) return
    const files = (data: DataTransfer | null) => [...(data?.files ?? [])].filter((file) => file.type.startsWith('image/'))
    const onPaste = (event: ClipboardEvent) => {
      const found = files(event.clipboardData)
      if (!found.length) return
      event.preventDefault()
      const left = addImages(found)
      if (left) toast.error(left)
    }
    const onDragOver = (event: DragEvent) => {
      if (event.dataTransfer?.types.includes('Files')) event.preventDefault()
    }
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer?.types.includes('Files')) return
      event.preventDefault()
      const left = addImages(files(event.dataTransfer))
      if (left) toast.error(left)
    }
    window.addEventListener('paste', onPaste)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('drop', onDrop)
    return () => {
      window.removeEventListener('paste', onPaste)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('drop', onDrop)
    }
  }, [images, disabled])

  useEffect(() => {
    if (typing) input.current?.focus()
  }, [typing])

  // Typing a request takes longer than starting an agent: start it as the type bar opens.
  const targetKey = JSON.stringify(target)
  useEffect(() => {
    if (typing && Object.keys(target).length) api.prewarm(target).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per target, not per render
  }, [typing, targetKey])

  const ready = (text.trim() || attached) && !uploading && !busy && !disabled
  const send = async () => {
    if (!ready) return
    setBusy(true)
    try {
      const ids = tray().ids()
      await onSend(text.trim(), ids)
      tray().sent(ids)
      setText('')
      if (input.current) input.current.style.height = ''
      onTypingChange(false)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={className}>
      <TrayStrip typing={typing} />
      <div className="relative flex items-center gap-3 px-5 pt-2 pb-[max(18px,env(safe-area-inset-bottom))] lg:pb-7">
        {/* Equal sides keep the middle pair centred; typing, the type bar takes the room instead. */}
        <div className={cn('flex items-center gap-3', !typing && 'flex-1 basis-0')}>
          {!typing && (
            <button type="button" className={cn(glyph, !onNewTask && 'pointer-events-none opacity-40')}
              aria-label="New task" title="New task (N)" disabled={!onNewTask} onClick={onNewTask}>
              <Plus />
            </button>
          )}
        </div>
  
        <div className={cn('flex min-w-0 items-center gap-3', typing && 'flex-1 justify-center')}>
          {stt === 'ready' && turnReady && !typing && (
            <button type="button" aria-pressed={conversation}
              className={cn(glyph, conversation && 'border-foreground bg-foreground text-background hover:text-background')}
              aria-label={conversation ? 'End the conversation' : 'Start a conversation: just talk, no button'}
              title={conversation ? 'End the conversation' : 'Start a conversation: just talk, no button'}
              onClick={() => (conversation ? voice().closeConversation() : void voice().openConversation(target))}>
              <AudioLines />
            </button>
          )}
          {!typing && (
            // Replies still come, captioned, and play on unheard: unmuting picks up where they've got to.
            <button type="button" aria-pressed={muted}
              className={cn(glyph, muted && 'border-foreground text-foreground')}
              aria-label={muted ? 'Unmute replies' : 'Mute replies'} title={muted ? 'Unmute replies' : 'Mute replies'}
              onClick={() => voice().setMuted(!muted)}>
              {muted ? <VolumeOff /> : <Volume2 />}
            </button>
          )}
          {images && !typing && !disabled && <AttachButton className={glyph} />}
          <form
            className={cn('flex items-end overflow-hidden rounded-[22px] border transition-[width,background-color,border-color] duration-600 ease-strata',
              typing ? 'min-h-11 w-[min(100%,560px)] min-w-0 border-border bg-card' : 'h-11 w-11 shrink-0 border-transparent')}
            onSubmit={(event) => {
              event.preventDefault()
              void send()
            }}
          >
            <button type="button" className={cn(glyph, typing && 'border-transparent text-foreground')}
              aria-label={typing ? 'Close typing' : 'Type instead'} title={typing ? 'Close typing (Esc)' : 'Type instead (/)'} aria-expanded={typing}
              onClick={() => onTypingChange(!typing)}>
              <Keyboard />
            </button>
            <textarea
              ref={input}
              value={text}
              rows={1}
              tabIndex={typing ? 0 : -1}
              aria-hidden={!typing}
              onChange={(event) => {
                setText(event.target.value)
                // Grows with what's typed, to about four lines, then scrolls, without a scrollbar.
                event.target.style.height = ''
                event.target.style.height = `${Math.min(event.target.scrollHeight, 104)}px`
              }}
              onKeyDown={(event) => {
                if (event.key === 'Escape') onTypingChange(false)
                // Enter sends; Shift+Enter starts a new line.
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  void send()
                }
              }}
              placeholder={placeholder}
              className={cn('h-[42px] min-w-0 flex-1 resize-none self-center bg-transparent px-1 py-[10px] text-[15px] leading-[22px] outline-none placeholder:text-muted-foreground',
                '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
                'transition-opacity delay-150 duration-300', typing ? 'opacity-100' : 'pointer-events-none opacity-0')}
            />
            {images && !disabled && typing && (
              // Only while typing: closed, the bar is just its glyph, and the dock has its own image glyph.
              <AttachButton className="mb-[5px] grid size-[34px] shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:text-foreground animate-in fade-in-0 fill-mode-backwards delay-200 duration-450 ease-strata [&_svg]:size-[18px]">
                <ImagePlus />
              </AttachButton>
            )}
            <button type="submit" aria-label={uploading ? 'Send, once the images are added' : 'Send'} disabled={!typing || !ready}
              className={cn('mr-1 mb-[5px] grid size-[34px] shrink-0 place-items-center rounded-full bg-foreground text-background transition-[opacity,transform] delay-200 duration-450 ease-strata disabled:bg-foreground/40',
                typing ? 'opacity-100' : 'pointer-events-none scale-60 opacity-0')}>
              <ArrowUp className="size-4" />
            </button>
          </form>
        </div>
  
        <div className={cn('flex items-center justify-end gap-3', !typing && 'flex-1 basis-0')}>
          {onOpenTasks && (
            <button type="button" className={cn(glyph, 'relative', typing && 'pointer-events-none absolute right-5 opacity-0')}
              aria-label="Tasks" title="Tasks" onClick={onOpenTasks}>
              {activeTasks ? <span className="text-[13px] font-medium tabular-nums">{activeTasks}</span> : <ListTodo />}
              {(needsYou || replyElsewhere) && (
                <span className={cn('absolute top-0.5 right-0.5 size-2.5 rounded-full border-2 border-background', needsYou ? 'bg-warn' : 'bg-agent')} />
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
