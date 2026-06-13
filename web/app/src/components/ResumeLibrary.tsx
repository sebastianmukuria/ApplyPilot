// Résumé library: browse / preview / upload PDFs and crown one as the master.
import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { Crown, FilePdf, UploadSimple, X } from '@phosphor-icons/react'
import { api, type ResumeItem } from '../lib/api'
import { DECK } from '../lib/motion'
import { PdfCanvas } from './PdfCanvas'
import { Eyebrow, IslandButton } from './ui'

const KIND_LABEL: Record<ResumeItem['kind'], string> = {
  master: 'current master',
  base: 'base résumé',
  library: 'library',
}

export function ResumeLibrary({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<ResumeItem[]>([])
  const [sel, setSel] = useState<ResumeItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = async () => {
    try {
      const r = await api.resumes()
      setItems(r.items)
      setSel((s) => r.items.find((i) => i.id === s?.id) ?? r.items.find((i) => i.is_master) ?? r.items[0] ?? null)
    } catch (e) {
      setErr(String(e))
    }
  }

  useEffect(() => {
    if (open) {
      setErr('')
      load()
    }
  }, [open])

  const upload = async (f: File) => {
    setBusy(true)
    setErr('')
    try {
      const item = await api.uploadResume(f)
      await load()
      setSel(item)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const crown = async () => {
    if (!sel) return
    setBusy(true)
    setErr('')
    try {
      const r = await api.selectResume(sel.id)
      setItems(r.items)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-2xl sm:p-8"
          onClick={(e) => {
            e.stopPropagation() // don't bubble into a parent sheet's backdrop
            onClose()
          }}
        >
          <motion.div
            initial={{ y: 28, scale: 0.98, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 16, scale: 0.99, opacity: 0 }}
            transition={{ duration: 0.45, ease: DECK }}
            className="shell flex h-full w-full max-w-5xl flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="core flex min-h-0 flex-1 flex-col overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-white/[0.07] px-5 py-3.5">
                <Eyebrow>Résumé library</Eyebrow>
                <button
                  onClick={onClose}
                  className="flex h-8 w-8 items-center justify-center rounded-full text-mut transition-all hover:bg-white/[0.06] hover:text-ink"
                >
                  <X weight="light" size={16} />
                </button>
              </div>

              <div className="grid min-h-0 flex-1 md:grid-cols-[300px_1fr]">
                {/* list pane */}
                <div className="flex min-h-0 flex-col border-b border-white/[0.07] md:border-b-0 md:border-r">
                  <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-3">
                    {items.map((it) => (
                      <button
                        key={it.id}
                        onClick={() => setSel(it)}
                        className={`flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-left transition-all duration-300 ${
                          sel?.id === it.id
                            ? 'bg-clay/10 ring-1 ring-clay/30'
                            : 'ring-1 ring-white/[0.05] hover:bg-white/[0.03]'
                        }`}
                      >
                        <FilePdf weight="light" size={18} className={it.is_master ? 'text-clay-hi' : 'text-faint'} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] text-ink">{it.name}</span>
                          <span className="block text-[10.5px] uppercase tracking-[0.14em] text-faint">
                            {KIND_LABEL[it.kind]} · {(it.size / 1024).toFixed(0)} KB
                          </span>
                        </span>
                        {it.is_master && <Crown weight="fill" size={14} className="flex-none text-clay" />}
                      </button>
                    ))}
                    {items.length === 0 && (
                      <div className="px-3 py-8 text-center text-[12.5px] italic text-faint">
                        No PDFs yet — upload one below.
                      </div>
                    )}
                  </div>
                  <div className="border-t border-white/[0.07] p-3">
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
                      disabled={busy}
                      className="flex w-full items-center justify-center gap-2 rounded-2xl py-3 text-[12.5px] text-mut ring-1 ring-dashed ring-white/15 transition-all hover:text-ink hover:ring-white/30 disabled:opacity-40"
                    >
                      <UploadSimple weight="light" size={15} /> Upload a PDF
                    </button>
                  </div>
                </div>

                {/* preview pane */}
                <div className="flex min-h-0 flex-col">
                  {sel ? (
                    <>
                      <PdfCanvas key={sel.id} src={api.resumeFileUrl(sel.id)} />
                      <div className="flex items-center justify-between gap-3 border-t border-white/[0.07] px-5 py-3.5">
                        <span className="truncate text-[12px] text-faint">{sel.name}</span>
                        {sel.is_master ? (
                          <span className="flex items-center gap-2 text-[12px] text-clay-hi">
                            <Crown weight="fill" size={14} /> This is the master
                          </span>
                        ) : (
                          <IslandButton onClick={crown} disabled={busy} icon={<Crown weight="fill" size={12} />}>
                            Use as master
                          </IslandButton>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="flex flex-1 items-center justify-center text-[13px] italic text-faint">
                      Select a résumé to preview it.
                    </div>
                  )}
                </div>
              </div>

              {err && <div className="border-t border-white/[0.07] px-5 py-2.5 text-[12px] text-clay-hi">{err}</div>}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
