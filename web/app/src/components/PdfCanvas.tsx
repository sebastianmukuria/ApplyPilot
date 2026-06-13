// Renders a PDF with pdf.js onto canvases — independent of the browser's PDF
// plugin settings (Chrome's "download PDFs instead" breaks iframe embeds).
import { useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

export function PdfCanvas({ src }: { src: string }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    let dead = false
    const host = hostRef.current
    if (!host) return
    host.replaceChildren()
    setState('loading')

    const task = pdfjs.getDocument({ url: src })
    ;(async () => {
      try {
        const doc = await task.promise
        if (dead) return
        const width = host.clientWidth || 720
        const dpr = Math.min(window.devicePixelRatio || 1, 2)
        for (let n = 1; n <= doc.numPages; n++) {
          const page = await doc.getPage(n)
          if (dead) return
          const base = page.getViewport({ scale: 1 })
          const scale = (width - 32) / base.width
          const vp = page.getViewport({ scale: scale * dpr })
          const canvas = document.createElement('canvas')
          canvas.width = vp.width
          canvas.height = vp.height
          canvas.style.width = `${vp.width / dpr}px`
          canvas.style.height = `${vp.height / dpr}px`
          canvas.style.display = 'block'
          canvas.style.margin = '16px auto'
          canvas.style.borderRadius = '6px'
          canvas.style.boxShadow = '0 4px 24px rgba(0,0,0,0.5)'
          host.appendChild(canvas)
          await page.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport: vp }).promise
        }
        if (!dead) setState('ready')
      } catch {
        if (!dead) setState('error')
      }
    })()

    return () => {
      dead = true
      task.destroy().catch(() => {})
    }
  }, [src])

  return (
    <div className="relative min-h-0 w-full flex-1 overflow-auto bg-[#161616]">
      <div ref={hostRef} />
      {state === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center text-[12.5px] italic text-faint">
          Rendering…
        </div>
      )}
      {state === 'error' && (
        <div className="absolute inset-0 flex items-center justify-center text-[12.5px] text-clay-hi">
          Couldn't render this PDF — use the download button instead.
        </div>
      )}
    </div>
  )
}
