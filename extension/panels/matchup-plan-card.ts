import type { MatchupPlan, PreviewPlanResponse } from '../lib/matchup-plan';
import { escapeHtml } from './_shared';

function chips(items: string[], empty: string): string {
  if (!items.length) return `<span class="sc-plan-chip muted">${escapeHtml(empty)}</span>`;
  return items.slice(0, 4).map((item) => `<span class="sc-plan-chip">${escapeHtml(item)}</span>`).join('');
}

export function renderMatchupPlanCard(response: PreviewPlanResponse): HTMLElement {
  const plan = response.plan;
  const el = document.createElement('div');
  el.className = 'sc-matchup-plan-card';
  const preserve = (plan.preserveTargets ?? []).map((item) => item.pokemon);
  const threats = (plan.mainThreats ?? []).map((item) => item.pokemon);
  const rules = (plan.dangerRules ?? []).slice(0, 3);
  const leadRules = (plan.leadRules ?? []).slice(0, 2);
  el.innerHTML = `
    <div class="sc-plan-topline">
      <div>
        <div class="sc-plan-kicker">Matchup Plan</div>
        <div class="sc-plan-title">${escapeHtml(plan.archetype)}</div>
      </div>
      <div class="sc-plan-source">${escapeHtml(response.source)} · ${escapeHtml(plan.confidence)}</div>
    </div>
    <div class="sc-plan-summary">${escapeHtml(plan.summary)}</div>
    <div class="sc-plan-win"><strong>Win path</strong> ${escapeHtml(plan.winPath)}</div>
    <div class="sc-plan-lead">
      <strong>Lead</strong> ${escapeHtml(plan.recommendedLead?.pokemon || 'unknown')}
      <span>${escapeHtml(plan.recommendedLead?.reason || '')}</span>
    </div>
    ${leadRules.length ? `
      <div class="sc-plan-rules">
        ${leadRules.map((rule) => `
          <div class="sc-plan-rule">
            <strong>If ${escapeHtml(rule.ifOpponentLead)} leads</strong>
            <span>Prefer ${escapeHtml((rule.prefer || []).join(' / ') || 'active play')}; avoid ${escapeHtml((rule.avoid || []).join(' / ') || 'passive play')}.</span>
          </div>
        `).join('')}
      </div>
    ` : ''}
    <div class="sc-plan-grid">
      <div><strong>Preserve</strong><div>${chips(preserve, 'none flagged')}</div></div>
      <div><strong>Threats</strong><div>${chips(threats, 'scout first')}</div></div>
    </div>
    ${rules.length ? `
      <div class="sc-plan-danger">
        ${rules.map((rule) => `<div><strong>${escapeHtml(rule.severity.toUpperCase())}</strong> ${escapeHtml(rule.rule)}</div>`).join('')}
      </div>
    ` : ''}
  `;
  return el;
}

export function renderMatchupPlanLoading(message = 'Building preview plan...'): HTMLElement {
  const el = document.createElement('div');
  el.className = 'sc-matchup-plan-card loading';
  el.innerHTML = `
    <div class="sc-plan-kicker">Matchup Plan</div>
    <div class="sc-plan-summary">${escapeHtml(message)}</div>
  `;
  return el;
}

export function planOneLine(plan: MatchupPlan | null): string {
  if (!plan) return 'No matchup plan yet.';
  const preserve = (plan.preserveTargets ?? []).map((item) => item.pokemon).slice(0, 2).join(', ');
  return `${plan.archetype}: ${plan.winPath}${preserve ? ` Preserve ${preserve}.` : ''}`;
}
