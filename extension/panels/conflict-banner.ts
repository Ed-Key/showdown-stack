/**
 * Render the red conflict banner shown above the main TCG card when
 * lib/conflict.ts:detectConflict() returns a non-null result.
 * Pure: data in, DOM element out.
 */

export type ConflictSeverity = 'STRONG' | 'POSSIBLE' | 'PIVOT';

export interface ConflictBannerProps {
  severity: ConflictSeverity;
  reason: string;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]!));
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
