// Images are shrunk on the device before they're sent (src/sennet/attachments.py): a phone's
// photo is several MB, and the agent reads a long side of about 1600 px just as well.

export const LONG_SIDE = 1600
const JPEG_QUALITY = 0.85
// A screenshot stays a PNG, which keeps text sharp, unless that comes out bigger than this.
const PNG_LIMIT = 1.5 * 1024 * 1024

/** The size to draw an image at: its own, or scaled down to `longSide` on its longer side. */
export function fitted(width: number, height: number, longSide = LONG_SIDE): { width: number; height: number } {
  const scale = Math.min(1, longSide / Math.max(width, height))
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) }
}

/** The image, shrunk and re-encoded: a PNG as a PNG when that stays small, anything else a JPEG.
 *  Re-encoding also leaves out what the file carried besides the picture, such as where a photo was taken. */
export async function shrink(image: Blob): Promise<Blob> {
  const bitmap = await createImageBitmap(image, { imageOrientation: 'from-image' })
  const size = fitted(bitmap.width, bitmap.height)
  const canvas = document.createElement('canvas')
  canvas.width = size.width
  canvas.height = size.height
  const context = canvas.getContext('2d')!
  if (image.type === 'image/png') {
    context.drawImage(bitmap, 0, 0, size.width, size.height)
    const png = await encode(canvas, 'image/png')
    if (png.size <= PNG_LIMIT) {
      bitmap.close()
      return png
    }
    context.clearRect(0, 0, size.width, size.height)
  }
  // JPEG has no transparency: what was transparent turns white, not black.
  context.fillStyle = '#fff'
  context.fillRect(0, 0, size.width, size.height)
  context.drawImage(bitmap, 0, 0, size.width, size.height)
  bitmap.close()
  return encode(canvas, 'image/jpeg', JPEG_QUALITY)
}

function encode(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Couldn't read that image"))), type, quality))
}
