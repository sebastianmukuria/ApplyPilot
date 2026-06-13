// Easter-egg trigger: 5 wordmark taps within 2.5s, or the Konami code.
import { useEffect, useRef, useState } from 'react'

const KONAMI = [
  'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
  'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a',
]

export function useFlightLog() {
  const [armed, setArmed] = useState(0) // increments to (re)trigger
  const taps = useRef<number[]>([])

  const tap = () => {
    const now = performance.now()
    taps.current = [...taps.current.filter((t) => now - t < 2500), now]
    if (taps.current.length >= 5) {
      taps.current = []
      setArmed((a) => a + 1)
    }
  }

  useEffect(() => {
    let idx = 0
    const onKey = (e: KeyboardEvent) => {
      idx = e.key === KONAMI[idx] ? idx + 1 : e.key === KONAMI[0] ? 1 : 0
      if (idx === KONAMI.length) {
        idx = 0
        setArmed((a) => a + 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return { tap, armed }
}
