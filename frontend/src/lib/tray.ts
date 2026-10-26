// Images waiting to go with the next message, typed or spoken: shrunk and uploaded as soon as
// they're added, so sending names them by id (src/sennet/attachments.py). One tray for the page,
// whichever task is on screen: what you add goes with what you say or type next.
import { useSyncExternalStore } from 'react'
import { api, type Attachment } from './api'
import { shrink } from './images'

export const MAX_IMAGES = 8 // the server's MAX_PER_MESSAGE

export interface Pending {
  key: number
  preview: string // an object URL of the shrunk image, or the original while it shrinks
  state: 'uploading' | 'ready' | 'failed'
  attachment?: Attachment
  error?: string
}

interface Steps {
  shrink: (image: Blob) => Promise<Blob>
  upload: (image: Blob) => Promise<Attachment>
  remove: (id: number) => Promise<void>
  url: (image: Blob) => string
  revoke: (url: string) => void
}

export class Tray {
  private items: readonly Pending[] = []
  private keys = 0
  private readonly listeners = new Set<() => void>()
  private readonly steps: Steps

  constructor(steps: Steps = {
    shrink, upload: api.uploadImage, remove: api.removeAttachment,
    url: (image) => URL.createObjectURL(image), revoke: (url) => URL.revokeObjectURL(url),
  }) {
    this.steps = steps
  }

  readonly get = () => this.items

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => void this.listeners.delete(listener)
  }

  /** Ids of the images ready to send, in the order they were added. */
  ids(): number[] {
    return this.items.flatMap((item) => (item.state === 'ready' && item.attachment ? [item.attachment.id] : []))
  }

  get uploading(): boolean {
    return this.items.some((item) => item.state === 'uploading')
  }

  /** Add images; past MAX_IMAGES the rest are left out, and the count of those returned. */
  add(images: Blob[]): number {
    const room = Math.max(0, MAX_IMAGES - this.items.length)
    for (const image of images.slice(0, room)) void this.upload(image)
    return images.length - Math.min(images.length, room)
  }

  private async upload(image: Blob) {
    const key = ++this.keys
    this.set([...this.items, { key, preview: this.steps.url(image), state: 'uploading' }])
    try {
      const shrunk = await this.steps.shrink(image)
      if (!this.items.some((item) => item.key === key)) return // taken out while it shrank
      this.change(key, { preview: this.steps.url(shrunk) })
      const attachment = await this.steps.upload(shrunk)
      if (!this.items.some((item) => item.key === key)) {
        void this.steps.remove(attachment.id).catch(() => {}) // taken out while it uploaded
        return
      }
      this.change(key, { state: 'ready', attachment })
    } catch (error) {
      this.change(key, { state: 'failed', error: error instanceof Error ? error.message : "Couldn't add that image" })
    }
  }

  /** Take one out; the server forgets it too. */
  remove(key: number) {
    const item = this.items.find((i) => i.key === key)
    if (!item) return
    this.set(this.items.filter((i) => i !== item))
    this.steps.revoke(item.preview)
    if (item.attachment) void this.steps.remove(item.attachment.id).catch(() => {})
  }

  /** These went with a message: out of the tray, kept on the server with it. */
  sent(ids: number[]) {
    const gone = this.items.filter((item) => item.attachment && ids.includes(item.attachment.id))
    if (!gone.length) return
    this.set(this.items.filter((item) => !gone.includes(item)))
    gone.forEach((item) => this.steps.revoke(item.preview))
  }

  private change(key: number, fields: Partial<Pending>) {
    const item = this.items.find((i) => i.key === key)
    if (!item) return
    if (fields.preview && fields.preview !== item.preview) this.steps.revoke(item.preview)
    this.set(this.items.map((i) => (i === item ? { ...i, ...fields } : i)))
  }

  private set(items: readonly Pending[]) {
    this.items = items
    this.listeners.forEach((listener) => listener())
  }
}

let shared: Tray | null = null

export function tray(): Tray {
  shared ??= new Tray()
  return shared
}

export function useTray(): readonly Pending[] {
  const t = tray()
  return useSyncExternalStore(t.subscribe, t.get)
}

/** Images into the tray (anything that isn't an image is left out); what didn't fit, said for a toast. */
export function addImages(files: Iterable<File>): string | null {
  const left = tray().add([...files].filter((file) => file.type.startsWith('image/')))
  return left ? `A message takes up to ${MAX_IMAGES} images; ${left === 1 ? 'one was' : `${left} were`} left out` : null
}
