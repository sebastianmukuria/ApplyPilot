// First-run wizard: walks a non-technical user from zero to a scanning
// pipeline. Every optional step says exactly what access it wants and why,
// and can be skipped. Auto-opens when the basics are missing.
import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  ArrowRight, Check, CircleNotch, EnvelopeOpen, FilePdf, MagnifyingGlass,
  PaperPlaneTilt, Sparkle, UploadSimple, User,
} from '@phosphor-icons/react'
import { api } from '../lib/api'
import { DECK } from '../lib/motion'
import { Eyebrow, IslandButton } from './ui'

export interface OnboardingState {
  resume_ready: boolean
  profile_ready: boolean
  searches_ready: boolean
  claude_cli: boolean
  api_key_present: boolean
  telegram_configured: boolean
  tracking_configured: boolean
  jobs_discovered: number
}

const inputCls =
  'w-full rounded-2xl bg-white/[0.03] px-4 py-3 text-[13.5px] text-ink ring-1 ring-white/[0.07] outline-none transition-shadow duration-300 placeholder:text-faint focus:ring-clay/40'

type StepId = 'welcome' | 'resume' | 'you' | 'hunt' | 'engine' | 'alerts' | 'tracking' | 'launch'
const STEPS: StepId[] = ['welcome', 'resume', 'you', 'hunt', 'engine', 'alerts', 'tracking', 'launch']

export function Onboarding({
  state,
  onDone,
}: {
  state: OnboardingState
  onDone: () => void
}) {
  const [step, setStep] = useState<StepId>('welcome')
  const idx = STEPS.indexOf(step)
  const next = () => setStep(STEPS[Math.min(idx + 1, STEPS.length - 1)])

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[70] flex items-center justify-center bg-[#050505]/95 p-4 backdrop-blur-xl"
    >
      <div className="shell w-full max-w-xl">
        <div className="core flex max-h-[88vh] flex-col overflow-hidden">
          {/* progress dots */}
          <div className="flex items-center justify-between border-b border-white/[0.07] px-6 py-4">
            <span className="flex items-center gap-2">
              <PaperPlaneTilt weight="light" size={16} className="text-clay" />
              <span className="font-display text-[11px] font-semibold uppercase tracking-[0.26em] text-ink">
                ApplyPilot setup
              </span>
            </span>
            <div className="flex gap-1.5">
              {STEPS.map((s, i) => (
                <span
                  key={s}
                  className={`h-1.5 rounded-full transition-all duration-500 ${
                    i === idx ? 'w-5 bg-clay' : i < idx ? 'w-1.5 bg-clay/40' : 'w-1.5 bg-white/10'
                  }`}
                />
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto p-7">
            <AnimatePresence mode="wait">
              <motion.div
                key={step}
                initial={{ opacity: 0, x: 18 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.35, ease: DECK }}
              >
                {step === 'welcome' && <Welcome onNext={next} />}
                {step === 'resume' && <ResumeStep ready={state.resume_ready} onNext={next} />}
                {step === 'you' && <YouStep onNext={next} />}
                {step === 'hunt' && <HuntStep onNext={next} />}
                {step === 'engine' && <EngineStep state={state} onNext={next} />}
                {step === 'alerts' && <AlertsStep configured={state.telegram_configured} onNext={next} />}
                {step === 'tracking' && <TrackingStep configured={state.tracking_configured} onNext={next} />}
                {step === 'launch' && <LaunchStep onDone={onDone} />}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

function StepShell({
  icon, title, blurb, children, onNext, nextLabel = 'Continue', skippable, onSkip,
}: {
  icon: React.ReactNode
  title: string
  blurb: string
  children?: React.ReactNode
  onNext?: () => void
  nextLabel?: string
  skippable?: boolean
  onSkip?: () => void
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2.5 text-clay">{icon}<Eyebrow>{title}</Eyebrow></div>
      <p className="mt-3 text-[14px] leading-relaxed text-mut">{blurb}</p>
      {children && <div className="mt-5 space-y-3">{children}</div>}
      <div className="mt-7 flex items-center gap-3">
        {onNext && (
          <IslandButton onClick={onNext} icon={<ArrowRight weight="light" size={13} />}>
            {nextLabel}
          </IslandButton>
        )}
        {skippable && (
          <button onClick={onSkip} className="rounded-full px-4 py-2 text-[12.5px] text-faint transition-colors hover:text-ink">
            Skip for now
          </button>
        )}
      </div>
    </div>
  )
}

function Welcome({ onNext }: { onNext: () => void }) {
  return (
    <StepShell
      icon={<Sparkle weight="light" size={18} />}
      title="Welcome aboard"
      blurb="ApplyPilot finds jobs that fit you, writes the paperwork, and fills out
applications in a real browser while you stay in control — nothing is ever
submitted without you unless you explicitly allow it. Everything runs on your
computer; your data never leaves it except to the job sites themselves.
Setup takes about five minutes."
      onNext={onNext}
      nextLabel="Let's set up"
    />
  )
}

function ResumeStep({ ready, onNext }: { ready: boolean; onNext: () => void }) {
  const [done, setDone] = useState(ready)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const upload = async (f: File) => {
    setBusy(true)
    setErr('')
    try {
      const item = await api.uploadResume(f)
      await api.selectResume(item.id) // becomes the master + the pipeline's base text
      setDone(true)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <StepShell
      icon={<FilePdf weight="light" size={18} />}
      title="Your résumé"
      blurb="Upload the résumé you're proudest of, as a PDF. It's stored only on this
computer. It becomes the résumé that gets uploaded with applications, and the
text inside it teaches the AI what you've actually done."
      onNext={done ? onNext : undefined}
      skippable={!done}
      onSkip={onNext}
    >
      <input
        ref={fileRef}
        type="file"
        accept="application/pdf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) upload(f)
          e.target.value = ''
        }}
      />
      <button
        onClick={() => fileRef.current?.click()}
        disabled={busy || done}
        className={`flex w-full items-center justify-center gap-2 rounded-2xl py-5 text-[13px] transition-all ${
          done
            ? 'text-sage ring-1 ring-sage/30'
            : 'text-mut ring-1 ring-dashed ring-white/15 hover:text-ink hover:ring-white/30'
        }`}
      >
        {busy ? (
          <><CircleNotch size={15} className="animate-spin" /> Uploading…</>
        ) : done ? (
          <><Check weight="bold" size={15} /> Résumé loaded</>
        ) : (
          <><UploadSimple weight="light" size={16} /> Choose your résumé PDF</>
        )}
      </button>
      {err && <p className="text-[12px] text-clay-hi">{err}</p>}
    </StepShell>
  )
}

function YouStep({ onNext }: { onNext: () => void }) {
  const [f, setF] = useState({ full_name: '', email: '', phone: '', city: '', linkedin_url: '' })
  const [err, setErr] = useState('')
  const canSave = f.full_name.trim() && f.email.trim()

  const save = async () => {
    try {
      await fetch('/api/profile/personal', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(f),
      }).then((r) => { if (!r.ok) throw new Error(`${r.status}`) })
      onNext()
    } catch (e) {
      setErr(`Couldn't save (${e}) — you can finish this later in Settings.`)
    }
  }

  return (
    <StepShell
      icon={<User weight="light" size={18} />}
      title="About you"
      blurb="These fill the contact fields on application forms — that's their only job.
Name and email are required by virtually every application; the rest are
optional but save you typing later."
      onNext={canSave ? save : undefined}
      skippable
      onSkip={onNext}
    >
      <div className="grid gap-2.5 sm:grid-cols-2">
        <input className={inputCls} placeholder="Full name *" value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} />
        <input className={inputCls} placeholder="Email *" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        <input className={inputCls} placeholder="Phone" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} />
        <input className={inputCls} placeholder="City" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} />
      </div>
      <input className={inputCls} placeholder="LinkedIn URL" value={f.linkedin_url} onChange={(e) => setF({ ...f, linkedin_url: e.target.value })} />
      {err && <p className="text-[12px] text-clay-hi">{err}</p>}
    </StepShell>
  )
}

function HuntStep({ onNext }: { onNext: () => void }) {
  const [titles, setTitles] = useState('')
  const [locations, setLocations] = useState('')
  const [remote, setRemote] = useState(true)
  const [err, setErr] = useState('')

  const save = async () => {
    try {
      await fetch('/api/searches', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          titles: titles.split(',').map((s) => s.trim()).filter(Boolean),
          locations: locations.split(',').map((s) => s.trim()).filter(Boolean),
          remote,
        }),
      }).then((r) => { if (!r.ok) throw new Error(`${r.status}`) })
      onNext()
    } catch (e) {
      setErr(`Couldn't save (${e}) — editable later in Settings.`)
    }
  }

  return (
    <StepShell
      icon={<MagnifyingGlass weight="light" size={18} />}
      title="What are we hunting?"
      blurb="Job titles to search for, separated by commas. Be generous — the AI scores
every result against your résumé, so a wide net costs nothing."
      onNext={titles.trim() ? save : undefined}
      skippable
      onSkip={onNext}
    >
      <input className={inputCls} placeholder="e.g. Data Analyst, Analytics Engineer, BI Analyst" value={titles} onChange={(e) => setTitles(e.target.value)} />
      <input className={inputCls} placeholder="Locations (optional) — e.g. Los Angeles, New York" value={locations} onChange={(e) => setLocations(e.target.value)} />
      <label className="flex cursor-pointer items-center gap-3 text-[13px] text-mut">
        <input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} className="accent-[#d97757]" />
        Include remote roles
      </label>
      {err && <p className="text-[12px] text-clay-hi">{err}</p>}
    </StepShell>
  )
}

function EngineStep({ state, onNext }: { state: OnboardingState; onNext: () => void }) {
  const ok = state.claude_cli || state.api_key_present
  return (
    <StepShell
      icon={<Sparkle weight="light" size={18} />}
      title="The engine"
      blurb={
        state.claude_cli
          ? 'Claude Code is installed — that\'s the whole engine. Scoring, documents, and applying all run on your Claude subscription. No API keys, no extra cost beyond the subscription you already have.'
          : 'ApplyPilot needs one AI to think with. The simplest path: install Claude Code (claude.com/claude-code) and sign in — your subscription then powers everything. Alternative: a free Google Gemini API key works for everything except auto-applying.'
      }
      onNext={onNext}
      nextLabel={ok ? 'Continue' : 'I\'ll set this up later'}
    >
      <div className="space-y-2">
        <EngineRow ok={state.claude_cli} label="Claude Code" detail={state.claude_cli ? 'found — full autopilot available' : 'not found — install from claude.com/claude-code'} />
        <EngineRow ok={state.api_key_present} label="API key (optional)" detail={state.api_key_present ? 'configured' : 'optional — add GEMINI_API_KEY in Settings later if you want a non-Claude engine'} />
      </div>
    </StepShell>
  )
}

function EngineRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl bg-white/[0.02] px-4 py-3 ring-1 ring-white/[0.06]">
      <span className={`h-2 w-2 flex-none rounded-full ${ok ? 'bg-sage' : 'bg-white/20'}`} />
      <span className="text-[13px] text-ink">{label}</span>
      <span className="min-w-0 flex-1 truncate text-right text-[11.5px] text-faint">{detail}</span>
    </div>
  )
}

function AlertsStep({ configured, onNext }: { configured: boolean; onNext: () => void }) {
  return (
    <StepShell
      icon={<EnvelopeOpen weight="light" size={18} />}
      title="Pings (optional)"
      blurb="When the autopilot needs you — a CAPTCHA, or an application ready for your
final review — it can ping your phone. Telegram and ntfy are free; both are
configured in Settings → Alerts whenever you want them. Browser notifications
work with zero setup: just flip them on in Settings. Nothing here sends your
data anywhere except the one-line ping itself."
      onNext={onNext}
      nextLabel={configured ? 'Already set up — continue' : 'Got it'}
      skippable={!configured}
      onSkip={onNext}
    />
  )
}

function TrackingStep({ configured, onNext }: { configured: boolean; onNext: () => void }) {
  const [phase, setPhase] = useState<'idle' | 'opening' | 'connected'>(configured ? 'connected' : 'idle')
  const [hasCreds, setHasCreds] = useState(true)
  useEffect(() => {
    api.trackingStatus().then((s) => { setHasCreds(s.has_credentials ?? false); if (s.configured) setPhase('connected') }).catch(() => {})
  }, [])
  const connect = async () => {
    try { await api.trackingConnect(); setPhase('opening') } catch { setHasCreds(false) }
  }
  return (
    <StepShell
      icon={<EnvelopeOpen weight="light" size={18} />}
      title="Email tracking (optional)"
      blurb=""
      onNext={onNext}
      nextLabel={configured ? 'Connected — continue' : 'Maybe later'}
      skippable={!configured}
      onSkip={onNext}
    >
      <p className="text-[13.5px] leading-relaxed text-mut">
        ApplyPilot can watch your Gmail for replies to your applications — interview
        invites, rejections, recruiter outreach — and chart your real response rates.
      </p>
      <div className="rounded-2xl bg-white/[0.02] p-4 text-[12px] leading-relaxed text-faint ring-1 ring-white/[0.06]">
        <strong className="text-mut">What it actually accesses, honestly:</strong> read-only
        Gmail permission, fetching only sender / subject / date / a one-line snippet —
        never full email bodies. Everything is processed and stored on this computer.
        You approve it yourself in a Google window — one click below — and can revoke access
        any time at myaccount.google.com. If reading email metadata feels like too much, skip
        it; you can always log outcomes by hand on each job card.
      </div>
      {phase === 'connected' ? (
        <div className="flex items-center gap-2 text-[12.5px] text-sage">
          <Check weight="bold" size={14} /> Gmail connected
        </div>
      ) : phase === 'opening' ? (
        <div className="flex items-center gap-2 text-[12.5px] text-sky">
          <CircleNotch size={13} className="animate-spin" /> Approve ApplyPilot in the browser window…
        </div>
      ) : hasCreds ? (
        <button
          onClick={connect}
          className="inline-flex items-center gap-2 rounded-full bg-clay px-4 py-2 text-[12.5px] font-medium text-[#140a06] transition-colors hover:bg-clay-hi"
        >
          <EnvelopeOpen weight="light" size={14} /> Connect Gmail
        </button>
      ) : (
        <p className="text-[11.5px] leading-relaxed text-faint">
          One setup step is still needed (a Google OAuth client) — see SETUP.md → Email tracking.
          You can do this later; skip for now.
        </p>
      )}
    </StepShell>
  )
}

function LaunchStep({ onDone }: { onDone: () => void }) {
  const [phase, setPhase] = useState<'idle' | 'running' | 'done' | 'unavailable'>('idle')

  const scan = async () => {
    try {
      const r = await fetch('/api/pipeline/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stages: ['all'] }),
      })
      if (!r.ok && r.status !== 409) throw new Error(`${r.status}`)
      setPhase('running')
      setTimeout(onDone, 1800) // land on the Deck; progress shows there
    } catch {
      setPhase('unavailable')
    }
  }

  return (
    <StepShell
      icon={<PaperPlaneTilt weight="light" size={18} />}
      title="Ready for the first scan"
      blurb="This searches the job boards, reads each posting, and scores every job
against your résumé from 1–10. It runs in the background and takes a few
minutes — the Deck fills up as results land. You can close this window;
the scan keeps going."
      onNext={phase === 'idle' ? scan : phase !== 'running' ? onDone : undefined}
      nextLabel={phase === 'idle' ? 'Start scanning' : 'Open the Flight Deck'}
    >
      {phase === 'running' && (
        <div className="flex items-center gap-2.5 text-[13px] text-sky">
          <CircleNotch size={15} className="animate-spin" /> Scanning the boards — heading to the Deck…
        </div>
      )}
      {phase === 'unavailable' && (
        <p className="text-[12.5px] text-faint">
          Couldn't start a scan from here — run <code className="text-mut">applypilot run</code> in a
          terminal, or use the Scan button on the Deck once the engine is configured.
        </p>
      )}
    </StepShell>
  )
}
