/** @vitest-environment jsdom */
/**
 * Smoke test for the renderUpdate() panel-mount sequence in
 * entrypoints/content.ts. Locks in the current DOM order so future refactors
 * can't silently rearrange the visual hierarchy.
 *
 * Originally these tests mirrored the inline mount logic in content.ts.
 * Phase 4 extracted that logic into `lib/panel-mount.ts:mountOrReplace`, so
 * the helpers below now drive the real production helper with the same
 * configs content.ts uses — single source of truth for the mount strategy.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderTcgCard, type TcgCardProps } from '../../panels/tcg-card';
import { renderThreatsPanel } from '../../panels/threats-panel';
import { renderPvChain } from '../../panels/pv-chain';
import { renderConflictBanner } from '../../panels/conflict-banner';
import { mountOrReplace } from '../../lib/panel-mount';

const TCG_FIXTURE: TcgCardProps = {
  activeType: 'Fairy',
  trend: { arrow: '↗', delta: 8, color: '#1a7a2a', direction: 'rising' },
  activeSpecies: 'Iron Valiant',
  headerSpecies: 'Iron Valiant',
  isSwitchRec: false,
  turn: 14,
  trendTag: '',
  hypsTag: '',
  winPct: 73,
  llmExplanation: '',
  moves: [
    { name: 'Ice Spinner', type: 'Ice', votes: 3, voteCap: 4, winPct: 73, category: 'P', isRecommended: true, desc: '' },
  ],
  worstThreat: { name: 'Ceaseless Edge', dmgPct: 214, isOhko: true },
  retreatCost: 2,
  sparklineHistory: [],
  flairChar: '',
};

const THREATS_FIXTURE = { onField: [], incoming: [] };
const PV_FIXTURE = { steps: [{ move: 'ICE SPINNER', side: 'me' as const }], depth: 4, sims: 1024 };

function buildPanelFixture(): HTMLDivElement {
  // Mirrors content.ts:78-90 — the initial panel before any engine update.
  const panel = document.createElement('div');
  panel.id = 'sc-panel';
  panel.innerHTML = `
    <div class="sc-pinned">
      <div class="sc-header">Copilot — idle</div>
      <div class="sc-conflict" style="display:none"></div>
      <div class="sc-best">—</div>
      <div class="sc-stats">—</div>
      <div class="sc-pv">PV: —</div>
      <div class="sc-alts">—</div>
      <div class="sc-pimc-pinned"></div>
    </div>
  `;
  return panel;
}

// ---- Mount helpers (call the real production helper) --------------------
// Each helper passes the same MountOptions config that content.ts uses, so
// the test drives the live mount strategy end-to-end.

function mountTcgCard(panel: HTMLElement, card: HTMLElement) {
  mountOrReplace(panel, {
    newEl: card,
    replaceTargets: ['.sc-tcg-card', '.sc-best'],
  });
}

function mountThreats(panel: HTMLElement, threats: HTMLElement) {
  mountOrReplace(panel, {
    newEl: threats,
    replaceTargets: ['.sc-trainer-card'],
    anchors: [{ selector: '.sc-tcg-card', position: 'before' }],
  });
}

function mountPv(panel: HTMLElement, pv: HTMLElement) {
  mountOrReplace(panel, {
    newEl: pv,
    replaceTargets: ['.sc-pv-card'],
    anchors: [{ selector: '.sc-trainer-card', position: 'after' }],
  });
}

function mountConflict(panel: HTMLElement, banner: HTMLElement) {
  mountOrReplace(panel, {
    newEl: banner,
    replaceTargets: ['.sc-conflict-banner'],
    anchors: [
      { selector: '.sc-status-overlay', position: 'before' },
      { selector: '.sc-tcg-card', position: 'before' },
    ],
    fallback: (root, el) => root.insertBefore(el, root.firstChild),
  });
}

function panelOrder(panel: HTMLElement): string[] {
  return Array.from(
    panel.querySelectorAll('.sc-conflict-banner, .sc-trainer-card, .sc-pv-card, .sc-tcg-card'),
  ).map((el) => (el as HTMLElement).className.split(' ')[0]);
}

// ---- Tests ----------------------------------------------------------------

describe('panel mount sequence — first engine update', () => {
  let panel: HTMLDivElement;
  beforeEach(() => { panel = buildPanelFixture(); });

  it('TCG card replaces .sc-best on first mount', () => {
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    expect(panel.querySelector('.sc-best')).toBeNull();
    expect(panel.querySelector('.sc-tcg-card')).not.toBeNull();
  });

  it('threats panel mounts ABOVE the TCG card', () => {
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    expect(panelOrder(panel)).toEqual(['sc-trainer-card', 'sc-tcg-card']);
  });

  it('PV chain mounts between threats and TCG card', () => {
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    mountPv(panel, renderPvChain(PV_FIXTURE));
    expect(panelOrder(panel)).toEqual(['sc-trainer-card', 'sc-pv-card', 'sc-tcg-card']);
  });

  it('conflict banner mounts above TCG card (and below threats + PV)', () => {
    // This codifies the current behavior. The conflict insert logic targets
    // .sc-tcg-card.previousSibling (which is .sc-pv-card after PV mounts).
    // If/when Phase 5 changes the intent to "conflict at very top", this
    // test must change too.
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    mountPv(panel, renderPvChain(PV_FIXTURE));
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-conflict-banner', 'sc-tcg-card',
    ]);
  });
});

describe('panel mount sequence — subsequent engine updates', () => {
  let panel: HTMLDivElement;
  beforeEach(() => {
    panel = buildPanelFixture();
    // Bootstrap state from a first update.
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    mountPv(panel, renderPvChain(PV_FIXTURE));
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
  });

  it('TCG card replace preserves overall ordering', () => {
    mountTcgCard(panel, renderTcgCard({ ...TCG_FIXTURE, turn: 15 }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-conflict-banner', 'sc-tcg-card',
    ]);
    // Exactly one TCG card (no duplicates from the replace).
    expect(panel.querySelectorAll('.sc-tcg-card').length).toBe(1);
  });

  it('threats panel replace preserves overall ordering', () => {
    mountThreats(panel, renderThreatsPanel({
      onField: [{ move: 'Earthquake', source: 'Garchomp', target: 'Heatran', dmgPct: 105, isOhko: true, source_seen: true }],
      incoming: [],
    }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-conflict-banner', 'sc-tcg-card',
    ]);
    expect(panel.querySelectorAll('.sc-trainer-card').length).toBe(1);
  });

  it('conflict banner replace preserves overall ordering', () => {
    mountConflict(panel, renderConflictBanner({ severity: 'POSSIBLE', reason: 'new' }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-conflict-banner', 'sc-tcg-card',
    ]);
    expect(panel.querySelectorAll('.sc-conflict-banner').length).toBe(1);
  });
});

describe('panel mount — degraded fixtures', () => {
  it('TCG card appends when .sc-best is missing (panel reset edge case)', () => {
    const panel = document.createElement('div');
    panel.innerHTML = `<div class="sc-pinned"></div>`;
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    expect(panel.querySelector('.sc-tcg-card')).not.toBeNull();
  });

  it('threats panel appends when no TCG card exists yet', () => {
    const panel = document.createElement('div');
    panel.innerHTML = `<div class="sc-pinned"></div>`;
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    expect(panel.querySelector('.sc-trainer-card')).not.toBeNull();
  });

  it('conflict banner falls back to panel.insertBefore when no TCG card', () => {
    const panel = document.createElement('div');
    const child = document.createElement('div');
    child.className = 'placeholder';
    panel.appendChild(child);
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
    // Inserted before the first child of panel.
    expect(panel.firstElementChild?.classList.contains('sc-conflict-banner')).toBe(true);
  });
});
