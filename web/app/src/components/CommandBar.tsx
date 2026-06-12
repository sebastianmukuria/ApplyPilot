// Floating glass island: wordmark, section tabs, run LED, settings.
import { motion } from 'motion/react'
import { PaperPlaneTilt, GearSix } from '@phosphor-icons/react'
import type { RunStatus } from '../lib/api'
import { DECK } from '../lib/motion'

export type View = 'deck' | 'queue' | 'intel' | 'answers'

const TABS: { id: View; label: string }[] = [
  { id: 'deck', label: 'Deck' },
  { id: 'queue', label: 'Queue' },
  { id: 'intel', label: 'Intel' },
  { id: 'answers', label: 'Answers' },
]

export function CommandBar({
  view,
  onView,
  run,
  onLogoTap,
  onSettings,
}: {
  view: View
  onView: (v: View) => void
  run: RunStatus | null
  onLogoTap: () => void
  onSettings: () => void
}) {
  const led = run?.active
    ? run.needs_you
      ? 'bg-clay shadow-[0_0_12px_2px_rgba(217,119,87,0.6)] animate-pulse'
      : run.done
        ? 'bg-sage shadow-[0_0_10px_1px_rgba(127,176,105,0.5)]'
        : 'bg-sky shadow-[0_0_10px_1px_rgba(122,167,217,0.5)] animate-pulse'
    : 'bg-white/20'

  return (
    <motion.header
      initial={{ y: -28, opacity: 0, filter: 'blur(6px)' }}
      animate={{ y: 0, opacity: 1, filter: 'blur(0px)' }}
      transition={{ duration: 0.8, ease: DECK }}
      className="fixed inset-x-0 top-5 z-40 flex justify-center px-4"
    >
      <div className="flex w-max items-center gap-1 rounded-full bg-black/55 px-2 py-2 ring-1 ring-white/10 backdrop-blur-2xl shadow-[0_8px_40px_rgba(0,0,0,0.45)]">
        <button
          onClick={onLogoTap}
          className="group flex items-center gap-2.5 rounded-full px-3 py-1.5 transition-colors duration-300 hover:bg-white/[0.06]"
          title="ApplyPilot"
        >
          <PaperPlaneTilt
            weight="light"
            size={18}
            className="text-clay transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:rotate-12"
          />
          <span className="hidden font-display text-[13px] font-semibold uppercase tracking-[0.28em] text-ink sm:block">
            ApplyPilot
          </span>
        </button>

        <span className="mx-1 h-5 w-px bg-white/10" />

        <nav className="relative flex items-center">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => onView(t.id)}
              className={`relative rounded-full px-4 py-1.5 text-[12.5px] font-medium tracking-wide transition-colors duration-300 ${
                view === t.id ? 'text-[#140a06]' : 'text-mut hover:text-ink'
              }`}
            >
              {view === t.id && (
                <motion.span
                  layoutId="tab-pill"
                  className="absolute inset-0 rounded-full bg-clay"
                  transition={{ type: 'spring', stiffness: 350, damping: 30 }}
                />
              )}
              <span className="relative">{t.label}</span>
            </button>
          ))}
        </nav>

        <span className="mx-1 h-5 w-px bg-white/10" />

        <div className="flex items-center gap-1 pl-1 pr-1.5">
          <span className={`h-2 w-2 rounded-full transition-colors duration-500 ${led}`} title={
            run?.active ? (run.needs_you ? 'Needs you' : run.done ? 'Run finished' : 'Run in flight') : 'Idle'
          } />
          <button
            onClick={onSettings}
            className="flex h-8 w-8 items-center justify-center rounded-full text-mut transition-all duration-300 hover:bg-white/[0.06] hover:text-ink"
            title="Run settings"
          >
            <GearSix weight="light" size={17} />
          </button>
        </div>
      </div>
    </motion.header>
  )
}
