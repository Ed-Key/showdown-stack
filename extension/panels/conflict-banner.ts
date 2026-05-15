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
  /** Candidate's hardest non-immune move vs opp's active mon. */
  bestMoveBack?: {
    move: string;
    dmgPctMax: number;
    ohko: boolean;
    twoHko: boolean;
  };
  /** True iff candidate strictly outspeeds opp.active. */
  fasterThanOpp?: boolean;
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

function renderSafeSwitchChip(s: SafeSwitchEntry): string {
  const speedBadge =
    s.fasterThanOpp === true
      ? `<span class="speed-badge faster" title="Outspeeds opp">⚡</span>`
      : s.fasterThanOpp === false
        ? `<span class="speed-badge slower" title="Slower than opp">↓</span>`
        : '';

  let outgoingHtml = '';
  if (s.bestMoveBack) {
    const killTag = s.bestMoveBack.ohko
      ? `<span class="kill-tag ohko">OHKO</span>`
      : s.bestMoveBack.twoHko
        ? `<span class="kill-tag two">2HKO</span>`
        : '';
    outgoingHtml = `<span class="outgoing-move">${escapeHtml(s.bestMoveBack.move)} <em class="outgoing-dmg">${Math.round(s.bestMoveBack.dmgPctMax)}%</em>${killTag}</span>`;
  }

  return `<span class="safe-switch-chip">`
    + `${escapeHtml(s.species)} <em class="incoming">${Math.round(s.worstDmgPct)}%</em>`
    + speedBadge
    + outgoingHtml
    + `</span>`;
}

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
         ${props.safeSwitches.map(renderSafeSwitchChip).join('')}
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
