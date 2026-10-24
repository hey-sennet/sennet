import { useEffect, useState } from 'react'

// A phone's keyboard takes at least this much of the screen; less is the browser's own bars moving.
const KEYBOARD_PX = 150

/**
 * Whether an on-screen keyboard is up, from how much of the window can be seen: shorter than the
 * tallest it has been at this width, by a keyboard's height. Android's back button closes the
 * keyboard without leaving the field it was typing in, so focus can't say. Turning the phone
 * starts again from its new width.
 */
export function useKeyboardUp(): boolean {
  const [up, setUp] = useState(false)
  useEffect(() => {
    let width = window.innerWidth
    let tallest = 0
    const measure = () => {
      const height = window.visualViewport?.height ?? window.innerHeight
      if (window.innerWidth !== width) {
        width = window.innerWidth
        tallest = 0
      }
      tallest = Math.max(tallest, height)
      setUp(height < tallest - KEYBOARD_PX)
    }
    measure()
    window.addEventListener('resize', measure)
    window.visualViewport?.addEventListener('resize', measure)
    return () => {
      window.removeEventListener('resize', measure)
      window.visualViewport?.removeEventListener('resize', measure)
    }
  }, [])
  return up
}
