// Unit tests for the PIMC vote-bar renderer.
//
// vitest is configured for `environment: 'node'` (no jsdom), so we use a
// minimal mock element that captures `innerHTML` writes. Renderer is pure
// (innerHTML write only — no event listeners, no children traversal).
//
// Coverage:
//  - aggregateVotes folds K hypotheses into per-move rows.
//  - renderPimcVoteBar produces the consensus / split / single-modal output
//    expected by the panel.
//  - Malformed input does NOT crash (graceful degradation requirement).

import { describe, it, expect } from 'vitest';
import {
  aggregateVotes,
  renderPimcVoteBar,
  type PimcHypothesis,
} from '../../panels/pimc-vote-bar';

function mockEl() {
  const el = { innerHTML: '' } as unknown as HTMLElement;
  return el;
}

const FOUR_AGREE: PimcHypothesis[] = [
  { top_move: 'DIAMONDSTORM-MEGA', value: 0.589, visit_share: 0.589, opp_summary: 'GOLISOPOD @ LIFEORB / EMERGENCYEXIT' },
  { top_move: 'DIAMONDSTORM-MEGA', value: 0.323, visit_share: 0.323, opp_summary: 'GOLISOPOD @ LIFEORB / EMERGENCYEXIT' },
  { top_move: 'DIAMONDSTORM-MEGA', value: 0.343, visit_share: 0.343, opp_summary: 'GOLISOPOD @ LIFEORB / EMERGENCYEXIT' },
  { top_move: 'DIAMONDSTORM-MEGA', value: 0.625, visit_share: 0.625, opp_summary: 'GOLISOPOD @ LIFEORB / EMERGENCYEXIT' },
];

const SPLIT_3_1: PimcHypothesis[] = [
  { top_move: 'EARTHQUAKE', visit_share: 0.55, opp_summary: 'TYRANITAR @ SAND' },
  { top_move: 'EARTHQUAKE', visit_share: 0.61, opp_summary: 'GLISCOR @ TOXICORB' },
  { top_move: 'EARTHQUAKE', visit_share: 0.48, opp_summary: 'EXCADRILL @ FOCUSSASH' },
  { top_move: 'switch:LANDORUS', visit_share: 0.40, opp_summary: 'GARCHOMP @ LIFEORB' },
];

describe('aggregateVotes', () => {
  it('groups all four hypotheses under one move when they agree', () => {
    const rows = aggregateVotes(FOUR_AGREE);
    expect(rows).toHaveLength(1);
    expect(rows[0].move).toBe('DIAMONDSTORM-MEGA');
    expect(rows[0].votes).toBe(4);
    expect(rows[0].oppSummaries).toHaveLength(4);
    expect(rows[0].meanVisitShare).toBeCloseTo((0.589 + 0.323 + 0.343 + 0.625) / 4, 3);
  });

  it('produces one row per unique top_move and sorts by votes', () => {
    const rows = aggregateVotes(SPLIT_3_1);
    expect(rows).toHaveLength(2);
    expect(rows[0].move).toBe('EARTHQUAKE');
    expect(rows[0].votes).toBe(3);
    expect(rows[1].move).toBe('switch:LANDORUS');
    expect(rows[1].votes).toBe(1);
  });

  it('handles empty / non-array input without throwing', () => {
    expect(aggregateVotes([])).toEqual([]);
    // @ts-expect-error -- intentional bad input
    expect(aggregateVotes(null)).toEqual([]);
    // @ts-expect-error -- intentional bad input
    expect(aggregateVotes(undefined)).toEqual([]);
  });

  it('treats missing fields as defaults (no crash)', () => {
    const rows = aggregateVotes([
      { } as PimcHypothesis,
      { top_move: 'TACKLE' } as PimcHypothesis,
      { top_move: 'TACKLE', visit_share: 0.5 } as PimcHypothesis,
    ]);
    // (unknown), TACKLE → two rows, TACKLE has 2 votes
    const tackle = rows.find(r => r.move === 'TACKLE');
    expect(tackle?.votes).toBe(2);
    expect(tackle?.meanVisitShare).toBeCloseTo(0.25, 3);
    const unknown = rows.find(r => r.move === '(unknown)');
    expect(unknown?.votes).toBe(1);
  });
});

describe('renderPimcVoteBar', () => {
  it('renders consensus line "4 of 4 hypotheses agree on: DIAMONDSTORM-MEGA"', () => {
    const el = mockEl();
    renderPimcVoteBar(el, FOUR_AGREE, 'DIAMONDSTORM-MEGA');
    expect(el.innerHTML).toContain('4 of 4 hypotheses agree on');
    expect(el.innerHTML).toContain('DIAMONDSTORM-MEGA');
    expect(el.innerHTML).toContain('PIMC: K=4');
    // Consensus → no "split" class on the header.
    expect(el.innerHTML).not.toContain('sc-pimc-split');
    expect(el.innerHTML).not.toContain('split decision');
  });

  it('flags split decision when not all hypotheses agree', () => {
    const el = mockEl();
    renderPimcVoteBar(el, SPLIT_3_1, 'EARTHQUAKE');
    expect(el.innerHTML).toContain('3 of 4 hypotheses agree on');
    expect(el.innerHTML).toContain('EARTHQUAKE');
    expect(el.innerHTML).toContain('sc-pimc-split');
    expect(el.innerHTML).toContain('split decision');
    // Both rows visible.
    expect(el.innerHTML).toContain('switch:LANDORUS');
  });

  it('renders the empty-state stub for single-modal responses (no breakdown)', () => {
    const el = mockEl();
    renderPimcVoteBar(el, null, 'TACKLE');
    expect(el.innerHTML).toContain('no PIMC data');
    expect(el.innerHTML).not.toContain('hypotheses agree');
  });

  it('renders empty-state for empty array (degrades gracefully)', () => {
    const el = mockEl();
    renderPimcVoteBar(el, [], 'TACKLE');
    expect(el.innerHTML).toContain('no PIMC data');
  });

  it('does not crash on malformed hypothesis records', () => {
    const el = mockEl();
    // Mix of valid + malformed records — should not throw.
    expect(() => renderPimcVoteBar(el, [
      { top_move: 'EQ', visit_share: 0.5, opp_summary: 'foo' },
      // @ts-expect-error -- intentional bad shape
      null,
      // @ts-expect-error -- intentional bad shape
      { visit_share: 'not-a-number', top_move: undefined },
      {},
    ] as PimcHypothesis[], null)).not.toThrow();
    expect(el.innerHTML).toContain('PIMC: K=4');
  });

  it('escapes HTML in move names and opp summaries (XSS guard)', () => {
    const el = mockEl();
    renderPimcVoteBar(el, [
      { top_move: '<img src=x>', visit_share: 0.5, opp_summary: '<script>alert(1)</script>' },
    ], '<img src=x>');
    expect(el.innerHTML).not.toContain('<img src=x>');
    expect(el.innerHTML).not.toContain('<script>');
    expect(el.innerHTML).toContain('&lt;img');
    expect(el.innerHTML).toContain('&lt;script');
  });

  it('falls back to first row top_move when bestMove is missing', () => {
    const el = mockEl();
    renderPimcVoteBar(el, FOUR_AGREE, null);
    expect(el.innerHTML).toContain('DIAMONDSTORM-MEGA');
    expect(el.innerHTML).toContain('4 of 4 hypotheses agree');
  });
});

describe('renderPimcVoteBar — single-modal regression guard', () => {
  // Verifies the documented "no regression" requirement: when the engine
  // response shape lacks `pimcBreakdown` (today's default for non-PIMC
  // proxy mode), the renderer produces *only* the empty stub. The
  // surrounding panel keeps its standard rec display untouched.
  it('returns only the empty-state innerHTML and writes nothing else', () => {
    const el = mockEl();
    el.innerHTML = '<div>existing-content</div>';
    renderPimcVoteBar(el, undefined, 'WHATEVER');
    // Existing content was overwritten with the empty stub — no leak.
    expect(el.innerHTML).toBe('<div class="sc-empty">no PIMC data (single-modal response)</div>');
  });
});
