// Core primitives: double-bezel cards, island buttons, pills, kinetic numerals.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { motion, useInView, useMotionValue, useSpring, useTransform } from 'motion/react'
import { ArrowUpRight } from '@phosphor-icons/react'
import { enter } from '../lib/motion'

export function Bezel({
  children,
  className = '',
  coreClassName = '',
  i = 0,
}: {
  children: ReactNode
  className?: string
  coreClassName?: string
  i?: number
}) {
  return (
    <motion.div
      variants={enter}
      custom={i}
      initial="hidden"
      whileInView="show"
      viewport={{ once: true, margin: '-40px' }}
      className={`shell ${className}`}
    >
      <div className={`core ${coreClassName}`}>{children}</div>
    </motion.div>
  )
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full bg-white/[0.04] px-3 py-1 text-[10px] font-medium uppercase tracking-[0.22em] text-mut ring-1 ring-white/10">
      {children}
    </span>
  )
}

const PILL_STYLES: Record<string, { dot: string; text: string; ring: string; label: string }> = {
  pending: { dot: 'bg-faint', text: 'text-mut', ring: 'ring-white/10', label: 'Pending' },
  in_progress: { dot: 'bg-sky', text: 'text-sky', ring: 'ring-sky/30', label: 'In flight' },
  applied: { dot: 'bg-sage', text: 'text-sage', ring: 'ring-sage/30', label: 'Applied' },
  handoff: { dot: 'bg-sage', text: 'text-sage', ring: 'ring-sage/40', label: 'Handed off' },
  failed: { dot: 'bg-clay', text: 'text-clay-hi', ring: 'ring-clay/30', label: 'Failed' },
  manual: { dot: 'bg-clay', text: 'text-clay-hi', ring: 'ring-clay/30', label: 'Manual ATS' },
}

export function StatusPill({ status }: { status: string | null }) {
  const s = PILL_STYLES[status ?? 'pending'] ?? PILL_STYLES.pending
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.14em] ring-1 ${s.text} ${s.ring}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot} ${status === 'in_progress' ? 'animate-pulse' : ''}`} />
      {s.label}
    </span>
  )
}

export function FlagChip({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.14em] text-clay-hi/80 ring-1 ring-white/10">
      {label}
    </span>
  )
}

export function ScoreChip({ score }: { score: number | null }) {
  const hot = (score ?? 0) >= 9
  return (
    <span
      className={`tnum inline-flex h-8 min-w-8 items-center justify-center rounded-xl px-2 font-display text-sm font-semibold ${
        hot ? 'bg-clay text-[#140a06]' : 'bg-clay/10 text-clay-hi ring-1 ring-clay/20'
      }`}
    >
      {score ?? '–'}
    </span>
  )
}

/** Primary CTA: pill with the nested-circle trailing icon and magnetic hover. */
export function IslandButton({
  children,
  onClick,
  href,
  variant = 'primary',
  icon,
  disabled,
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  href?: string
  variant?: 'primary' | 'ghost' | 'danger'
  icon?: ReactNode
  disabled?: boolean
  className?: string
}) {
  const base =
    'group inline-flex items-center gap-3 rounded-full px-5 py-2.5 text-[13px] font-medium transition-all duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] active:scale-[0.98] disabled:opacity-40 disabled:pointer-events-none'
  const look =
    variant === 'primary'
      ? 'bg-clay text-[#140a06] hover:bg-clay-hi'
      : variant === 'danger'
        ? 'text-clay-hi ring-1 ring-white/10 hover:ring-clay/40 hover:bg-clay/5'
        : 'text-ink ring-1 ring-white/10 hover:bg-white/[0.05] hover:ring-white/20'
  const inner = (
    <>
      <span>{children}</span>
      <span
        className={`flex h-7 w-7 items-center justify-center rounded-full transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:-translate-y-px group-hover:translate-x-0.5 group-hover:scale-105 ${
          variant === 'primary' ? 'bg-black/15' : 'bg-white/[0.06]'
        }`}
      >
        {icon ?? <ArrowUpRight weight="light" size={14} />}
      </span>
    </>
  )
  if (href)
    return (
      <a href={href} target="_blank" rel="noreferrer" className={`${base} ${look} ${className}`}>
        {inner}
      </a>
    )
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${look} ${className}`}>
      {inner}
    </button>
  )
}

/** Quiet text button for secondary row actions. */
export function GhostAction({
  children,
  onClick,
  href,
  download,
  title,
}: {
  children: ReactNode
  onClick?: () => void
  href?: string
  download?: boolean
  title?: string
}) {
  const cls =
    'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] text-mut ring-1 ring-white/[0.07] transition-all duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] hover:text-ink hover:ring-white/20 active:scale-[0.98]'
  if (href)
    return (
      <a className={cls} href={href} {...(download ? {} : { target: '_blank', rel: 'noreferrer' })} title={title}>
        {children}
      </a>
    )
  return (
    <button className={cls} onClick={onClick} title={title}>
      {children}
    </button>
  )
}

/** Kinetic numeral: springs from 0 when scrolled into view. */
export function CountUp({
  value,
  format = (n: number) => Math.round(n).toLocaleString(),
  className = '',
}: {
  value: number
  format?: (n: number) => string
  className?: string
}) {
  const ref = useRef<HTMLSpanElement>(null)
  const inView = useInView(ref, { once: true, margin: '-20px' })
  const mv = useMotionValue(0)
  const springy = useSpring(mv, { stiffness: 80, damping: 22 })
  const text = useTransform(springy, format)
  const [display, setDisplay] = useState('0')
  useEffect(() => text.on('change', setDisplay), [text])
  useEffect(() => {
    if (inView) mv.set(value)
  }, [inView, value, mv])
  return (
    <span ref={ref} className={`tnum ${className}`}>
      {display}
    </span>
  )
}
