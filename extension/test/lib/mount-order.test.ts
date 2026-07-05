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
import { renderMatchupPlanCard } from '../../panels/matchup-plan-card';
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

// Minimal MatchupPlan shape renderMatchupPlanCard reads directly (see
// test/lib/plan-fit.test.ts `basePlan` for a complete literal).
const FIXTURE_RESPONSE = {
  source: 'model',
  model: 'claude-sonnet-4-6',
  provider: 'anthropic',
  plan: {
    archetype: 'bulky stall/control',
    confidence: 'high',
    summary: 'Opponent preview shows disruption and recovery loops.',
    winPath: 'Create progress before passive control stabilizes.',
    recommendedLead: { pokemon: 'Garchomp', rating: 'safe', reason: 'Default information lead.' },
    backupLeads: [],
    avoidLeads: [],
    leadRules: [],
    preserveTargets: [],
    mainThreats: [],
    dangerRules: [],
    earlyPriorities: [],
    uncertainties: [],
  },
} as any;

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
  // Banner is now an inside-the-TCG-card child, mounted as first child of
  // .sc-tcg-card. Any previous instance (whether inside or top-level) is
  // stripped first. Returns silently if no TCG card exists yet.
  panel.querySelector('.sc-conflict-banner')?.remove();
  const card = panel.querySelector<HTMLElement>('.sc-tcg-card');
  if (!card) return;
  card.insertBefore(banner, card.firstChild);
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

  it('conflict banner mounts INSIDE the TCG card as its first child', () => {
    mountTcgCard(panel, renderTcgCard(TCG_FIXTURE));
    mountThreats(panel, renderThreatsPanel(THREATS_FIXTURE));
    mountPv(panel, renderPvChain(PV_FIXTURE));
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
    // Tree-order traversal: threats → PV → TCG card → its banner child.
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-tcg-card', 'sc-conflict-banner',
    ]);
    // Banner is actually a child of the TCG card, not a sibling.
    const card = panel.querySelector('.sc-tcg-card')!;
    expect(card.querySelector(':scope > .sc-conflict-banner')).not.toBeNull();
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

  it('TCG card replace discards the inside-card conflict banner (re-mount required)', () => {
    // Banner is a child of the TCG card, so swapping the card removes it.
    // Production code handles this by calling renderConflict() after
    // renderUpdate() on every engine tick, so the banner is re-attached
    // immediately when there's still a conflict to surface.
    mountTcgCard(panel, renderTcgCard({ ...TCG_FIXTURE, turn: 15 }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-tcg-card',
    ]);
    expect(panel.querySelectorAll('.sc-tcg-card').length).toBe(1);
    // Re-mount the banner — it should land back inside the fresh card.
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-tcg-card', 'sc-conflict-banner',
    ]);
  });

  it('threats panel replace preserves overall ordering', () => {
    mountThreats(panel, renderThreatsPanel({
      onField: [{ move: 'Earthquake', source: 'Garchomp', target: 'Heatran', dmgPct: 105, isOhko: true, source_seen: true }],
      incoming: [],
    }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-tcg-card', 'sc-conflict-banner',
    ]);
    expect(panel.querySelectorAll('.sc-trainer-card').length).toBe(1);
  });

  it('conflict banner replace preserves overall ordering', () => {
    mountConflict(panel, renderConflictBanner({ severity: 'POSSIBLE', reason: 'new' }));
    expect(panelOrder(panel)).toEqual([
      'sc-trainer-card', 'sc-pv-card', 'sc-tcg-card', 'sc-conflict-banner',
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

  it('conflict banner is dropped when no TCG card exists to host it', () => {
    // The banner has no meaningful position without the recommendation it
    // warns about — there's no fallback mount site.
    const panel = document.createElement('div');
    const child = document.createElement('div');
    child.className = 'placeholder';
    panel.appendChild(child);
    mountConflict(panel, renderConflictBanner({ severity: 'STRONG', reason: 'r' }));
    expect(panel.querySelector('.sc-conflict-banner')).toBeNull();
  });
});

describe('matchup plan card persistence', () => {
  it('matchup plan card stays mounted when re-rendered mid-battle', () => {
    const root = document.createElement('div');
    const card1 = renderMatchupPlanCard(FIXTURE_RESPONSE);
    mountOrReplace(root, {
      newEl: card1,
      replaceTargets: ['.sc-matchup-plan-card'],
      anchors: [],
      fallback: (r, el) => r.prepend(el),
    });
    // Simulate a turn-5 re-render of the same battle: replaces in place, never removes.
    const card2 = renderMatchupPlanCard(FIXTURE_RESPONSE);
    mountOrReplace(root, {
      newEl: card2,
      replaceTargets: ['.sc-matchup-plan-card'],
      anchors: [],
      fallback: (r, el) => r.prepend(el),
    });
    expect(root.querySelectorAll('.sc-matchup-plan-card').length).toBe(1);
  });
});
