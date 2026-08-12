# ODIN HUD — Interface Specification

A build spec for a full-screen, Iron Man / J.A.R.V.I.S.-style heads-up display
that surfaces live telemetry from the user's Windows PC and acts as the visual
front-end for ODIN, an existing Python voice assistant.

This document is the single source of truth for the build. Every color, font,
dimension, and data field is specified. Follow it exactly.

---

## 1. What this is

Two reference screenshots drive this design (Rainmeter-era "Stark Industries"
desktop skins). Read the following as a description of the target, not as a
suggestion to copy pixel-for-pixel — the goal is the same *language*, rebuilt
as a live web app wired to real system data and to ODIN's assistant state.

**What the references contain:**

- A near-black field with everything drawn in a narrow range of electric blues
  and cyans. Almost no other hue. Color is used for *state* (warning amber,
  critical red), never for decoration.
- A dominant circular centerpiece — concentric rings, one rotating, wrapped
  around a glowing triangular arc-reactor core. Ring segments are labeled with
  short uppercase abbreviations (COMP, DOCS, CTRL, DESK, PLAY, VOL, CFG, GAME)
  and act as launchers.
- Four radial gauges at the diagonals of that centerpiece, each an arc from 0%
  to 100% with a hairline tick scale and a numeric readout in the middle.
- Dense side columns of small rectangular panels: CPU per-core bars, RAM
  used/free, per-drive capacity, network up/down, temperatures, an audio
  spectrum analyzer, a media transport row, a weather block, a large digital
  clock, uptime, a recycle-bin readout, a news/notes list.
- Every panel is bounded not by a full box but by **corner brackets** — short
  L-shaped rules at each corner, with the sides left open. Panels are connected
  by thin diagonal "circuit" lines that run between them.
- Type is uppercase, condensed, widely letter-spaced for labels; monospaced with
  tabular figures for numbers. Nothing is set in a normal proportional UI face.
- A ruler strip of tick marks with numbers runs along the top edge.
- A wordmark ("STARK INDUSTRIES" / "IRON MAN") sits in dead space as a lockup.
- Reference 2 adds a bottom dock of nine large circular glyph buttons.

**What we change for ODIN:**

The centerpiece is not decorative. It is the **voice state orb** — the single
signature element of this interface. It visibly changes character depending on
whether ODIN is idle, listening, thinking, speaking, or running a long
research task. Everything else on screen stays quiet so this one element
carries the personality. The rest of the HUD is honest instrumentation: real
psutil numbers, real ODIN skill activity, real knowledge-base contents.

---

## 2. Tech stack

Build it as a **local web app rendered in a frameless desktop window**, with a
Python backend that ODIN already runs inside.

| Layer | Choice | Why |
|---|---|---|
| Rendering | HTML + CSS + inline SVG + Canvas2D | Arcs, glows, and rotating rings are trivial in SVG; the spectrum analyzer and waveform need Canvas. Nothing here justifies a game engine. |
| Frontend framework | **None.** Vanilla ES modules. | The DOM is ~40 static panels updated by pushed values. A framework's diffing buys nothing and fights the animation loop. |
| Transport | WebSocket, JSON frames | Push, not poll. One socket, one message shape. |
| Backend | **FastAPI** + `uvicorn`, running in a daemon thread inside ODIN | Reuses ODIN's existing process, so skill events are already in scope. |
| Telemetry | `psutil`, `pynvml` (GPU), `WMI`/`LibreHardwareMonitor` (fan/temp) | `psutil` covers CPU, RAM, disk, net, battery, uptime. Temps on Windows need a helper — see §7.4. |
| Window shell | `pywebview` (frameless, `on_top` optional) | One `pip install`, no Node toolchain, no Electron bundle. |

**Alternative shell (optional, nice-to-have):** to make the HUD the actual
desktop wallpaper behind icons, render the same URL through **Lively Wallpaper**
(free, open source, accepts a local URL as a wallpaper source). Do not build
custom `WorkerW` window-parenting — it is fragile across Windows updates.

Do not build a native PyQt/QML version. The visual language here is arcs,
gradients, blurs, and glows; CSS and SVG do those in a fraction of the code.

---

## 3. Design tokens

Define these once in `static/css/tokens.css` as custom properties. Never write
a raw hex value anywhere else in the codebase.

### 3.1 Color

```css
:root {
  /* Field */
  --void:        #00030A;  /* page background */
  --panel:       rgba(6, 28, 48, 0.34);  /* panel fill, always translucent */
  --panel-solid: #041824;  /* for panels that must occlude, e.g. modals */

  /* The blue range — this is the whole palette */
  --cy-100: #E4FBFF;  /* headline numerals, peak values */
  --cy-200: #9BE8FF;  /* primary readouts */
  --cy-300: #35C8F5;  /* PRIMARY ACCENT — arcs, active strokes, glow source */
  --cy-400: #128FC4;  /* secondary strokes, inactive arc track fill */
  --cy-500: #0B5F87;  /* labels, muted text */
  --cy-600: #073B55;  /* hairline rules, tick marks, panel brackets */
  --cy-700: #04212F;  /* gauge track, empty bar segments */

  /* State — used sparingly, only to mean something */
  --ok:      #17E9A0;  /* nominal / task complete */
  --warn:    #FFB020;  /* >75% load, low battery, degraded */
  --crit:    #FF4444;  /* >90% load, thermal alarm, error */
  --thinking:#B06CFF;  /* ODIN processing — the ONLY non-blue in normal use */
}
```

**Rules on color:**

- Amber and red appear **only** when a threshold is crossed. A HUD where
  everything is always slightly amber teaches the user to ignore amber.
- `--thinking` violet is reserved exclusively for the voice orb's processing
  state. It must not appear anywhere else, ever. Its rarity is what makes it
  readable at a glance from across the room.
- Never introduce a gradient between two different hues. Gradients run within
  the cyan ramp only (e.g. `--cy-400 → --cy-200`).

### 3.2 Glow

Glow is the texture of this interface. Standardize it — do not hand-tune
`box-shadow` per component.

```css
:root {
  --glow-sm: 0 0 4px rgba(53, 200, 245, 0.55);
  --glow-md: 0 0 10px rgba(53, 200, 245, 0.45), 0 0 24px rgba(53, 200, 245, 0.18);
  --glow-lg: 0 0 20px rgba(53, 200, 245, 0.55), 0 0 60px rgba(53, 200, 245, 0.22);
  --text-glow: 0 0 6px rgba(53, 200, 245, 0.7);
}
```

For SVG strokes use an `<filter>` with `feGaussianBlur` + `feMerge` rather than
CSS shadows — sharper and cheaper on stroked paths:

```svg
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
  <feGaussianBlur stdDeviation="2.5" result="b"/>
  <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
```

### 3.3 Type

Three faces, three jobs. Load from Google Fonts or self-host in `static/fonts/`.

| Role | Face | Usage |
|---|---|---|
| **Display** | `Michroma` | Wordmarks and the ODIN lockup only. Never below 14px, never more than ~6 words. Letter-spacing `0.12em`. |
| **Label** | `Saira Condensed` | Every panel title, axis label, ring segment, button. **Always uppercase**, weight 500, letter-spacing `0.22em`. This wide tracking is the single most important typographic signal of the style — do not reduce it. |
| **Data** | `Share Tech Mono` | Every number, clock, IP, percentage, byte count. Must use `font-variant-numeric: tabular-nums` so digits don't jitter as values change. |

```css
--f-display: 'Michroma', sans-serif;
--f-label:   'Saira Condensed', 'Roboto Condensed', sans-serif;
--f-data:    'Share Tech Mono', 'JetBrains Mono', monospace;
```

**Type scale** (fixed px — this is a fixed-resolution HUD, not a responsive site):

```
--t-micro:  9px   /* tick labels, units, footnotes */
--t-label: 11px   /* panel titles, ring segments */
--t-body:  13px   /* list rows, transcript */
--t-data:  16px   /* standard numeric readouts */
--t-lg:    28px   /* gauge center values, weather temp */
--t-xl:    54px   /* master clock */
--t-hero:  72px   /* nothing yet — reserved */
```

### 3.4 Line and shape

- Hairlines are `1px solid var(--cy-600)`. Active/data strokes are `2px`.
  Gauge arcs are `4px`. Nothing is thicker than `4px` except the orb ring.
- **Border-radius is 0 on all rectangular panels.** Circles are circles;
  rectangles are sharp. There is no in-between. A rounded rectangle instantly
  reads as a modern web app and breaks the illusion.
- Panels are never fully outlined. See the bracket component in §5.1.

### 3.5 Motion

```css
--ease-hud: cubic-bezier(0.22, 1, 0.36, 1);
--dur-fast: 140ms;   /* hover, button press */
--dur-val:  600ms;   /* numeric/arc value transitions */
--dur-slow: 1200ms;  /* panel reveal on boot */
```

All value changes are **eased, never instant** — a gauge snapping to a new
number looks broken; a gauge sweeping to it looks alive. But cap it: anything
slower than 600ms makes the HUD feel laggy rather than smooth.

Respect `@media (prefers-reduced-motion: reduce)` — kill ring rotation, orb
pulse, and scanline drift; keep value transitions (they carry information).

---

## 4. Layout

Target **1920 × 1080**, non-responsive, `overflow: hidden`. Use CSS Grid with a
24-column × 12-row grid, `gap: 14px`, `padding: 0 18px 18px`.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ A  TICK RULER  01 02 03 ... 24  ·  ODIN v2.0  ·  USER  ·  UPTIME  · STATUS │  r1
├──────────────┬─────────────────────────────────────────────┬───────────────┤
│              │                                             │               │
│  B  CPU      │                                             │  F  CLOCK     │  r2
│     per-core │              D   VOICE ORB                  │     + DATE    │
│              │            (the centerpiece)                ├───────────────┤
├──────────────┤                                             │               │
│  C  MEMORY   │      concentric rings, arc-reactor core,     │  G  WEATHER   │  r3-5
│              │      4 corner gauges, segment launchers      │               │
├──────────────┤                                             ├───────────────┤
│  C2 STORAGE  │                                             │  H  TEMPS     │  r6
│              │                                             │     CPU/GPU   │
├──────────────┼─────────────────────────────────────────────┼───────────────┤
│  I  NETWORK  │  E  TRANSCRIPT  (live ODIN speech, ticker)   │  J  KNOWLEDGE │  r7-8
│     up/down  ├─────────────────────────────────────────────┤     BASE      │
│     sparkline│  E2 SKILL ACTIVITY LOG  (last 6 executions)  │     topics    │
├──────────────┼─────────────────────────────────────────────┼───────────────┤
│  K  AUDIO    │                                             │  L  NOTES /   │  r9-10
│     spectrum │        ODIN  ·  display lockup              │     REMINDERS │
├──────────────┴─────────────────────────────────────────────┴───────────────┤
│  M   DOCK — 9 circular launchers                                           │  r11-12
└────────────────────────────────────────────────────────────────────────────┘
```

Grid placements:

| Zone | Column span | Row span |
|---|---|---|
| A ruler | 1 / 25 | 1 |
| B cpu | 1 / 6 | 2 / 4 |
| C memory | 1 / 6 | 4 / 6 |
| C2 storage | 1 / 6 | 6 / 7 |
| D orb | 7 / 19 | 2 / 7 |
| E transcript | 7 / 19 | 7 / 8 |
| E2 skill log | 7 / 19 | 8 / 9 |
| F clock | 20 / 25 | 2 / 3 |
| G weather | 20 / 25 | 3 / 6 |
| H temps | 20 / 25 | 6 / 7 |
| I network | 1 / 6 | 7 / 9 |
| J knowledge | 20 / 25 | 7 / 9 |
| K audio | 1 / 6 | 9 / 11 |
| L notes | 20 / 25 | 9 / 11 |
| M dock | 1 / 25 | 11 / 13 |

**Background layers** (stacked, bottom to top), all `pointer-events: none`
except the top content layer:

1. `--void` flat fill.
2. Radial vignette: `radial-gradient(ellipse at 50% 45%, rgba(10,60,90,0.22) 0%, transparent 62%)` — pulls the eye to the orb.
3. Grid mesh: two repeating linear-gradients at 40px, `rgba(11,95,135,0.055)`, 1px lines.
4. Scanlines: repeating-linear-gradient, 3px period, `rgba(0,0,0,0.22)` on alternating lines, `opacity: 0.35`.
5. Circuit traces: one absolutely-positioned SVG covering the viewport, hairline `--cy-600` polylines running between panel corners at 45°/90° only. Purely graphic. ~12 traces, no more.

---

## 5. Component library

Build each of these once in `static/js/components/` as a factory function that
returns a DOM node plus an `update(value)` method. No component fetches its own
data — the socket handler calls `update()`.

### 5.1 `Panel` — the bracket frame

Every rectangular zone uses this. It is the workhorse.

- No border on the element itself.
- Four corner brackets: L-shaped, arm length `14px`, stroke `1px var(--cy-600)`.
  Implement with two pseudo-elements plus two child spans, or a single inline
  SVG — SVG is cleaner and lets you animate `stroke-dashoffset` on boot.
- Background `var(--panel)` with `backdrop-filter: blur(2px)`.
- **Title treatment:** label face, `--t-label`, `var(--cy-500)`, uppercase,
  sitting on the top-left inside `10px` padding, preceded by a `6px × 6px`
  filled square in `--cy-300`. To the title's right, a hairline rule runs to
  the panel's right edge — this is a strong, cheap, very "HUD" detail.
- Optional top-right status pip: 5px circle, `--ok` / `--warn` / `--crit`.
- On boot, brackets draw in (dash-offset animation, 400ms) before contents
  fade up.

### 5.2 `RadialGauge`

The four gauges flanking the orb, plus reuse anywhere a 0–100 value appears.

- SVG, `viewBox="0 0 120 120"`, radius `r = 50`, center `60,60`.
- Sweep: **270°**, starting at 135° (bottom-left) and ending at 45°
  (bottom-right), leaving a gap at the bottom for the label.
- Track: `stroke: var(--cy-700)`, width 4, full sweep.
- Value arc: `stroke: var(--cy-300)`, width 4, `filter: url(#glow)`,
  `stroke-linecap: round`.
- Arc math — precompute once:
  ```js
  const R = 50, SWEEP = 270;
  const CIRC = 2 * Math.PI * R;              // 314.159
  const ARC_LEN = CIRC * (SWEEP / 360);      // 235.619
  // set once:
  el.style.strokeDasharray = `${ARC_LEN} ${CIRC}`;
  // per update (pct 0..1):
  el.style.strokeDashoffset = ARC_LEN * (1 - pct);
  ```
  Rotate the whole `<g>` by `135deg` about center so the sweep starts correctly.
- Tick scale: 21 radial ticks outside the arc (every 5%), `1px var(--cy-600)`,
  every 5th tick (0/25/50/75/100) longer and `var(--cy-400)` with a `--t-micro`
  numeral.
- Center: value in data face at `--t-lg` in `--cy-100`, unit beneath in label
  face at `--t-micro` in `--cy-500`.
- Threshold recolor: arc stroke → `--warn` above 75%, `--crit` above 90%, with a
  `--dur-val` transition. When `--crit`, add a 1.2s pulse on opacity.

### 5.3 `VoiceOrb` — the signature element

The one thing on screen allowed to be elaborate. Roughly 420px square, centered
in zone D. Composed of five stacked layers in one SVG:

1. **Outer segment ring** (r ≈ 200) — 32 discrete segments with 4° gaps.
   Rotates continuously, `60s` linear, infinite. Segment opacity varies
   sinusoidally around the ring for a "scanning" feel.
2. **Launcher ring** (r ≈ 170) — 8 labeled arc segments (see §6.7). Each is a
   hover/click target: on hover the segment fills `rgba(53,200,245,0.14)` and
   its label goes `--cy-100` with `--text-glow`.
3. **Tick ring** (r ≈ 145) — 120 fine radial ticks, static, `--cy-600`.
   Counter-rotating slowly (`-90s`) at 0.4 opacity.
4. **Data ring** (r ≈ 118) — a live arc showing **overall system load**
   (weighted: 0.5·CPU + 0.3·RAM + 0.2·disk-IO). This makes the centerpiece
   informational, not just ornamental.
5. **Core** (r ≈ 78) — the arc reactor. An equilateral triangle inscribed in a
   circle, both stroked `--cy-200` and filled with a radial gradient from
   `#DFFBFF` at center → `--cy-300` → transparent. Wrapped in `--glow-lg`.

**States** — this is the point of the whole component. Drive via a
`data-state` attribute on the root; all differences are CSS.

| State | Core | Rings | Notes |
|---|---|---|---|
| `idle` | Slow breathe: scale 1.0→1.04, opacity 0.75→1.0, 4s ease-in-out infinite | Outer ring rotating at base speed | Default resting appearance |
| `listening` | Core brightens to `--cy-100`; **radius modulates with live mic amplitude** | Outer ring speeds to 24s; a second bright arc sweeps the ring once per second | The most alive state — it must visibly react to the user's voice |
| `thinking` | Core desaturates toward `--thinking`; triangle rotates 360° over 3s, linear, infinite | Data ring switches to an indeterminate 25% arc spinning at 1.5s | The only violet on screen |
| `speaking` | Core pulses on TTS envelope (or a 180ms synthetic pulse per word) | Outer segments light up in sequence, radiating outward | |
| `learning` | Core holds a steady bright state | **Data ring becomes a determinate progress arc** for `deep_learn`, 0→100%, with subtopic name rendered beneath the orb | Ties directly into the RAG skill |
| `error` | Core flashes `--crit` twice, 200ms | Rings freeze for 600ms | |

Mic amplitude for `listening`: the backend already has the audio stream. Send
an RMS float `0..1` at ~20 Hz over the socket; map to core radius
`78 + amp * 22` with a 60ms smoothing filter so it doesn't judder.

### 5.4 `BarMeter`

Horizontal segmented bar for per-core CPU, RAM, drives.

- Track `--cy-700`, height `8px`, zero radius.
- Fill is **segmented**: 40 cells of 2px separated by 1px gaps (use
  `repeating-linear-gradient` as a mask, not 40 DOM nodes).
- Left: label face caption. Right: data-face percentage, `--t-data`.
- Peak marker: a 2px vertical tick in `--cy-100` showing the max value seen in
  the last 10s, decaying downward. Cheap detail, reads as very instrument-like.
- Same 75/90 threshold recolor as the gauge.

### 5.5 `Sparkline`

60-sample rolling history for network and CPU.

- Canvas, 2× device-pixel-ratio scaled, redrawn on each telemetry frame.
- Line `1.5px --cy-300`; area beneath filled with a vertical gradient
  `rgba(53,200,245,0.28) → transparent`.
- Y-axis auto-scales to the window max with a 15% headroom, eased over 600ms so
  the baseline doesn't jump.
- Current value pinned top-right in data face, with unit.

### 5.6 `Spectrum`

The audio analyser in zone K.

- `AudioContext` + `AnalyserNode`, `fftSize: 128` → 64 bins.
- Render 48 bars (drop the top bins, they're mostly empty), each a stack of
  discrete 3px cells with 1px gaps — **not** a smooth bar. The cell stack is
  what makes it read as 2010s-HUD rather than a modern visualizer.
- Cell color by height: bottom 60% `--cy-400`, next 25% `--cy-200`, top 15%
  `--cy-100`.
- Per-bar peak-hold cap: a single cell in `--cy-100` that falls at 0.6 cells/frame.
- Source: default output loopback if available, else mic input. If neither, run
  a seeded pseudo-random idle animation — never show a dead flat analyser.

### 5.7 `TickRuler`

Zone A, full width. 24 major ticks with `--t-micro` two-digit numerals
(`01`–`24`, i.e. hours), 4 minor ticks between each. A single bright
`--cy-100` caret slides along it to mark **the current time of day** — so the
ruler is a real 24-hour progress indicator, not decoration. Under the reduced-
motion query it still updates, just without transition.

### 5.8 `DockButton`

Nine circular launchers, `72px` diameter.

- Ring `2px --cy-400`, inner disc `rgba(6,28,48,0.5)`, glyph in `--cy-200`.
- Hover: ring → `--cy-300` + `--glow-md`, glyph → `--cy-100`, scale 1.06,
  and a second ring expands outward and fades (140ms).
- Active/pressed: scale 0.96, inner disc brightens.
- Label appears below on hover only, label face `--t-micro`.
- Keyboard reachable, visible focus ring (`2px --ok` outline offset 3px).

### 5.9 `Readout`

The generic key/value row used across most panels.

```
▸ LABEL ·············································· 42.7 GB
```

Label face left in `--cy-500`, dotted leader in `--cy-700`, data face right in
`--cy-200`. Height 20px. This one component covers 60% of the panel contents.

---

## 6. Panel-by-panel content

### 6.1 A — Header / ruler
Left: `ODIN` in display face + version. Center: the 24h tick ruler. Right:
`USER: {username}` · `UP {d}d {h}h {m}m` · connection pip (socket health).

### 6.2 B — CPU
- Overall CPU `BarMeter` + current clock speed (MHz) as a `Readout`.
- Per-core `BarMeter` stack (up to 16 cores; above that, aggregate into 16 groups).
- Process count, and the **top 3 processes by CPU** as `Readout` rows — genuinely
  useful, and it's the kind of live churn that makes a HUD feel real.

### 6.3 C / C2 — Memory & storage
- RAM: used / total / percent `BarMeter`, plus a `Sparkline` of the last 60s.
- Swap: single `BarMeter`.
- Per-drive (C:, D:, …): `BarMeter` with `used / total` in GB and free space.
- Disk read/write throughput as two `Readout` rows in MB/s.

### 6.4 D — Voice orb
See §5.3. Beneath the orb: current state as label-face text
(`IDLE` / `LISTENING` / `PROCESSING` / `SPEAKING` / `LEARNING: {subtopic}`).

### 6.5 E / E2 — Transcript & skill log
- **Transcript:** last user utterance in `--cy-500`, ODIN's current reply
  streaming in `--cy-200` at `--t-body`. Type-on effect at ~40 chars/s while
  TTS speaks; instant if reduced-motion.
- **Skill log:** last 6 skill executions, newest at top, each row
  `HH:MM:SS · SKILL_NAME · OK|FAIL · {duration}ms`. Success pip `--ok`,
  failure `--crit`. New rows slide in from the left over 200ms.

### 6.6 F / G / H — Clock, weather, thermals
- **Clock:** `HH:MM:SS` at `--t-xl` in `--cy-100` with `--text-glow`; seconds
  in `--cy-400` at 60% size. Date line beneath in label face. Timezone label.
- **Weather:** reuse ODIN's existing `get_weather` skill output. Large temp at
  `--t-lg`, condition text, then `Readout` rows for humidity / feels-like /
  wind / pressure / sunrise / sunset. Three-day strip along the bottom.
- **Thermals:** CPU temp, GPU temp, GPU load, GPU VRAM, fan RPM. Each a
  `Readout` with threshold coloring. If a sensor is unavailable, render the row
  with `--` in `--cy-600` — **never hide the row and never fabricate a value.**

### 6.7 Orb ring launchers (8 segments)
`SYS` (task manager) · `FILES` (explorer) · `WEB` (browser) · `CODE` (editor) ·
`MUSIC` (player) · `VOL` (volume popover) · `LEARN` (deep-learn prompt) ·
`PWR` (power menu). Each maps to an existing ODIN skill via the command
endpoint in §7.3 — the HUD must not implement its own launching logic.

### 6.8 J — Knowledge base
Wired to the RAG add-on's `list_learned_topics`. One row per learned topic:
topic name, chunk count, a thin bar showing relative size, and last-updated
date. Header shows total topics / total chunks. When `deep_learn` is running,
that topic's row shows a live progress bar and the current subtopic.

Empty state: `NO TOPICS LEARNED — SAY "DEEP SEARCH ABOUT …" TO BEGIN.`
(Empty screens are an invitation to act, not an apology.)

### 6.9 L — Notes & reminders
Reads ODIN's notes file and pending reminder timers. Rows show text + relative
time (`IN 12M`). Overdue/fired items get a `--warn` pip.

### 6.10 M — Dock
Nine `DockButton`s. Suggested: Explorer · Browser · Terminal · Code · Music ·
Settings · Task Manager · Screenshot · ODIN Console (opens a text-input overlay
so the user can type a command instead of speaking).

---

## 7. Data layer

### 7.1 Socket contract

One WebSocket at `ws://127.0.0.1:8777/telemetry`. Server pushes frames; client
never polls. Three message types, discriminated by `type`.

**`telemetry`** — every 1000ms:

```json
{
  "type": "telemetry",
  "ts": 1754870400.12,
  "cpu": {
    "percent": 34.2,
    "per_core": [22.1, 41.0, 18.7, 55.3],
    "freq_mhz": 3592,
    "processes": 214,
    "top": [{"name": "chrome.exe", "cpu": 12.4}]
  },
  "mem": {"used_gb": 18.4, "total_gb": 32.0, "percent": 57.5,
          "swap_percent": 12.0},
  "disks": [{"mount": "C:", "used_gb": 411.2, "total_gb": 931.5, "percent": 44.1}],
  "disk_io": {"read_mbs": 4.2, "write_mbs": 1.1},
  "net": {"up_kbs": 128.4, "down_kbs": 2044.9,
          "total_up_gb": 12.1, "total_down_gb": 88.7, "ip": "192.168.1.14"},
  "battery": {"percent": 88, "plugged": true},
  "thermals": {"cpu_c": 54, "gpu_c": 61, "gpu_load": 22,
               "gpu_vram_percent": 31, "fan_rpm": 1180},
  "uptime_sec": 246540
}
```

Any unavailable field is `null` — the client renders `--`. Never send 0 for
"unknown"; a fake zero is worse than an honest blank.

**`odin`** — pushed on state change, not on an interval:

```json
{
  "type": "odin",
  "state": "listening",
  "mic_rms": 0.42,
  "transcript_user": "make a deep search about react",
  "transcript_odin": "Starting deep research on React…",
  "learning": {"topic": "react", "subtopic": "Hooks", "progress": 0.375},
  "skill_log": [
    {"ts": 1754870399, "skill": "deep_learn", "ok": true, "ms": 84210}
  ]
}
```

Send `mic_rms` alone at 20 Hz while `state === "listening"` (a minimal frame
with just `type`, `state`, `mic_rms`) — do not resend the whole object.

**`kb`** — on demand and after any `deep_learn` completes:

```json
{
  "type": "kb",
  "topics": [{"topic": "react", "chunks": 142, "updated": 1754870400}]
}
```

### 7.2 Backend structure

```
core/hud/
  server.py       FastAPI app, WS endpoint, broadcast manager
  telemetry.py    psutil/pynvml collectors -> the telemetry dict
  bridge.py       ODIN state hooks: on_state_change, on_skill_start/end,
                  on_mic_frame, on_learn_progress  -> broadcast()
static/
  index.html
  css/  tokens.css  layout.css  components.css  effects.css
  js/   main.js  socket.js  components/*.js
  fonts/
```

`server.py` runs `uvicorn` in a daemon thread started from ODIN's `main.py`, so
one process owns both the assistant and the HUD.

### 7.3 Command endpoint (HUD → ODIN)

`POST /command` with `{"text": "open chrome"}`. The handler passes the string
straight into ODIN's existing brain/tool-use loop — identical to a typed
message. Dock buttons and ring launchers are **just preset command strings**.
This is the whole integration; do not duplicate skill logic in the frontend.

Bind to `127.0.0.1` only. No auth needed on loopback, but do not bind `0.0.0.0`
"for convenience" — that exposes remote PC control on the LAN.

### 7.4 Sensor notes

- `psutil.sensors_temperatures()` **returns empty on Windows.** For CPU/fan,
  run **LibreHardwareMonitor** with its WMI provider enabled and query
  `root\LibreHardwareMonitor` via the `wmi` package. If it isn't installed,
  return `null` and let the UI show `--`.
- GPU: `pynvml` for NVIDIA (temp, load, VRAM). AMD needs LibreHardwareMonitor.
- Network rates: `psutil.net_io_counters()` is cumulative — you must diff
  against the previous sample and divide by elapsed time. Same for disk I/O.
- Cache disk usage; `psutil.disk_usage()` per drive every second is wasteful.
  Poll drives every 15s, everything else every 1s.

---

## 8. Boot sequence

A ~2.6s orchestrated startup. This is the second-most memorable moment after
the orb, and it costs almost nothing to build.

| t | Event |
|---|---|
| 0.0s | Black. A single hairline draws horizontally across the vertical center, 400ms. |
| 0.4s | Line splits vertically outward, revealing the grid mesh (opacity 0→1, 300ms). |
| 0.7s | Panel brackets draw in, staggered 40ms apart, ordered outward from center. |
| 1.3s | Panel contents fade up with a 3px upward translate, same stagger. |
| 1.6s | Orb rings scale in 0.85→1.0 from the center, outermost first. |
| 2.1s | Core ignites: white flash to `#FFFFFF`, then settles to the idle gradient over 500ms. Rings begin rotating. |
| 2.4s | Gauges sweep from 0 to their live values. Clock starts. |
| 2.6s | Socket connects; `ODIN ONLINE` prints once into the transcript. |

Skip the entire sequence under `prefers-reduced-motion` — render the final
state immediately.

---

## 9. Build order

Work in this order; each phase should run standalone before moving on.

1. **Shell + tokens.** `index.html`, all CSS token files, the background layer
   stack, the grid with empty labeled `Panel`s. Verify the grid at 1920×1080.
2. **Telemetry backend.** `telemetry.py` + WS server, printing frames to console.
   Confirm the rate-derived fields (net, disk I/O) are correct before any UI.
3. **Static components.** `Panel`, `Readout`, `BarMeter`, `RadialGauge`,
   `Sparkline` — driven by a mock frame generator, no socket yet.
4. **Wire the socket.** Replace mocks with live frames. Add threshold coloring
   and value easing. At this point it's a working system monitor.
5. **The orb.** All five layers, all six states, driven first by a manual state
   switcher in the console, then by the ODIN bridge.
6. **ODIN bridge.** `bridge.py` hooks, transcript, skill log, `/command`
   endpoint, dock and ring launchers.
7. **Knowledge base panel.** Wire to the RAG add-on.
8. **Spectrum + boot sequence + circuit traces.** The polish pass. Do this last —
   it's the most fun and the least load-bearing.

---

## 10. Constraints and quality floor

- **Performance:** the HUD runs 24/7. Target < 4% CPU idle. Use one shared
  `requestAnimationFrame` loop for all canvas components — not one per widget.
  Pause all animation when the window is hidden (`document.hidden`).
- **No fabricated data.** Every number on screen must trace to a real reading.
  If a sensor is missing, show `--`. A HUD that invents plausible numbers is
  worse than useless.
- **No layout shift.** Tabular figures everywhere; fixed-width containers for
  values. Nothing may reflow as numbers change.
- **Accessibility floor:** keyboard-navigable dock and ring launchers, visible
  focus rings, `prefers-reduced-motion` honored, `aria-live="polite"` on the
  transcript so a screen reader announces ODIN's replies.
- **Copy voice:** all interface text is uppercase label-face, terse, and
  instrument-like. `NO SIGNAL`, not "Sorry, we couldn't find any data!" Errors
  state what happened and what to do: `SENSOR OFFLINE — INSTALL LHM`.

---

## 11. The one risk worth taking

The temptation with this style is to make every panel elaborate — spinning
rings on the memory gauge, animated brackets everywhere, glow on all text.
That's how these Rainmeter desktops usually end up, and it's why they're
unreadable as actual instruments.

Do the opposite. **The orb is the only elaborate thing.** Everything else is
flat, still, and quiet — brackets, hairlines, monospaced numbers, no motion
except eased value changes. The contrast is what makes the centerpiece land,
and it's what makes the HUD survive being on screen all day.

Before shipping, take a screenshot, and remove one effect.