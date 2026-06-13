// Glass full-height modal that renders a PDF inline (browser-native viewer).
import { AnimatePresence, motion } from 'motion/react'
import { DownloadSimple, X } from '@phosphor-icons/react'
import { DECK } from '../lib/motion'
import { PdfCanvas } from './PdfCanvas'
import { Eyebrow } from './ui'

export interface PdfTarget {
  title: string
  /** inline-disposition URL rendered in the viewer */
  src: string
  /** attachment-disposition URL for the optional download action */
  downloadSrc?: string
}

export function PdfViewer({ target, onClose }: { target: PdfTarget | null; onClose: () => void }) {
  return (
    <AnimatePresence>
      {target && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-2xl sm:p-8"
          onClick={onClose}
        >
          <motion.div
            initial={{ y: 28, scale: 0.98, opacity: 0 }}
            animate={{ y: 0, scale: 1, opacity: 1 }}
            exit={{ y: 16, scale: 0.99, opacity: 0 }}
            transition={{ duration: 0.45, ease: DECK }}
            className="shell flex h-full w-full max-w-4xl flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="core flex min-h-0 flex-1 flex-col overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-white/[0.07] px-5 py-3.5">
                <Eyebrow>{target.title}</Eyebrow>
                <div className="flex items-center gap-1.5">
                  {target.downloadSrc && (
                    <a
                      href={target.downloadSrc}
                      className="flex h-8 w-8 items-center justify-center rounded-full text-mut transition-all duration-300 hover:bg-white/[0.06] hover:text-ink"
                      title="Download"
                    >
                      <DownloadSimple weight="light" size={16} />
                    </a>
                  )}
                  <button
                    onClick={onClose}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-mut transition-all duration-300 hover:bg-white/[0.06] hover:text-ink"
                  >
                    <X weight="light" size={16} />
                  </button>
                </div>
              </div>
              <PdfCanvas src={target.src} />
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
