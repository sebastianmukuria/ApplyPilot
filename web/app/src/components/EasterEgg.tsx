// The Flight Log: 5 taps on the wordmark (or Konami) launches a paper-plane
// squadron — one plane per submitted application — with a HUD toast.
import { useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { PaperPlaneTilt } from '@phosphor-icons/react'

export function FlightLog({ trigger, applied }: { trigger: number; applied: number }) {
  const [show, setShow] = useState(false)
  const reduced = useMemo(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    [],
  )

  useEffect(() => {
    if (trigger === 0) return
    setShow(true)
    const t = setTimeout(() => setShow(false), reduced ? 3000 : 4600)
    return () => clearTimeout(t)
  }, [trigger, reduced])

  const planes = useMemo(() => {
    const n = Math.max(1, Math.min(applied, 30))
    return Array.from({ length: n }, (_, i) => ({
      id: `${trigger}-${i}`,
      delay: i * 0.09,
      y0: 18 + ((i * 37) % 55), // vh
      drift: ((i * 23) % 30) - 15, // vh wander
      size: 16 + ((i * 13) % 14),
      spin: ((i * 41) % 24) - 12,
    }))
  }, [trigger, applied])

  return (
    <AnimatePresence>
      {show && (
        <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden">
          {!reduced &&
            planes.map((p) => (
              <motion.span
                key={p.id}
                className="absolute text-clay"
                style={{ top: `${p.y0}vh`, left: '-4vw' }}
                initial={{ x: 0, y: 0, rotate: p.spin, opacity: 0 }}
                animate={{
                  x: '112vw',
                  y: [`0vh`, `${p.drift}vh`, `${p.drift * 0.4}vh`],
                  rotate: [p.spin, -p.spin, p.spin / 2],
                  opacity: [0, 1, 1, 0.9],
                }}
                exit={{ opacity: 0 }}
                transition={{ duration: 3.2 + p.delay, delay: p.delay, ease: [0.3, 0.6, 0.4, 1] }}
              >
                <PaperPlaneTilt weight="fill" size={p.size} />
              </motion.span>
            ))}
          <motion.div
            initial={{ y: 24, opacity: 0, filter: 'blur(6px)' }}
            animate={{ y: 0, opacity: 1, filter: 'blur(0px)' }}
            exit={{ y: 12, opacity: 0 }}
            transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1], delay: 0.4 }}
            className="absolute bottom-10 left-1/2 -translate-x-1/2"
          >
            <div className="flex items-center gap-3 rounded-full bg-black/70 px-5 py-3 ring-1 ring-clay/30 backdrop-blur-xl">
              <PaperPlaneTilt weight="light" size={16} className="text-clay" />
              <span className="font-display text-[13px] tracking-wide text-ink">
                Flight log: <span className="tnum text-clay-hi">{applied}</span> application{applied === 1 ? '' : 's'} and counting
              </span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  )
}
