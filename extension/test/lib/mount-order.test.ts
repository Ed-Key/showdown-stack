/** @vitest-environment jsdom */
/**
 * Smoke test for the renderUpdate() panel-mount sequence in
 * entrypoints/content.ts. Locks in the current DOM order so that the Phase 4
 * `mountPanels()` extraction (or any other refactor that touches the four
 * insertBefore / replaceWith blocks) can't silently rearrange the visual
 * hierarchy.
 *
 * Source-of-truth references in content.ts:
 *   - L78-90:    initial panel HTML with .sc-pinned scaffolding
 *   - L1208-1231 TCG card mount (replaces .sc-best on first call)
 *   - L1238-1257 threats panel mount (inserted ABOVE .sc-tcg-card)
 *   - L1263-1286 PV chain mount (after threats, before TCG card)
 *   - L951-978   conflict banner mount (before TCG card)
 *
 * The mount helpers below mirror that logic verbatim. When Phase 4 extracts
 * a shared `mountOrReplace()` into panels/_mount.ts, replace these mirrors
 * with imports; the test cases themselves should still hold.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderTcgCard, type TcgCardProps } from '../../panels/tcg-card';
import { renderThreatsPanel } from '../../panels/threats-panel';
import { renderPvChain } from '../../panels/pv-chain';
import { renderConflictBanner } from '../../panels/conflict-banner';

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

// ---- Mount helpers (mirror content.ts) -----------------------------------

function mountTcgCard(panel: HTMLElement, card: HTMLElement) {
  // content.ts:1222-1230
  const existingCard = panel.querySelector('.sc-tcg-card');
  const oldBest = panel.querySelector('.sc-best');
  if (existingCard) existingCard.replaceWith(card);
  else if (oldBest) oldBest.replaceWith(card);
  else panel.appendChild(card);
}

function mountThreats(panel: HTMLElement, threats: HTMLElement) {
  // content.ts:1244-1255
  const tcgCard = panel.querySelector('.sc-tcg-card');
  const oldThreats = panel.querySelector('.sc-trainer-card');
  if (oldThreats) oldThreats.replaceWith(threats);
  else if (tcgCard && tcgCard.parentElement) tcgCard.parentElement.insertBefore(threats, tcgCard);
  else panel.appendChild(threats);
}

function mountPv(panel: HTMLElement, pv: HTMLElement) {
  // content.ts:1275-1283
  const oldPv = panel.querySelector('.sc-pv-card');
  const threatsEl = panel.querySelector('.sc-trainer-card');
  if (oldPv) oldPv.replaceWith(pv);
  else if (threatsEl && threatsEl.parentElement) threatsEl.parentElement.insertBefore(pv, threatsEl.nextSibling);
  else panel.appendChild(pv);
}

function mountConflict(panel: HTMLElement, banner: HTMLElement) {
  // content.ts:960-977
  const oldBanner = panel.querySelector('.sc-conflict-banner');
  if (oldBanner) {
    oldBanner.replaceWith(banner);
    return;
  }
  const overlay = panel.querySelector('.sc-status-overlay');
  if (overlay && overlay.parentElement) {
    overlay.parentElement.insertBefore(banner, overlay);
  } else {
    const card = panel.querySelector('.sc-tcg-card');
    if (card && card.parentElement) {
      card.parentElement.insertBefore(banner, card);
    } else {
      panel.insertBefore(banner, panel.firstChild);
    }
  }
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
