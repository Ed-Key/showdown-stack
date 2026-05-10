// Unit tests for the val-trend helpers.
//
// These functions are pure — they take a numeric history (most-recent last)
// and return classification labels / display glyphs. No DOM, no proxy, no
// engine: trivial to exercise.
//
// Coverage:
//  - computeTrend: rising / falling / collapsing / flat / null edge cases.
//  - appendVal: cap, NaN guard, immutability.
//  - isDesperate: threshold + PIMC-split suppression.
//  - formatTrendArrow / formatTrendTitle: stable mapping for the renderer.

import { describe, it, expect } from 'vitest';
import {
  appendVal, computeTrend, formatTrendArrow, formatTrendTitle, isDesperate,
  VAL_HISTORY_CAP, DESPERATE_THRESHOLD,
} from '../../lib/val-trend';

describe('computeTrend', () => {
  it('returns null when history is empty', () => {
    expect(computeTrend([])).toBe(null);
  });

  it('returns null on the first turn (only one sample, no prior to compare)', () => {
    expect(computeTrend([0.7])).toBe(null);
  });

  it('returns "rising" when val is climbing > 0.05 over the last two turns', () => {
    expect(computeTrend([0.50, 0.60])).toBe('rising');
    expect(computeTrend([0.40, 0.50, 0.62])).toBe('rising');
  });

  it('returns "falling" when val is dropping > 0.05 over the last two turns', () => {
    expect(computeTrend([0.70, 0.60])).toBe('falling');
    expect(computeTrend([0.80, 0.70, 0.60])).toBe('falling');
  });

  it('returns "flat" when val moved within ±0.05 over the last two turns', () => {
    expect(computeTrend([0.55, 0.56])).toBe('flat');
    expect(computeTrend([0.60, 0.58, 0.59])).toBe('flat');
  });

  it('returns "collapsing" when val dropped > 0.20 over two turns', () => {
    expect(computeTrend([0.70, 0.45])).toBe('collapsing');
    expect(computeTrend([0.61, 0.59, 0.30])).toBe('collapsing');
  });

  it('matches the postmortem-style scenario (0.61 → 0.59 → 0.50) as falling, not collapsing', () => {
    // The motivating example from the prompt: engine honestly reporting a
    // collapsing line, but it's a 0.11 drop over 2 turns, not a single
    // 0.20+ cliff. Should surface as "falling" so the player still gets
    // the warning without misclassifying it as a collapse.
    expect(computeTrend([0.61, 0.59, 0.50])).toBe('falling');
  });

  it('skips non-finite samples gracefully (returns null when current is NaN)', () => {
    expect(computeTrend([0.5, NaN])).toBe(null);
    expect(computeTrend([NaN, 0.5])).toBe(null);
  });

  it('uses 2- and 3-back averaging when history is long enough', () => {
    // Average delta = ((0.50-0.40) + (0.50-0.30)) / 2 = 0.15 → rising.
    expect(computeTrend([0.30, 0.40, 0.50])).toBe('rising');
  });

  it('flat dominates when neither rising nor falling threshold is crossed', () => {
    // delta2 = +0.02, average = +0.025 → flat.
    expect(computeTrend([0.55, 0.56, 0.58])).toBe('flat');
  });
});

describe('appendVal', () => {
  it('appends a new sample without mutating the input', () => {
    const a = [0.5, 0.6];
    const b = appendVal(a, 0.7);
    expect(a).toEqual([0.5, 0.6]);
    expect(b).toEqual([0.5, 0.6, 0.7]);
  });

  it('caps the array at VAL_HISTORY_CAP entries (keeps the most recent)', () => {
    const long = Array.from({ length: VAL_HISTORY_CAP + 5 }, (_, i) => i / 100);
    const next = appendVal(long, 0.99);
    expect(next.length).toBe(VAL_HISTORY_CAP);
    expect(next[next.length - 1]).toBe(0.99);
  });

  it('skips NaN / non-finite samples (returns a copy of input)', () => {
    const a = [0.4];
    const b = appendVal(a, NaN);
    expect(b).toEqual([0.4]);
    expect(b).not.toBe(a);
    expect(appendVal(a, Infinity)).toEqual([0.4]);
  });
});

describe('isDesperate', () => {
  it('returns true when val < threshold', () => {
    expect(isDesperate(0.10)).toBe(true);
    expect(isDesperate(0.25)).toBe(true);
    expect(isDesperate(DESPERATE_THRESHOLD - 0.001)).toBe(true);
  });

  it('returns false when val ≥ threshold (boundary is inclusive of healthy)', () => {
    expect(isDesperate(0.30)).toBe(false);
    expect(isDesperate(0.35)).toBe(false);
    expect(isDesperate(0.85)).toBe(false);
  });

  it('returns false when pimcSplit is true, regardless of low val', () => {
    // PIMC split → suppress DESPERATE: the scalar val under hedged
    // hypotheses is misleading; the split tag already signals uncertainty.
    expect(isDesperate(0.05, true)).toBe(false);
    expect(isDesperate(0.20, true)).toBe(false);
  });

  it('returns false on non-finite vals', () => {
    expect(isDesperate(NaN)).toBe(false);
    expect(isDesperate(Infinity)).toBe(false);
  });
});

describe('formatTrendArrow', () => {
  it('maps each trend label to its glyph', () => {
    expect(formatTrendArrow('rising')).toBe('↗');
    expect(formatTrendArrow('falling')).toBe('↘');
    expect(formatTrendArrow('collapsing')).toBe('⚠');
  });

  it('renders no glyph for flat / null (degrades cleanly on turn 1)', () => {
    expect(formatTrendArrow('flat')).toBe('');
    expect(formatTrendArrow(null)).toBe('');
  });
});

describe('formatTrendTitle', () => {
  it('returns a non-empty tooltip for every meaningful state', () => {
    expect(formatTrendTitle('rising').length).toBeGreaterThan(0);
    expect(formatTrendTitle('falling').length).toBeGreaterThan(0);
    expect(formatTrendTitle('collapsing').length).toBeGreaterThan(0);
    expect(formatTrendTitle('flat').length).toBeGreaterThan(0);
  });

  it('returns an empty tooltip for null (nothing to render)', () => {
    expect(formatTrendTitle(null)).toBe('');
  });
});

describe('integration — feed + trend across simulated turns', () => {
  // End-to-end-ish: walk a series of "engine final" vals through appendVal
  // and check trend at each step. Mirrors how content.ts uses these helpers.

  it('first turn → null (no history); second turn surfaces rising/falling', () => {
    let h: number[] = [];
    h = appendVal(h, 0.55);
    expect(computeTrend(h)).toBe(null);
    h = appendVal(h, 0.65);
    expect(computeTrend(h)).toBe('rising');
  });

  it('catches the "engine honestly collapsing" motif over a 3-turn window', () => {
    let h: number[] = [];
    h = appendVal(h, 0.61);
    expect(computeTrend(h)).toBe(null);  // T1: no history yet
    h = appendVal(h, 0.59);
    expect(computeTrend(h)).toBe('flat'); // T2: small wobble
    h = appendVal(h, 0.50);
    // T3: average delta over 2-back+3-back is more negative than -0.05 → falling.
    expect(computeTrend(h)).toBe('falling');
  });

  it('catches a one-shot collapse (>0.20 in a single turn)', () => {
    let h: number[] = [0.65, 0.60];
    expect(computeTrend(h)).toBe('flat');
    h = appendVal(h, 0.30);
    expect(computeTrend(h)).toBe('collapsing');
  });

  it('history capped at VAL_HISTORY_CAP — older samples drop off', () => {
    let h: number[] = [];
    for (let i = 0; i < VAL_HISTORY_CAP + 3; i++) h = appendVal(h, 0.5);
    expect(h.length).toBe(VAL_HISTORY_CAP);
  });
});
