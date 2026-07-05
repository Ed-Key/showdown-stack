import type { PlanFitResult } from '../lib/matchup-plan';
import { escapeHtml } from './_shared';

const LABELS: Record<PlanFitResult['rating'], string> = {
  good: 'PLAN FIT',
  risky: 'PLAN RISK',
  violates_plan: 'VIOLATES PLAN',
  uncertain: 'PLAN UNKNOWN',
};

export function renderPlanFitBanner(result: PlanFitResult): HTMLElement {
  const el = document.createElement('div');
  el.className = `sc-plan-fit-banner ${result.rating}`;
  const alt = result.preferredAlternative
    ? `<div class="sc-plan-fit-alt"><strong>Alternative</strong> ${escapeHtml(result.preferredAlternative.action)} - ${escapeHtml(result.preferredAlternative.reason)}</div>`
    : '';
  const fallback = result.strategicFallback
    ? `<div class="sc-plan-fit-alt"><strong>Lens</strong> ${escapeHtml(result.strategicFallback)}</div>`
    : '';
  el.innerHTML = `
    <div class="sc-plan-fit-label">${LABELS[result.rating]}</div>
    <div class="sc-plan-fit-copy">${escapeHtml(result.reason)}</div>
    ${alt || fallback}
  `;
  return el;
}
