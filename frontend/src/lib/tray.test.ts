import { describe, expect, it, vi } from 'vitest'
import { fitted } from './images'
import { MAX_IMAGES, Tray } from './tray'
import type { Attachment } from './api'

const image = (id: number): Attachment => ({ id, media_type: 'image/jpeg', width: 10, height: 10 })

function harness() {
  let ids = 0
  const uploads: { resolve: (a: Attachment) => void; reject: (e: Error) => void }[] = []
  const steps = {
    shrink: vi.fn(async (blob: Blob) => blob),
    upload: vi.fn(() => new Promise<Attachment>((resolve, reject) => uploads.push({ resolve, reject }))),
    remove: vi.fn(async () => {}),
    url: vi.fn(() => `blob:${++ids}`),
    revoke: vi.fn(),
  }
  return { tray: new Tray(steps), steps, uploads }
}

const settle = () => new Promise((resolve) => setTimeout(resolve))

describe('the tray', () => {
  it('uploads what is added and offers only the uploaded ones to send', async () => {
    const { tray, uploads } = harness()
    tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    expect(tray.uploading).toBe(true)
    expect(tray.ids()).toEqual([])
    uploads[1].resolve(image(8))
    await settle()
    expect(tray.ids()).toEqual([8])
    uploads[0].resolve(image(7))
    await settle()
    expect(tray.ids()).toEqual([7, 8]) // in the order they were added
    expect(tray.uploading).toBe(false)
  })

  it('forgets on the server an image taken out, even one still uploading', async () => {
    const { tray, steps, uploads } = harness()
    tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    uploads[0].resolve(image(1))
    await settle()
    const [first, second] = tray.get()
    tray.remove(first.key)
    expect(steps.remove).toHaveBeenCalledWith(1)
    tray.remove(second.key)
    uploads[1].resolve(image(2))
    await settle()
    expect(steps.remove).toHaveBeenCalledWith(2)
    expect(tray.get()).toEqual([])
  })

  it('lets go of what was sent, and keeps the rest', async () => {
    const { tray, steps, uploads } = harness()
    tray.add([new Blob(['a']), new Blob(['b'])])
    await settle()
    uploads.forEach((upload, i) => upload.resolve(image(i + 1)))
    await settle()
    tray.sent([1])
    expect(tray.ids()).toEqual([2])
    expect(steps.remove).not.toHaveBeenCalled() // it stays on the server with its message
  })

  it('marks an image that failed, and sends without it', async () => {
    const { tray, uploads } = harness()
    tray.add([new Blob(['a'])])
    await settle()
    uploads[0].reject(new Error('that image is over 10 MB'))
    await settle()
    expect(tray.get()[0]).toMatchObject({ state: 'failed', error: 'that image is over 10 MB' })
    expect(tray.ids()).toEqual([])
  })

  it(`takes up to ${MAX_IMAGES}`, () => {
    const { tray } = harness()
    expect(tray.add(Array.from({ length: MAX_IMAGES + 2 }, () => new Blob(['x'])))).toBe(2)
    expect(tray.get()).toHaveLength(MAX_IMAGES)
  })
})

describe('shrinking', () => {
  it('scales the long side down to 1600 and leaves a small image as it is', () => {
    expect(fitted(4032, 3024)).toEqual({ width: 1600, height: 1200 })
    expect(fitted(1170, 2532)).toEqual({ width: 739, height: 1600 })
    expect(fitted(800, 600)).toEqual({ width: 800, height: 600 })
  })
})
