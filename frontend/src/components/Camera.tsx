import { useEffect, useRef, useState } from 'react'
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog'
import { SwitchCamera, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogOverlay, DialogPortal, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

type Facing = 'environment' | 'user'

/**
 * Takes a photo inside Sennet: full screen on a phone, a card on a wider screen. Sennet's own
 * rather than the phone's camera app, which would put the page in the background and so end a
 * conversation's microphone; here you can keep talking about what you're pointing at. Where the
 * camera can't be opened (no permission, no camera), `onFallback` offers the system's picker instead.
 */
export function Camera({ open, onClose, onTaken, onFallback }: {
  open: boolean
  onClose: () => void
  onTaken: (photo: ImageBitmap) => void // the frame as the camera gave it, not yet encoded
  onFallback: () => void
}) {
  const video = useRef<HTMLVideoElement>(null)
  const [facing, setFacing] = useState<Facing>('environment')
  const [cameras, setCameras] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [live, setLive] = useState(false)
  const [flash, setFlash] = useState(false)

  useEffect(() => {
    if (!open) return
    let stream: MediaStream | null = null
    let current = true
    setError(null)
    setLive(false)
    setFlash(false)
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('This browser can’t open the camera here.')
      return
    }
    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1920 } }, audio: false,
    }).then(async (opened) => {
      if (!current) return opened.getTracks().forEach((track) => track.stop())
      stream = opened
      if (video.current) {
        video.current.srcObject = opened
        await video.current.play().catch(() => {})
      }
      setLive(true)
      // Labels and the count are only known once a camera is allowed.
      const devices = await navigator.mediaDevices.enumerateDevices()
      if (current) setCameras(devices.filter((d) => d.kind === 'videoinput').length)
    }, (reason: unknown) => {
      if (!current) return
      const name = reason instanceof DOMException ? reason.name : ''
      setError(name === 'NotAllowedError' ? 'Sennet isn’t allowed to use the camera.'
        : name === 'NotFoundError' || name === 'OverconstrainedError' ? 'There’s no camera to use.'
        : 'The camera couldn’t be opened.')
    })
    return () => {
      current = false
      stream?.getTracks().forEach((track) => track.stop())
    }
  }, [open, facing])

  // The frame goes as it is: encoded once, at the size it is sent, rather than saved here first.
  const take = async () => {
    const v = video.current
    if (!v || !v.videoWidth) return
    setFlash(true)
    try {
      onTaken(await createImageBitmap(v))
    } finally {
      onClose()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogPortal>
        <DialogOverlay forceRender className="bg-black/60 duration-300 ease-strata" />
        <DialogPrimitive.Popup
          className="fixed inset-0 z-50 flex flex-col bg-black text-white outline-none duration-300 ease-strata
            data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0
            sm:inset-auto sm:top-1/2 sm:left-1/2 sm:w-[min(640px,calc(100%-2rem))] sm:-translate-x-1/2 sm:-translate-y-1/2
            sm:overflow-hidden sm:rounded-[28px] sm:data-open:zoom-in-95 sm:data-closed:zoom-out-95">
          <DialogTitle className="sr-only">Take a photo</DialogTitle>
          <div className="relative min-h-0 flex-1 sm:aspect-[4/3] sm:flex-none">
            <video ref={video} playsInline muted autoPlay aria-label="What the camera sees"
              className={cn('size-full object-cover sm:object-contain', facing === 'user' && '-scale-x-100',
                'transition-opacity duration-300', live ? 'opacity-100' : 'opacity-0')} />
            <span aria-hidden className={cn('pointer-events-none absolute inset-0 bg-white transition-opacity duration-200',
              flash ? 'opacity-80' : 'opacity-0')} />
            {error && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-5 px-8 text-center">
                <p className="font-serif text-[22px] leading-[1.25]">{error}</p>
                <Button variant="secondary" className="h-11 rounded-full px-5" onClick={() => {
                  onClose()
                  onFallback()
                }}>Choose a photo instead</Button>
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center justify-between px-8 pt-5 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
            <button type="button" aria-label="Close the camera" onClick={onClose}
              className="grid size-11 place-items-center rounded-full text-white/80 hover:text-white [&_svg]:size-6">
              <X />
            </button>
            <button type="button" aria-label="Take the photo" disabled={!live} onClick={() => void take()}
              className="grid size-[72px] place-items-center rounded-full border-4 border-white transition-transform active:scale-92 disabled:opacity-40">
              <span className="size-[56px] rounded-full bg-white" />
            </button>
            <button type="button" aria-label="Switch camera" onClick={() => setFacing(facing === 'environment' ? 'user' : 'environment')}
              className={cn('grid size-11 place-items-center rounded-full text-white/80 hover:text-white [&_svg]:size-6',
                cameras < 2 && 'invisible')}>
              <SwitchCamera />
            </button>
          </div>
        </DialogPrimitive.Popup>
      </DialogPortal>
    </Dialog>
  )
}
