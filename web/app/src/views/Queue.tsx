// Queue: sticky filter rail + animated card list.
import { useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { MagnifyingGlass, X } from '@phosphor-icons/react'
import { api, type Job, type JobsQuery } from '../lib/api'
import { DECK } from '../lib/motion'
import { JobCard } from '../components/JobCard'
import { Eyebrow } from '../components/ui'

const SORTS: { id: NonNullable<JobsQuery['sort']>; label: string }[] = [
  { id: 'score', label: 'Score' },
  { id: 'salary', label: 'Salary' },
  { id: 'recent', label: 'Recent' },
  { id: 'company', label: 'A–Z' },
]

const STAGE_LABELS: Record<string, string> = {
  discovered: 'Discovered', scored: 'Scored', strong: 'Strong (≥7)',
  docs_ready: 'Docs ready', applied: 'Applied', handoff: 'Handed off', failed: 'Failed',
}

export function Queue({
  onLaunch,
  refreshKey,
  stage,
  onClearStage,
}: {
  onLaunch: (j: Job) => void
  refreshKey: number
  stage?: string | null
  onClearStage?: () => void
}) {
  const [minScore, setMinScore] = useState(8)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<NonNullable<JobsQuery['sort']>>('score')
  const [docsReady, setDocsReady] = useState(false)
  const [showApplied, setShowApplied] = useState(false)
  const [hideFlagged, setHideFlagged] = useState(true)
  const [minSalaryK, setMinSalaryK] = useState(0)
  const [includeNoSalary, setIncludeNoSalary] = useState(true)
  const [jobs, setJobs] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [bump, setBump] = useState(0)

  const query = useMemo<JobsQuery>(
    () =>
      stage
        ? { stage, search: search || undefined, sort, limit: 200 }
        : {
            min_score: minScore,
            search: search || undefined,
            sort,
            only_docs_ready: docsReady,
            show_applied: showApplied,
            hide_flagged: hideFlagged,
            min_salary_k: minSalaryK,
            include_no_salary: includeNoSalary,
            limit: 100,
          },
    [stage, minScore, search, sort, docsReady, showApplied, hideFlagged, minSalaryK, includeNoSalary],
  )

  useEffect(() => {
    let dead = false
    setLoading(true)
    const t = setTimeout(async () => {
      try {
        const r = await api.jobs(query)
        if (!dead) {
          setJobs(r.jobs)
          setTotal(r.total_matched)
        }
      } finally {
        if (!dead) setLoading(false)
      }
    }, 150)
    return () => {
      dead = true
      clearTimeout(t)
    }
  }, [query, refreshKey, bump])

  return (
    <div className="mx-auto grid w-full max-w-7xl gap-8 px-4 pb-32 lg:grid-cols-[280px_1fr]">
      {/* filter rail (or a stage chip when drilling into a funnel number) */}
      <motion.aside
        initial={{ opacity: 0, x: -16 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.7, ease: DECK }}
        className="lg:sticky lg:top-28 lg:h-max"
      >
        {stage ? (
          <div className="shell">
            <div className="core space-y-4 p-5">
              <Eyebrow>Viewing</Eyebrow>
              <div className="flex items-center gap-2 rounded-2xl bg-clay/10 px-3.5 py-3 ring-1 ring-clay/25">
                <span className="flex-1 text-[13px] font-medium text-clay-hi">{STAGE_LABELS[stage] ?? stage}</span>
                <button onClick={onClearStage} className="text-clay-hi/70 transition-colors hover:text-clay-hi" title="Back to all filters">
                  <X weight="bold" size={14} />
                </button>
              </div>
              <label className="flex items-center gap-2 rounded-full bg-white/[0.03] px-3.5 py-2.5 ring-1 ring-white/[0.07] focus-within:ring-clay/40">
                <MagnifyingGlass weight="light" size={15} className="text-faint" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search within"
                  className="w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-faint"
                />
              </label>
              <button onClick={onClearStage} className="text-[12px] text-mut underline-offset-2 hover:text-ink hover:underline">
                ← back to filtered queue
              </button>
            </div>
          </div>
        ) : (
        <div className="shell">
          <div className="core space-y-6 p-5">
            <Eyebrow>Filters</Eyebrow>

            <label className="flex items-center gap-2 rounded-full bg-white/[0.03] px-3.5 py-2.5 ring-1 ring-white/[0.07] focus-within:ring-clay/40">
              <MagnifyingGlass weight="light" size={15} className="text-faint" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Company or title"
                className="w-full bg-transparent text-[13px] text-ink outline-none placeholder:text-faint"
              />
            </label>

            <Range label="Min fit score" value={minScore} min={1} max={10} onChange={setMinScore} />
            <Range
              label="Salary floor"
              value={minSalaryK}
              min={0}
              max={250}
              step={10}
              onChange={setMinSalaryK}
              format={(v) => (v ? `$${v}k` : 'any')}
            />

            <div className="space-y-2.5">
              <Toggle label="Docs ready only" on={docsReady} set={setDocsReady} />
              <Toggle label="Include no-salary roles" on={includeNoSalary} set={setIncludeNoSalary} />
              <Toggle label="Hide staffing / low-comp" on={hideFlagged} set={setHideFlagged} />
              <Toggle label="Show applied + handed off" on={showApplied} set={setShowApplied} />
            </div>

            <div>
              <div className="mb-2 text-[11px] uppercase tracking-[0.18em] text-faint">Sort</div>
              <div className="flex flex-wrap gap-1.5">
                {SORTS.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => setSort(s.id)}
                    className={`rounded-full px-3 py-1.5 text-[12px] transition-all duration-300 ${
                      sort === s.id
                        ? 'bg-clay/15 text-clay-hi ring-1 ring-clay/30'
                        : 'text-mut ring-1 ring-white/[0.07] hover:text-ink'
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
        )}
      </motion.aside>

      {/* card list */}
      <div className="min-w-0">
        <div className="mb-4 flex items-baseline justify-between">
          <span className="font-display text-[12px] uppercase tracking-[0.24em] text-mut">
            {loading ? 'Scanning…' : stage ? `${total} ${(STAGE_LABELS[stage] ?? stage).toLowerCase()}` : `${total} roles on the board`}
          </span>
        </div>
        <motion.div layout className="space-y-4">
          <AnimatePresence mode="popLayout">
            {jobs.map((j, i) => (
              <JobCard key={j.url} job={j} i={i} onLaunch={onLaunch} onChanged={() => setBump((b) => b + 1)} />
            ))}
          </AnimatePresence>
          {!loading && jobs.length === 0 && (
            <div className="shell">
              <div className="core p-10 text-center text-[13px] italic text-faint">
                Nothing matches — loosen a filter or run discovery.
              </div>
            </div>
          )}
        </motion.div>
      </div>
    </div>
  )
}

function Toggle({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) {
  return (
    <button onClick={() => set(!on)} className="group flex w-full items-center justify-between text-left">
      <span className="text-[12.5px] text-mut transition-colors group-hover:text-ink">{label}</span>
      <span
        className={`relative h-5 w-9 rounded-full transition-colors duration-300 ${on ? 'bg-clay/80' : 'bg-white/10'}`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-ink transition-transform duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] ${
            on ? 'translate-x-[16px]' : 'translate-x-0'
          }`}
        />
      </span>
    </button>
  )
}

function Range({
  label, value, min, max, step = 1, onChange, format = String,
}: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-[0.18em] text-faint">{label}</span>
        <span className="tnum text-[12px] text-clay-hi">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[#d97757]"
      />
    </div>
  )
}
