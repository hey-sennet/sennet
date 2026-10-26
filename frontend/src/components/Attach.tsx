import { useEffect, useRef, useState } from 'react'
import { Popover } from '@base-ui/react/popover'
import { Camera as CameraIcon, ImagePlus, Images, X } from 'lucide-react'
import { toast } from 'sonner'
import { addImages, tray, useTray } from '@/lib/tray'
import { cn } from '@/lib/utils'
import { useVoice } from '@/voice/usePushToTalk'
import { Camera } from './Camera'

/**
 * Adds images to what you say or type next: a photo taken in Sennet's own camera, or images
 * chosen from the device. Pasting or dropping an image anywhere on the page adds it too (Dock).
 */
export function AttachButton({ className, children }: { className: string; children?: React.ReactNode }) {
  const [camera, setCamera] = useState(false)
  const choose = useRef<HTMLInputElement>(null)
  const capture = useRef<HTMLInputElement>(null) // the system's camera, when Sennet's can't open
  const take = (input: HTMLInputElement) => {
    const left = input.files && addImages(input.files)
    if (left) toast.error(left)
    input.value = '' // the same file can be chosen again
  }
  return (
    <>
      <Popover.Root>
        <Popover.Trigger aria-label="Add an image" title="Add an image" className={className}>
          {children ?? <ImagePlus />}
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Positioner side="top" align="center" sideOffset={10} className="z-50">
            <Popover.Popup className={cn('w-56 rounded-2xl border bg-popover p-1.5 text-popover-foreground shadow-lg outline-none',
              'origin-(--transform-origin) transition-[opacity,scale] duration-200 ease-strata data-starting-style:scale-95 data-starting-style:opacity-0 data-ending-style:scale-95 data-ending-style:opacity-0')}>
              <Popover.Close render={<button type="button" />} onClick={() => setCamera(true)}
                className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[14px] transition-colors hover:bg-secondary [&_svg]:size-4 [&_svg]:text-muted-foreground">
                <CameraIcon /> Take a photo
              </Popover.Close>
              <Popover.Close render={<button type="button" />} onClick={() => choose.current?.click()}
                className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-[14px] transition-colors hover:bg-secondary [&_svg]:size-4 [&_svg]:text-muted-foreground">
                <Images /> Choose images
              </Popover.Close>
              <p className="hidden px-3 pt-1 pb-1.5 text-xs text-muted-foreground [@media(pointer:fine)]:block">or paste or drop one on the page</p>
            </Popover.Popup>
          </Popover.Positioner>
        </Popover.Portal>
      </Popover.Root>
      <input ref={choose} type="file" accept="image/*" multiple hidden onChange={(event) => take(event.currentTarget)} />
      <input ref={capture} type="file" accept="image/*" capture="environment" hidden onChange={(event) => take(event.currentTarget)} />
      <Camera open={camera} onClose={() => setCamera(false)} onTaken={(photo) => tray().add([photo])}
        onFallback={() => capture.current?.click()} />
    </>
  )
}

/**
 * The images waiting to go, above the dock: each can be taken out until it's sent. They go with
 * the next message, typed or said; while you're talking they light up, as going with it.
 */
export function TrayStrip({ typing }: { typing: boolean }) {
  const items = useTray()
  const { listening } = useVoice()
  // Kept a moment once emptied, so the strip folds away rather than vanishing.
  const [shown, setShown] = useState(items)
  if (items.length > 0 && items !== shown) setShown(items)
  useEffect(() => {
    if (items.length) return
    const timer = setTimeout(() => setShown(items), 300)
    return () => clearTimeout(timer)
  }, [items])
  const open = items.length > 0
  const going = listening && items.some((item) => item.state === 'ready')
  return (
    <div aria-label="Images to send" role="group"
      className={cn('grid transition-[grid-template-rows,opacity] duration-300 ease-strata', open ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0')}>
      <div className="min-h-0 overflow-hidden">
        <div className="flex flex-col items-center gap-1.5 px-5 pt-2">
          <ul className="flex max-w-full gap-2 overflow-x-auto px-1 pt-1.5 pb-1 [scrollbar-width:none]">
            {shown.map((item) => (
              <li key={item.key} className="relative shrink-0">
                <img src={item.preview} alt="" title={item.error}
                  className={cn('size-14 rounded-xl border object-cover transition-[opacity,box-shadow] duration-300',
                    item.state === 'uploading' && 'animate-pulse opacity-60',
                    item.state === 'failed' && 'border-destructive opacity-50',
                    going && item.state === 'ready' && 'ring-2 ring-foreground ring-offset-2 ring-offset-background')} />
                <button type="button" aria-label="Take this image out" onClick={() => tray().remove(item.key)}
                  className="absolute -top-1.5 -right-1.5 grid size-5 place-items-center rounded-full border border-background bg-foreground text-background [&_svg]:size-3">
                  <X />
                </button>
              </li>
            ))}
          </ul>
          {!typing && (
            <p className={cn('text-xs transition-colors duration-300', going ? 'text-foreground' : 'text-muted-foreground')} aria-live="polite">
              {shown.some((item) => item.state === 'failed') ? 'An image couldn’t be added: tap × to take it out'
                : going ? 'Going with what you’re saying' : 'Going with what you say or type next'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
