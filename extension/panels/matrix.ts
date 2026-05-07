// extension/panels/matrix.ts
// Pure DOM-rendering for the damage matrix card. Given a DamageMatrix
// (or null while waiting for data), writes a compact HTML table into
// the target element. No fetching, no state — caller drives refresh.

import type { DamageMatrix, MatrixCell } from '../lib/damage-matrix';

export function renderMatrix(target: HTMLElement, matrix: DamageMatrix | null): void {
  if (!matrix) {
    target.innerHTML = '<div class="sc-matrix-empty">waiting for belief data…</div>';
    return;
  }
  // group cells: rows = attackers, cols = defenders, value = best cell per (atk,def)
  const attackers = unique(matrix.cells.map(c => c.attacker));
  const defenders = unique(matrix.cells.map(c => c.defender));
  const cellByPair = new Map<string, MatrixCell>();
  for (const c of matrix.cells) {
    const key = `${c.attacker}::${c.defender}`;
    const existing = cellByPair.get(key);
    if (!existing || c.dmgPctMax > existing.dmgPctMax) cellByPair.set(key, c);
  }
  // Legend explains how to read the matrix: rows are opp Pokemon attacking,
  // cols are your Pokemon defending. Asterisk marks chaos-modal moves (move
  // not yet revealed, so this is a guess from Smogon usage stats).
  let html = `<div class="sc-matrix-legend">
    <div>rows = opp attacking · cols = your team</div>
    <div><span class="sc-matrix-legend-chip sc-ohko">OHKO</span><span class="sc-matrix-legend-chip sc-2hko">2HKO</span><span class="sc-matrix-legend-chip sc-warn">≥50%</span><span class="sc-matrix-legend-chip">&lt;50%</span><span class="sc-matrix-legend-chip">❌ immune</span></div>
    <div>* = chaos guess (move not yet seen) · plain = revealed</div>
  </div>`;
  html += '<div class="sc-matrix-scroll"><table class="sc-matrix"><thead><tr><th></th>';
  for (const d of defenders) html += `<th title="${d}">${shortName(d)}</th>`;
  html += '</tr></thead><tbody>';
  for (const a of attackers) {
    html += `<tr><td class="sc-row-label" title="${a}">${shortName(a)}</td>`;
    for (const d of defenders) {
      const cell = cellByPair.get(`${a}::${d}`);
      html += renderCell(cell);
    }
    html += '</tr>';
  }
  html += '</tbody></table></div>';
  target.innerHTML = html;
}

function renderCell(c: MatrixCell | undefined): string {
  if (!c) return '<td>—</td>';
  if (c.immune) return '<td class="sc-immune">❌</td>';
  const conf = c.moveSource === 'modal' ? '*' : '';
  const cls = c.ohko ? 'sc-ohko' : c.twoHko ? 'sc-2hko' : c.dmgPctMax >= 50 ? 'sc-warn' : '';
  const label = `${c.dmgPctMin}-${c.dmgPctMax}%${conf}`;
  const tooltip = `${c.move}${c.moveSource === 'modal' ? ` (chaos ${c.modalPct}%)` : ' (revealed)'}`;
  return `<td class="${cls}" title="${tooltip}">${label}</td>`;
}

function unique<T>(arr: T[]): T[] { return Array.from(new Set(arr)); }
function shortName(s: string): string {
  if (s.length <= 8) return s;
  return s.slice(0, 7) + '…';
}
