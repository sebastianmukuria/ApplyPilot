# ApplyPilot v2 — "Flight Deck" Design System

Mission-control for a job hunt. Dark-first, OLED black, ethereal glass,
asymmetric bento, spring physics. The page should feel like premium avionics:
calm, precise, alive.

## Vibe (locked)

- **Canvas:** `#050505` OLED black. Fixed aurora mesh behind everything:
  two radial-gradient orbs (clay `#d97757` at 8% alpha top-left drifting,
  emerald `#34d399` at 5% alpha bottom-right), 120s slow drift via transform.
  Fixed film-grain noise overlay at 3% opacity, pointer-events-none.
- **Surfaces:** double-bezel cards — outer shell `bg-white/[0.03]` +
  `ring-1 ring-white/10` + `p-1.5` + `rounded-[1.75rem]`; inner core
  `bg-[#0a0a0a]` + inset top highlight + `rounded-[calc(1.75rem-0.375rem)]`.
  Glass (backdrop-blur) ONLY on the floating command bar and modals.
- **Type:** `Clash Display` (display/masthead/big numerals, semibold,
  tight tracking) + `Geist` (UI, tabular-nums for data). No Inter.
- **Icons:** Phosphor, weight="light", 1.25rem default.
- **Accent discipline:** clay `#D97757` is THE interactive color (CTAs,
  active states, focus). Status semantics are fixed and never follow accent:
  sage `#7FB069` success / sky `#7AA7D9` in-flight / clay `#E0735C` needs-you
  / zinc pending. Score chips: clay tint, solid clay ≥9.
- **Hairlines:** `white/10`. Never solid gray borders. No dark drop shadows —
  elevation via surface lift + ambient `shadow-[0_8px_40px_rgba(0,0,0,0.45)]`
  on floating elements only.

## Layout

- **Floating command bar** (fluid island): detached glass pill `mt-5 mx-auto`,
  contains wordmark (paper-plane glyph + APPLYPILOT in Clash), section tabs
  (Deck / Queue / Intel / Answers), run status LED, settings gear.
- **Deck (home):** asymmetric bento grid 12-col:
  - hero cell (col-span-8): "Flight Deck" kinetic headline + live funnel
    sparkline + primary CTA (Run next apply)
  - spend cell (col-span-4): Est. spend, animated count-up numeral
  - funnel band (col-span-12): 7 stat cells, Clash numerals, count-up on view
  - live-run cell (col-span-7): status, SSE log terminal (mono-styled Geist),
    needs-you alert state with clay pulse
  - handoff cell (col-span-5): awaiting-confirmation list
- **Queue:** filter rail (left, sticky) + card list. Cards: double-bezel,
  hover lifts -2px + hairline brightens, expand in-place (layout animation)
  for preview. Stagger-reveal on first paint (60ms steps, blur-up).
- **Intel (stats):** charts in bento cells — area chart (apps/day) with clay
  gradient fill + glow line; status donut; score histogram. Animated draw-in.
- **Answers:** generator (question → answer) + projects editor, same cards.

## Motion language

- Curve: `cubic-bezier(0.32, 0.72, 0, 1)`, 500–800ms for entries; springs
  (stiffness 260, damping 24) for interactive bits.
- Entry: `translate-y-12 blur-sm opacity-0 → 0/0/100` staggered.
- Numerals: spring count-up when entering viewport; digits roll.
- Buttons: pill, button-in-button trailing icon (nested circle), magnetic
  hover (icon translates diagonally), `active:scale-[0.98]`.
- Filter changes: AnimatePresence layout animations — cards reflow, never pop.
- Needs-you: clay LED pulse on command bar + card; never blinking text.
- All animation on transform/opacity only.

## Easter egg ("Flight log")

Click the paper-plane wordmark glyph **5×** (or Konami code): a squadron of
paper planes (one per applied job, cap 30) launches from the logo, arcs across
the viewport on randomized spring paths with slight bank rotation, leaving
brief contrails; a HUD toast reads "Flight log: N applications and counting."
Respect `prefers-reduced-motion`: swap to a static toast.

## Stack

Vite + React 18 + TypeScript + Tailwind v4 + `motion` (Framer) +
`@phosphor-icons/react` + Recharts (restyled). Fonts self-hosted woff2
(`geist` npm package + Clash Display from Fontshare, files committed in
`web/public/fonts`). Dev: vite proxy `/api` → `127.0.0.1:8765`.
Build: `outDir ../src/applypilot/webdist` (served by `applypilot app`).

## Quality bar (pre-ship checklist)

Double-bezel everywhere · no banned fonts/icons/shadows · py-24 breathing ·
custom beziers only · stagger reveals · blur only on fixed elements ·
single-column collapse <768px (min-h-[100dvh], never h-screen) ·
prefers-reduced-motion honored · reads as flight instrument, not template.
