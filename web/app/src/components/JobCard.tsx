// One job: bezel card, expands in place for the dossier preview.
import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  ArrowSquareOut, CaretDown, EyeSlash, FilePdf, EnvelopeSimple, Play, Check,
} from '@phosphor-icons/react'
import { api, type Job } from '../lib/api'
import { DECK } from '../lib/motion'
import { PdfViewer, type PdfTarget } from './PdfViewer'
import { FlagChip, GhostAction, IslandButton, ScoreChip, StatusPill } from './ui'

export function JobCard({
  job,
  onLaunch,
  onChanged,
  i = 0,
}: {
  job: Job
  onLaunch: (job: Job) => void
  onChanged: () => void
  i?: number
}) {
  const [open, setOpen] = useState(false)
  const [viewer, setViewer] = useState<PdfTarget | null>(null)
  const [detail, setDetail] = useState<{ resume_preview: string; cover_preview: string } | null>(null)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && !detail) {
      try {
        setDetail(await api.jobDetail(job.url))
      } catch {
        setDetail({ resume_preview: '', cover_preview: '' })
      }
    }
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 24, filter: 'blur(5px)' }}
      animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
      exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.18 } }}
      transition={{ duration: 0.6, ease: DECK, delay: Math.min(i, 8) * 0.05 }}
      className="shell transition-shadow duration-500 hover:shadow-[0_8px_40px_rgba(0,0,0,0.45)]"
    >
      <motion.div layout className="core p-5">
        <motion.div layout className="flex flex-wrap items-center gap-3">
          <ScoreChip score={job.fit_score} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2.5">
              <span className="truncate font-display text-[15.5px] font-semibold tracking-wide text-ink">
                {job.company ?? '?'}
              </span>
              <span className="truncate text-[13px] text-mut">{job.title}</span>
            </div>
            <div className="tnum mt-1 flex flex-wrap items-center gap-x-2 text-[12px] text-faint">
              <span className={job.salary ? 'text-mut' : 'italic'}>
                {job.salary ?? 'salary n/a'}
              </span>
              {job.location && <><span>·</span><span>{job.location}</span></>}
              {job.site && <><span>·</span><span>{job.site}</span></>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {job.flags.map((f) => (
              <FlagChip key={f} label={f} />
            ))}
            <StatusPill status={job.apply_status} />
          </div>
        </motion.div>

        <motion.div layout className="mt-4 flex flex-wrap items-center gap-2">
          <IslandButton onClick={() => onLaunch(job)} icon={<Play weight="fill" size={12} />}>
            Apply
          </IslandButton>
          <GhostAction href={job.application_url ?? job.url}>
            <ArrowSquareOut weight="light" size={14} /> Open
          </GhostAction>
          {job.has_resume && (
            <GhostAction
              onClick={() =>
                setViewer({
                  title: `Résumé — ${job.company ?? ''}`,
                  src: api.resumeUrl(job.url, true),
                  downloadSrc: api.resumeUrl(job.url),
                })
              }
            >
              <FilePdf weight="light" size={14} /> Résumé
            </GhostAction>
          )}
          {job.has_cover && (
            <GhostAction
              onClick={() =>
                setViewer({
                  title: `Cover letter — ${job.company ?? ''}`,
                  src: api.coverUrl(job.url, true),
                  downloadSrc: api.coverUrl(job.url),
                })
              }
            >
              <EnvelopeSimple weight="light" size={14} /> Cover
            </GhostAction>
          )}
          {(job.apply_status === 'handoff' || job.apply_status === 'failed') && (
            <GhostAction
              onClick={async () => {
                await api.markApplied(job.url)
                onChanged()
              }}
              title="I submitted this one myself"
            >
              <Check weight="light" size={14} /> Mark applied
            </GhostAction>
          )}
          <span className="flex-1" />
          <GhostAction
            onClick={async () => {
              await api.hide(job.url)
              onChanged()
            }}
            title="Hide from the queue"
          >
            <EyeSlash weight="light" size={14} />
          </GhostAction>
          <GhostAction onClick={toggle} title="Dossier">
            <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.4, ease: DECK }}>
              <CaretDown weight="light" size={14} />
            </motion.span>
            Dossier
          </GhostAction>
        </motion.div>

        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              key="dossier"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.5, ease: DECK }}
              className="overflow-hidden"
            >
              <div className="mt-5 border-t border-white/[0.07] pt-4">
                {job.score_reasoning && (
                  <p className="mb-4 max-w-3xl text-[13px] leading-relaxed text-mut">
                    <span className="mr-2 font-display text-[11px] uppercase tracking-[0.2em] text-clay-hi">
                      Why this score
                    </span>
                    {job.score_reasoning}
                  </p>
                )}
                <div className="grid gap-4 md:grid-cols-2">
                  {detail?.resume_preview ? (
                    <Dossier title="Tailored résumé" text={detail.resume_preview} />
                  ) : null}
                  {detail?.cover_preview ? (
                    <Dossier title="Cover letter" text={detail.cover_preview} />
                  ) : null}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
      <PdfViewer target={viewer} onClose={() => setViewer(null)} />
    </motion.div>
  )
}

function Dossier({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-2xl bg-white/[0.025] p-4 ring-1 ring-white/[0.06]">
      <div className="mb-2 font-display text-[11px] uppercase tracking-[0.2em] text-mut">{title}</div>
      <div className="logwell max-h-72 overflow-auto">{text}</div>
    </div>
  )
}
