/**
 * @vitest-environment jsdom
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderTcgCard, type TcgCardProps } from '../../panels/tcg-card';

const FIXTURE: TcgCardProps = {
  recommendedMove: 'Ice Spinner',
  moveType: 'Ice',
  trend: { arrow: '↗', delta: 8, color: '#1a7a2a', direction: 'rising' },
  activeSpecies: 'Iron Valiant',
  turn: 14,
  trendTag: '▲ LAST 3 TURNS',
  hypsTag: '3/4 HYPS',
  winPct: 73,
  llmExplanation: 'OHKOs Samurott-Hisui and removes the terrain.',
  alternatives: [
    { name: 'Ice Spinner', type: 'Ice', votes: 3, voteCap: 4, winPct: 73, category: 'P', isRecommended: true, desc: 'OHKOs Samurott-H' },
    { name: 'Moonblast', type: 'Fairy', votes: 1, voteCap: 4, winPct: 58, category: 'S', isRecommended: false, desc: 'trades into Iron V' },
  ],
  worstThreat: { name: 'Ceaseless Edge', dmgPct: 214, isOhko: true },
  retreatCost: 2,
  sparklineHistory: [0.40, 0.50, 0.55, 0.60, 0.65, 0.73],
  flairChar: '❄',
};

describe('renderTcgCard', () => {
  let host: HTMLDivElement;

  beforeEach(() => {
    host = document.createElement('div');
  });

  it('returns a DOM element with the tcg-card class', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.classList.contains('sc-tcg-card')).toBe(true);
  });

  it('applies the type class derived from moveType', () => {
    const el = renderTcgCard(FIXTURE);
    // Ice → water in TCG mapping
    expect(el.classList.contains('t-water')).toBe(true);
  });

  it('renders the move name', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.querySelector('.card-name')?.textContent).toBe('Ice Spinner');
  });

  it('renders the trend arrow and delta', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.querySelector('.trend-arrow')?.textContent).toBe('↗');
    expect(el.querySelector('.trend-delta')?.textContent).toBe('+8');
  });

  it('renders the win % in the spark strip', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.querySelector('.big-conf')?.textContent).toContain('73');
  });

  it('renders the LLM flavor strip', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.querySelector('.flavor')?.textContent).toContain('OHKOs Samurott-Hisui');
  });

  it('renders one move row per alternative', () => {
    const el = renderTcgCard(FIXTURE);
    expect(el.querySelectorAll('.move').length).toBe(2);
  });

  it('marks the recommended move with the rec class', () => {
    const el = renderTcgCard(FIXTURE);
    const recMoves = el.querySelectorAll('.move.rec');
    expect(recMoves.length).toBe(1);
    expect(recMoves[0].textContent).toContain('Ice Spinner');
  });

  it('renders filled + empty energy orbs per vote count', () => {
    const el = renderTcgCard(FIXTURE);
    const firstMove = el.querySelectorAll('.move')[0];
    const orbs = firstMove.querySelectorAll('.energy');
    expect(orbs.length).toBe(4); // voteCap
    // First 3 are filled (votes=3), last is empty
    expect(orbs[0].classList.contains('empty')).toBe(false);
    expect(orbs[3].classList.contains('empty')).toBe(true);
  });

  it('renders the worst threat in the bottom strip', () => {
    const el = renderTcgCard(FIXTURE);
    const bottom = el.querySelector('.bottom');
    expect(bottom?.textContent).toContain('214%');
    expect(bottom?.textContent).toContain('CEASELESS');
  });
});
