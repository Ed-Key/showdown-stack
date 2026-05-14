/**
 * Render the red conflict banner shown above the main TCG card when
 * lib/conflict.ts:detectConflict() returns a non-null result.
 * Pure: data in, DOM element out.
 */
import { escapeHtml } from './_shared';

export type ConflictSeverity = 'STRONG' | 'POSSIBLE' | 'PIVOT';

export interface ConflictBannerProps {
  severity: ConflictSeverity;
  reason: string;
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
  el.innerHTML = `
    <div class="icon">!</div>
    <div class="text">
      <strong>${SEVERITY_LABEL[props.severity]}</strong>
      ${escapeHtml(props.reason)}
    </div>
  `;
  return el;
}
