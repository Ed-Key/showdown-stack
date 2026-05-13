// extension/lib/val-trend.ts
//
// Pure helpers for the engine-confidence trend indicator. The engine emits a
// `confidence` (top-1 value) on every `final` event; we track per-battle
// history of those values so the panel can render a tiny arrow indicating
// whether the position is improving, stable, decaying, or collapsing.
//
// Why: the displayed confidence is the engine's belief about the *best*
// available move from the *current* position. If the engine reports
// "QuiverDance, 60%" three turns in a row but val collapses 0.61 → 0.59
// → 0.50, the move pick still LOOKS positive even though the engine is
// actually telling us "this line is failing." A trend arrow turns that
// hidden signal into a glanceable cue.
//
// All functions are pure / framework-free so they're trivially testable
// (vitest runs in node mode, no jsdom).

export type Trend = 'rising' | 'falling' | 'collapsing' | 'flat' | null;

/** Cap on per-battle val history length. We only need the last few turns. */
export const VAL_HISTORY_CAP = 10;

/** val < this triggers the DESPERATE tag (engine says "least-bad option"). */
export const DESPERATE_THRESHOLD = 0.30;

/** Trend deltas (compared against the last 2-3 turns).
 * EPS guards against IEEE-754 noise (e.g. 0.60 - 0.65 = -0.05000000000000004
 * which would tip a "flat" classification into "falling" without slack). */
const EPS = 1e-9;
const RISING_DELTA = 0.05;
const FALLING_DELTA = -0.05;
const COLLAPSE_DELTA = -0.20;

/**
 * Compute the trend label from a per-battle val history (most-recent last).
 *
 * Compares the last entry to entries 2 and 3 turns back:
 *  - collapsing: most recent dropped > 0.20 vs 2 turns ago
 *  - falling:    most recent down > 0.05 vs 2-3 turns ago
 *  - rising:     most recent up > 0.05 vs 2-3 turns ago
 *  - flat:       within ±0.05 of the comparison points
 *  - null:       not enough history yet (≤ 1 sample) — caller should hide
 *                the indicator on first turn.
 *
 * `null` is intentional: turn 1 has no prior, and we don't want to confuse
 * users by surfacing a meaningless "flat" badge before any signal exists.
 */
export function computeTrend(history: readonly number[]): Trend {
  if (!Array.isArray(history) || history.length < 2) return null;
  const current = history[history.length - 1];
  if (!Number.isFinite(current)) return null;

  // Prefer the 2-turns-ago comparison; fall back to 3-turns-ago when
  // available (smooths a single-turn jitter without being misleading).
  const twoBack = history[history.length - 2];
  const threeBack = history.length >= 3 ? history[history.length - 3] : undefined;

  if (!Number.isFinite(twoBack)) return null;
  const delta2 = current - twoBack;

  // Collapse check first — it's the loudest signal and overrides everything.
  if (delta2 < COLLAPSE_DELTA - EPS) return 'collapsing';

  // Average the 2- and 3-back deltas when we have 3-back, otherwise use 2-back
  // alone. We compare the AVERAGE delta against the threshold so a single
  // wobble doesn't flip the arrow back and forth.
  let delta = delta2;
  if (threeBack !== undefined && Number.isFinite(threeBack)) {
    delta = (delta2 + (current - threeBack)) / 2;
  }

  if (delta > RISING_DELTA + EPS) return 'rising';
  if (delta < FALLING_DELTA - EPS) return 'falling';
  return 'flat';
}

/** UTF-8 glyph for each trend state. `flat` and `null` render no glyph. */
export function formatTrendArrow(trend: Trend): string {
  switch (trend) {
    case 'rising': return '↗';
    case 'falling': return '↘';
    case 'collapsing': return '⚠';
    case 'flat':
    case null:
    default:
      return '';
  }
}

/** Tooltip / aria-label string for each trend state. */
export function formatTrendTitle(trend: Trend): string {
  switch (trend) {
    case 'rising': return 'engine confidence rising over the last 2-3 turns (+0.05 or more)';
    case 'falling': return 'engine confidence falling over the last 2-3 turns (-0.05 or more)';
    case 'collapsing': return 'engine confidence collapsing — dropped more than 0.20 in 2 turns';
    case 'flat': return 'engine confidence stable';
    case null:
    default: return '';
  }
}

/**
 * Append a val sample to a history array, returning a new array (capped).
 * Pure — does not mutate the input. NaN / non-finite samples are skipped.
 */
export function appendVal(history: readonly number[], val: number): number[] {
  if (!Number.isFinite(val)) return history.slice();
  const next = [...history, val];
  if (next.length > VAL_HISTORY_CAP) return next.slice(next.length - VAL_HISTORY_CAP);
  return next;
}

/** Sparkline geometry. Tuned for an inline 60-80px wide indicator beside the
 * trend arrow; adjust here rather than in CSS so SVG viewBox stays in sync. */
export const SPARKLINE_WIDTH = 70;
export const SPARKLINE_HEIGHT = 16;
export const SPARKLINE_MAX_POINTS = 8;
const SPARKLINE_PAD_X = 2;
const SPARKLINE_PAD_Y = 3;

/**
 * Render a tiny inline sparkline of recent val samples as an SVG string.
 *
 * Companion to the trend arrow: where the arrow gives a binary direction,
 * the sparkline shows the *shape* of the last N turns. A steady plateau,
 * a slow decline, and a cliff all read differently even when the latest
 * delta yields the same arrow.
 *
 * Behavior:
 *  - 0 samples → a neutral em-dash placeholder (so the layout doesn't shift
 *    on turn 1 — same reasoning as `formatTrendArrow` returning '' on null).
 *  - 1 sample  → a single dot at the right edge.
 *  - 2+        → polyline of the last `SPARKLINE_MAX_POINTS` samples plus
 *    a dot at every data point. y-axis is auto-scaled across [min, max] of
 *    the visible window so small wobbles read as small wobbles.
 *  - line color: green (rising), amber (falling), red (collapsing), grey
 *    otherwise — derived from `computeTrend(window)` so the chart agrees
 *    with the arrow next to it.
 *  - tooltip (`<title>`) lists each sample as "T1: 0.61 → T2: 0.59 → ...".
 *    `startTurn` shifts the labels so they match the actual game turn the
 *    user sees in the panel; defaults to 1 when omitted.
 *
 * Pure / framework-free: returns a string the caller drops into innerHTML.
 * No mutation, no DOM access — testable under node-mode vitest.
 */
export function renderSparkline(history: readonly number[], startTurn: number = 1): string {
  const safe = Array.isArray(history)
    ? history.filter((v) => Number.isFinite(v))
    : [];

  // Empty placeholder. Em-dash centered in the same bounding box keeps the
  // confidence line from reflowing when the sparkline appears on turn 2.
  if (safe.length === 0) {
    return (
      `<span class="sc-sparkline sc-sparkline-empty" ` +
      `title="no engine confidence history yet" ` +
      `aria-label="no engine confidence history yet">—</span>`
    );
  }

  const window = safe.slice(-SPARKLINE_MAX_POINTS);
  const baseTurn = startTurn + (safe.length - window.length);
  const tooltip = window
    .map((v, i) => `T${baseTurn + i}: ${v.toFixed(2)}`)
    .join(' → ');
  const tipAttr = tooltip
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Map trend → stroke color so the sparkline reinforces the arrow.
  const trend = computeTrend(window);
  const stroke =
    trend === 'rising' ? '#7fdc7f' :
    trend === 'falling' ? '#ffb060' :
    trend === 'collapsing' ? '#ff6a6a' :
    '#9aa0a6';

  const w = SPARKLINE_WIDTH;
  const h = SPARKLINE_HEIGHT;
  const innerW = w - SPARKLINE_PAD_X * 2;
  const innerH = h - SPARKLINE_PAD_Y * 2;

  // Auto-scale y across the visible window. If everything is identical,
  // collapse to a flat midline (avoids divide-by-zero and a misleading dip).
  let min = Math.min(...window);
  let max = Math.max(...window);
  if (max === min) { min -= 0.005; max += 0.005; }
  const range = max - min;

  const xFor = (i: number) =>
    window.length === 1
      ? w - SPARKLINE_PAD_X
      : SPARKLINE_PAD_X + (i / (window.length - 1)) * innerW;
  const yFor = (v: number) =>
    SPARKLINE_PAD_Y + (1 - (v - min) / range) * innerH;

  const points = window
    .map((v, i) => `${xFor(i).toFixed(2)},${yFor(v).toFixed(2)}`)
    .join(' ');

  // Single sample: just a dot, no polyline (a 1-point polyline is invisible).
  const polyline =
    window.length >= 2
      ? `<polyline points="${points}" fill="none" stroke="${stroke}" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round"/>`
      : '';
  const dots = window
    .map((v, i) =>
      `<circle cx="${xFor(i).toFixed(2)}" cy="${yFor(v).toFixed(2)}" r="1.2" fill="${stroke}"/>`,
    )
    .join('');

  return (
    `<span class="sc-sparkline" title="${tipAttr}" aria-label="${tipAttr}">` +
    `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">` +
    `<title>${tipAttr}</title>` +
    `${polyline}${dots}` +
    `</svg>` +
    `</span>`
  );
}

/**
 * Whether the current val warrants the DESPERATE tag.
 *
 * `pimcSplit=true` suppresses the tag — when the PIMC vote is split, the
 * scalar `confidence` we're judging is one hypothesis's view, not a stable
 * aggregate, so flagging "desperate" risks crying wolf. The PIMC split tag
 * already communicates uncertainty in that case.
 */
export function isDesperate(val: number, pimcSplit: boolean = false): boolean {
  if (!Number.isFinite(val)) return false;
  if (pimcSplit) return false;
  return val < DESPERATE_THRESHOLD;
}

// ────────────────────────────────────────────────────────────────
// computeTrendArrow — single API for the HP-slot trend display
// Added 2026-05-12 for TCG dashboard redesign.
// Returns the arrow glyph, delta in percentage points, and color hint.
// ────────────────────────────────────────────────────────────────

export type TrendDirection = 'rising' | 'falling' | 'flat' | 'collapsing';

export interface TrendArrow {
  direction: TrendDirection;
  /** Unicode arrow glyph: ↗ rising · ↘ falling · → flat · ⚠ collapsing */
  arrow: string;
  /** Win% delta over last 3 turns, in percentage points (e.g. +8, -6, +0) */
  delta: number;
  /** CSS color hint */
  color: string;
}

/**
 * Compute the trend arrow + delta for the HP-slot display.
 * Reads the last few entries of confidence history (0-1 scale) and reports
 * the direction and magnitude over the last 3 entries.
 */
export function computeTrendArrow(history: number[]): TrendArrow {
  if (history.length < 2) {
    return { direction: 'flat', arrow: '→', delta: 0, color: '#707070' };
  }

  // Look at last 3 turns of change (or fewer if history is short)
  const lookback = Math.min(3, history.length - 1);
  const recent = history[history.length - 1];
  const past = history[history.length - 1 - lookback];
  const deltaPct = Math.round((recent - past) * 100);

  // Collapsing: drop > 20pp over the window
  if (deltaPct <= -20) {
    return { direction: 'collapsing', arrow: '⚠', delta: deltaPct, color: '#c8302a' };
  }
  // Rising: gain >= 4pp
  if (deltaPct >= 4) {
    return { direction: 'rising', arrow: '↗', delta: deltaPct, color: '#1a7a2a' };
  }
  // Falling: loss <= -4pp
  if (deltaPct <= -4) {
    return { direction: 'falling', arrow: '↘', delta: deltaPct, color: '#a02020' };
  }
  // Otherwise flat
  return { direction: 'flat', arrow: '→', delta: deltaPct, color: '#707070' };
}
