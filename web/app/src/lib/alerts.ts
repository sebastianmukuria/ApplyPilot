// In-app alerting: OS notifications + a soft chime when a run flips state.
// Frontend-only — watches the runs poll, no backend involvement.
import { useEffect, useRef, useState } from 'react'
import type { RunsResponse, RunStatus } from './api'

export interface AlertPrefs {
  browser: boolean
  chime: boolean
}

const PREFS_KEY = 'ap.alerts'

export function loadAlertPrefs(): AlertPrefs {
  try {
    return { browser: false, chime: true, ...JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') }
  } catch {
    return { browser: false, chime: true }
  }
}

export function saveAlertPrefs(p: AlertPrefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(p))
}

export async function enableBrowserNotifications(): Promise<boolean> {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  return (await Notification.requestPermission()) === 'granted'
}

/** Two-tone "attention" chime via WebAudio — no asset, ~0.4s, gentle. */
function chime() {
  try {
    const ctx = new AudioContext()
    const play = (freq: number, at: number) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0, ctx.currentTime + at)
      gain.gain.linearRampToValueAtTime(0.12, ctx.currentTime + at + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + at + 0.35)
      osc.connect(gain).connect(ctx.destination)
      osc.start(ctx.currentTime + at)
      osc.stop(ctx.currentTime + at + 0.4)
    }
    play(880, 0)
    play(1174.66, 0.16) // D6 — a pleasant fourth up
    setTimeout(() => ctx.close(), 1200)
  } catch {
    /* audio blocked until first interaction — fine */
  }
}

function notifyOS(title: string, body: string, tag: string) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  try {
    new Notification(title, { body, tag, icon: '/favicon.svg' })
  } catch {
    /* some browsers require a service worker — degrade silently */
  }
}

/** Watches run transitions and fires alerts per prefs. */
export function useRunAlerts(runs: RunsResponse | null, prefs: AlertPrefs) {
  const prev = useRef<Map<string, RunStatus>>(new Map())

  useEffect(() => {
    if (!runs) return
    const seen = prev.current
    for (const r of runs.runs) {
      if (!r.run_id) continue
      const before = seen.get(r.run_id)
      const company = r.company ?? 'a job'

      if (r.needs_you && !before?.needs_you) {
        if (prefs.chime) chime()
        if (prefs.browser)
          notifyOS('ApplyPilot needs you', `${company} — solve the CAPTCHA or review & submit.`, r.run_id)
      } else if (r.done && !before?.done) {
        if (prefs.browser) {
          const failed = r.status === 'failed'
          notifyOS(
            failed ? 'Run failed' : 'Run finished',
            failed ? `${company} hit a wall — check the log.` : `${company} is ${r.status === 'handoff' ? 'ready for your submit' : 'done'}.`,
            r.run_id,
          )
        }
      }
      seen.set(r.run_id, r)
    }
    // forget runs that left the registry
    const liveIds = new Set(runs.runs.map((r) => r.run_id))
    for (const id of [...seen.keys()]) if (!liveIds.has(id)) seen.delete(id)
  }, [runs, prefs])
}

export function useAlertPrefs(): [AlertPrefs, (p: AlertPrefs) => void] {
  const [prefs, setPrefs] = useState<AlertPrefs>(loadAlertPrefs)
  const update = (p: AlertPrefs) => {
    setPrefs(p)
    saveAlertPrefs(p)
  }
  return [prefs, update]
}
