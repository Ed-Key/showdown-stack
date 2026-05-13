/** @vitest-environment jsdom */
import { describe, it, expect } from 'vitest';
import { renderPvChain, type PvChainProps } from '../../panels/pv-chain';

const FIXTURE: PvChainProps = {
  steps: [
    { move: 'ICE SPINNER', side: 'me' },
    { move: 'AQUA CUTTER', side: 'opp' },
    { move: 'EXTREME SPEED', side: 'me' },
    { move: 'SUCKER PUNCH', side: 'opp' },
  ],
  depth: 4,
  sims: 182400,
};

describe('renderPvChain', () => {
  it('returns an element with the pv-card class', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.classList.contains('sc-pv-card')).toBe(true);
  });

  it('renders the engine PV label', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.textContent).toContain('ENGINE LINE');
  });

  it('renders one pv-step per move', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.querySelectorAll('.pv-step').length).toBe(4);
  });

  it('color-codes me vs opp', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.querySelectorAll('.pv-step.me').length).toBe(2);
    expect(el.querySelectorAll('.pv-step.opp').length).toBe(2);
  });

  it('renders depth and sims meta', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.textContent).toContain('depth 4');
    expect(el.textContent).toContain('182K sims');
  });

  it('renders arrows between steps', () => {
    const el = renderPvChain(FIXTURE);
    expect(el.querySelectorAll('.pv-arrow').length).toBe(3);
  });
});
