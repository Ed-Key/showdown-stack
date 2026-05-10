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
