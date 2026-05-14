import { escapeHtml } from './_shared';

export interface PvStep {
  move: string;
  side: 'me' | 'opp';
}

export interface PvChainProps {
  steps: PvStep[];
  depth: number;
  sims: number;
}

function formatSims(sims: number): string {
  if (sims >= 1_000_000) return `${(sims / 1_000_000).toFixed(1)}M`;
  if (sims >= 1_000) return `${Math.round(sims / 1000)}K`;
  return String(sims);
}

export function renderPvChain(p: PvChainProps): HTMLElement {
  const el = document.createElement('div');
  el.className = 'sc-pv-card';
  const stepsHtml = p.steps
    .map((s, i) => {
      const sideClass = s.side === 'me' ? 'me' : 'opp';
      const bullet = s.side === 'me' ? '●' : '○';
      const stepHtml = `<span class="pv-step ${sideClass}">${bullet} ${escapeHtml(s.move)}</span>`;
      const arrow = i < p.steps.length - 1 ? '<span class="pv-arrow">→</span>' : '';
      return stepHtml + arrow;
    })
    .join('');
  el.innerHTML = `
    <div class="pv-inner">
      <div class="pv-header">
        <span class="pv-label">🔮 ENGINE LINE (PV)</span>
        <span class="pv-meta">depth ${p.depth} · ${formatSims(p.sims)} sims</span>
      </div>
      <div class="pv-chain">${stepsHtml}</div>
    </div>
  `;
  return el;
}
