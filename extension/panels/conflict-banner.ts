/**
 * Render the red conflict banner shown above the main TCG card when
 * lib/conflict.ts:detectConflict() returns a non-null result.
 * Pure: data in, DOM element out.
 */
import { escapeHtml } from './_shared';

export type ConflictSeverity = 'STRONG' | 'POSSIBLE' | 'PIVOT';

export interface SafeSwitchEntry {
  species: string;
  /** Worst-case damage % this mon takes from opp's onField threats. */
  worstDmgPct: number;
}

export interface ConflictBannerProps {
  severity: ConflictSeverity;
  reason: string;
  /**
   * Ranked non-OHKO switch options (safest first). Rendered as a chip row
   * under the message so the user has an immediate "switch into this"
   * answer when the engine's pick conflicts with the matrix.
   */
  safeSwitches?: SafeSwitchEntry[];
}

const SEVERITY_LABEL: Record<ConflictSeverity, string> = {
  STRONG: '⚠ STRONG CONFLICT',
  POSSIBLE: '⚠ POSSIBLE CONFLICT',
  PIVOT: '⚠ PIVOT INTO DEATH',
};

export function renderConflictBanner(props: ConflictBannerProps | null): HTMLElement {
  const el = document.createElement('div');
  el.className = 'sc-conflict-banner';
  if (!props) {
    el.classList.add('hidden');
    return el;
  }
  const safeSwitchesHtml = props.safeSwitches?.length
    ? `<div class="safe-switches">
         <span class="safe-switches-label">→ Safer switch:</span>
         ${props.safeSwitches
           .map(s => `<span class="safe-switch-chip">${escapeHtml(s.species)} <em>${Math.round(s.worstDmgPct)}%</em></span>`)
           .join('')}
       </div>`
    : '';
  el.innerHTML = `
    <div class="icon">!</div>
    <div class="text">
      <strong>${SEVERITY_LABEL[props.severity]}</strong>
      ${escapeHtml(props.reason)}
      ${safeSwitchesHtml}
    </div>
  `;
  return el;
}
