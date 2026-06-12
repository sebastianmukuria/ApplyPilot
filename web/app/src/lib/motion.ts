// Shared motion language — one curve, one set of entry variants.
import type { Variants, Transition } from 'motion/react'

export const DECK: [number, number, number, number] = [0.32, 0.72, 0, 1]

export const spring: Transition = { type: 'spring', stiffness: 260, damping: 24 }

export const enter: Variants = {
  hidden: { opacity: 0, y: 28, filter: 'blur(6px)' },
  show: (i: number = 0) => ({
    opacity: 1,
    y: 0,
    filter: 'blur(0px)',
    transition: { duration: 0.7, ease: DECK, delay: i * 0.06 },
  }),
}

export const stagger: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06 } },
}

export const pop: Variants = {
  hidden: { opacity: 0, scale: 0.96 },
  show: { opacity: 1, scale: 1, transition: spring },
  exit: { opacity: 0, scale: 0.97, transition: { duration: 0.18 } },
}
