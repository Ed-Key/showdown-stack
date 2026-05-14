import { speciesToSpriteURL } from '../lib/tcg/species-url';
import { escapeHtml } from './_shared';

export interface ThreatRow {
  move: string;
  source: string;
  target: string;
  dmgPct: number;
  isOhko: boolean;
  source_seen: boolean;
}

export interface ThreatsPanelProps {
  onField: ThreatRow[];
  incoming: ThreatRow[];
}

function rowHtml(t: ThreatRow): string {
  const icon = t.isOhko
    ? `<span class="threat-icon ohko">☠</span>`
    : `<span class="threat-icon warn">⚠</span>`;
  const seenTag = t.source_seen
    ? `<span class="seen-tag seen">SEEN</span>`
    : `<span class="seen-tag chaos">CHAOS</span>`;
  const dmgClass = t.isOhko ? 'ohko' : '';
  return `
    <div class="threat-row">
      ${icon}
      <span class="threat-move">${escapeHtml(t.move)}</span>
      ${seenTag}
      <span class="threat-from">
        <img alt="" src="${speciesToSpriteURL(t.source)}"/>
        ${escapeHtml(t.target.slice(0, 8))}
      </span>
      <span class="threat-dmg ${dmgClass}">${t.dmgPct}%</span>
    </div>
  `;
}

export function renderThreatsPanel(p: ThreatsPanelProps): HTMLElement {
  const el = document.createElement('div');
  el.className = 'sc-trainer-card';
  el.innerHTML = `
    <div class="trainer-inner">
      <div class="trainer-header">
        <span class="t-name">⚠ Threats Report</span>
        <span class="t-type">TRAINER · STADIUM</span>
      </div>
      <div class="trainer-body">
        <div class="section-label">ON-FIELD</div>
        ${p.onField.map(rowHtml).join('')}
        <div class="section-label" style="margin-top:8px">INCOMING</div>
        ${p.incoming.map(rowHtml).join('')}
      </div>
    </div>
  `;
  return el;
}
