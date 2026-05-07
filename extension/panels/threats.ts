// extension/panels/threats.ts
// Pure DOM-rendering for the threats card. Given a ThreatsReport (or null
// while waiting for data), writes a compact block of HTML into the target
// element. No fetching, no state — caller drives refresh.

import type { ThreatsReport, Threat } from '../lib/threats';

export function renderThreats(target: HTMLElement, report: ThreatsReport | null): void {
  if (!report) {
    target.innerHTML = '<div class="sc-empty">no opp data yet</div>';
    return;
  }
  const onField = report.onField.slice(0, 4).map(threatRow).join('');
  const incoming = report.incoming.slice(0, 5).map(threatRow).join('');
  target.innerHTML = `
    <div class="sc-threats-header">${report.speedNote}</div>
    <div class="sc-threats-section">ON-FIELD${onField || '<div class="sc-empty">  no threats above 50%</div>'}</div>
    ${incoming ? `<div class="sc-threats-section">INCOMING${incoming}</div>` : ''}
  `;
}

function threatRow(t: Threat): string {
  const conf = t.moveSource === 'modal'
    ? `<span class="sc-conf">${t.modalPct}%</span>`
    : `<span class="sc-conf">✓</span>`;
  const victims = t.victims
    .slice()
    .sort((a, b) => b.dmgPct - a.dmgPct)
    .slice(0, 3)
    .map(v => `${v.ohko ? '☠ ' : v.twoHko ? '⚠ ' : ''}${v.species} ${v.dmgPct}%`)
    .join(' · ');
  return `<div class="sc-threat-row">⚠ ${t.oppMove} ${conf} → ${victims}</div>`;
}
