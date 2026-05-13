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
  renderSparkline,
  VAL_HISTORY_CAP, DESPERATE_THRESHOLD,
  SPARKLINE_WIDTH, SPARKLINE_HEIGHT, SPARKLINE_MAX_POINTS,
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

describe('renderSparkline', () => {
  // Pure-string assertions — vitest runs in node mode (no jsdom), so we
  // inspect the returned markup directly rather than mounting the SVG.
  // We intentionally don't lock down exact pixel coordinates; we assert on
  // structural facts (point count, line presence, color, tooltip format)
  // so trivial geometry tweaks don't churn the test.

  it('returns a placeholder span for empty history (no SVG, no crash)', () => {
    const out = renderSparkline([]);
    expect(out).toContain('sc-sparkline-empty');
    expect(out).not.toContain('<svg');
    // Em-dash placeholder so the line layout stays stable.
    expect(out).toContain('—');
  });

  it('treats a non-array input as empty (defensive)', () => {
    // @ts-expect-error — exercising the runtime guard path
    expect(renderSparkline(null)).toContain('sc-sparkline-empty');
    // @ts-expect-error — exercising the runtime guard path
    expect(renderSparkline(undefined)).toContain('sc-sparkline-empty');
  });

  it('renders a single dot (no polyline) for a 1-sample history', () => {
    const out = renderSparkline([0.65]);
    expect(out).toContain('<svg');
    expect(out).toContain('<circle');
    expect(out).not.toContain('<polyline');
    // Tooltip shows the one sample.
    expect(out).toContain('T1: 0.65');
  });

  it('renders a polyline + N dots for a multi-sample history', () => {
    const out = renderSparkline([0.65, 0.60, 0.50, 0.35, 0.20]);
    expect(out).toContain('<polyline');
    // One <circle> per data point.
    const dots = (out.match(/<circle /g) || []).length;
    expect(dots).toBe(5);
  });

  it('uses the SVG dimensions defined by the geometry constants', () => {
    const out = renderSparkline([0.5, 0.6]);
    expect(out).toContain(`width="${SPARKLINE_WIDTH}"`);
    expect(out).toContain(`height="${SPARKLINE_HEIGHT}"`);
    expect(out).toContain(`viewBox="0 0 ${SPARKLINE_WIDTH} ${SPARKLINE_HEIGHT}"`);
  });

  it('caps the visible window at SPARKLINE_MAX_POINTS (older samples drop off)', () => {
    const long = Array.from({ length: SPARKLINE_MAX_POINTS + 4 }, (_, i) => 0.3 + i * 0.01);
    const out = renderSparkline(long);
    const dots = (out.match(/<circle /g) || []).length;
    expect(dots).toBe(SPARKLINE_MAX_POINTS);
  });

  it('formats the tooltip as "T1: 0.65 → T2: 0.60 → ..." with two-decimal vals', () => {
    const out = renderSparkline([0.65, 0.60, 0.50]);
    // Default startTurn=1.
    expect(out).toContain('T1: 0.65 → T2: 0.60 → T3: 0.50');
  });

  it('shifts tooltip turn labels when startTurn is supplied', () => {
    const out = renderSparkline([0.40, 0.30], 7);
    expect(out).toContain('T7: 0.40 → T8: 0.30');
  });

  it('shifts tooltip turn labels for long histories that get windowed', () => {
    // 10 samples, window of 8 → labels start at startTurn + (10-8) = startTurn+2.
    const long = Array.from({ length: 10 }, (_, i) => 0.5 + i * 0.01);
    const out = renderSparkline(long, 1);
    // First visible sample is index 2 of the original → should label as T3.
    expect(out).toContain('T3: 0.52');
    expect(out).not.toContain('T1: 0.50');
  });

  it('skips non-finite samples instead of crashing', () => {
    const out = renderSparkline([0.5, NaN, 0.6, Infinity]);
    // 2 finite samples → 2 dots, 1 polyline.
    const dots = (out.match(/<circle /g) || []).length;
    expect(dots).toBe(2);
    expect(out).toContain('<polyline');
  });

  it('color-shifts the stroke by trend (rising → green, falling → amber, collapsing → red)', () => {
    expect(renderSparkline([0.3, 0.4, 0.55])).toContain('#7fdc7f');   // rising
    expect(renderSparkline([0.7, 0.6, 0.5])).toContain('#ffb060');    // falling
    expect(renderSparkline([0.7, 0.45])).toContain('#ff6a6a');        // collapsing
    expect(renderSparkline([0.5, 0.51, 0.5])).toContain('#9aa0a6');   // flat → grey
  });

  it('does not crash when all samples are equal (no divide-by-zero)', () => {
    const out = renderSparkline([0.5, 0.5, 0.5, 0.5]);
    expect(out).toContain('<polyline');
    // No NaN / Infinity should leak into coordinates.
    expect(out).not.toMatch(/NaN|Infinity/);
  });

  it('escapes the tooltip attribute defensively (no raw quotes / angle brackets)', () => {
    // The tooltip is built from numeric sample values, so the only chars
    // that show up are digits, dots, colons, arrows. The escape pass is a
    // belt-and-suspenders check; this test just confirms we don't ship
    // bare quotes that would break the title="" attribute.
    const out = renderSparkline([0.5, 0.6]);
    const titleMatch = out.match(/title="([^"]*)"/);
    expect(titleMatch).not.toBeNull();
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

// ────────────────────────────────────────────────────────────────
// computeTrendArrow tests (Task 4 — TCG dashboard HP-slot)
// ────────────────────────────────────────────────────────────────

import { computeTrendArrow } from '../../lib/val-trend';

describe('computeTrendArrow', () => {
  it('returns rising arrow with positive delta for rising history', () => {
    // confidence rising from 0.50 → 0.65 over last 3 turns
    const r = computeTrendArrow([0.40, 0.45, 0.50, 0.55, 0.60, 0.65]);
    expect(r.direction).toBe('rising');
    expect(r.arrow).toBe('↗');
    expect(r.delta).toBeGreaterThan(0);
  });

  it('returns falling arrow with negative delta for falling history', () => {
    const r = computeTrendArrow([0.80, 0.75, 0.70, 0.65, 0.60, 0.55]);
    expect(r.direction).toBe('falling');
    expect(r.arrow).toBe('↘');
    expect(r.delta).toBeLessThan(0);
  });

  it('returns flat arrow with near-zero delta for stable history', () => {
    const r = computeTrendArrow([0.50, 0.51, 0.49, 0.50, 0.51, 0.50]);
    expect(r.direction).toBe('flat');
    expect(r.arrow).toBe('→');
  });

  it('returns collapsing arrow for sharp drop >0.20', () => {
    const r = computeTrendArrow([0.80, 0.78, 0.75, 0.70, 0.50, 0.45]);
    expect(r.direction).toBe('collapsing');
    expect(r.arrow).toBe('⚠');
    expect(r.delta).toBeLessThan(-20);
  });

  it('handles short history (<3 entries) without crashing', () => {
    const r = computeTrendArrow([0.50]);
    expect(r.direction).toBe('flat');
    expect(r.delta).toBe(0);
  });

  it('handles empty history without crashing', () => {
    const r = computeTrendArrow([]);
    expect(r.direction).toBe('flat');
    expect(r.delta).toBe(0);
  });

  it('delta is in percentage points (0-100 scale, not 0-1)', () => {
    // 0.50 → 0.65 = +15 percentage points, not +0.15
    const r = computeTrendArrow([0.40, 0.45, 0.50, 0.55, 0.60, 0.65]);
    expect(r.delta).toBeGreaterThanOrEqual(10);
    expect(r.delta).toBeLessThanOrEqual(20);
  });

  it('returns flat with delta 0 for NaN-containing history', () => {
    const r = computeTrendArrow([0.40, NaN, 0.50, 0.55, 0.60, 0.65]);
    expect(r.direction).toBe('flat');
    expect(r.delta).toBe(0);
  });

  it('returns flat with delta 0 for non-array input', () => {
    // @ts-expect-error — testing runtime guard against bad caller input
    const r = computeTrendArrow(null);
    expect(r.direction).toBe('flat');
    expect(r.delta).toBe(0);
  });
});
