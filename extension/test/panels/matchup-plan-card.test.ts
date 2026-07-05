/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { renderMatchupPlanCard } from '../../panels/matchup-plan-card';
import type { MatchupPlan, PreviewPlanResponse } from '../../lib/matchup-plan';

const plan: MatchupPlan = {
  archetype: 'rain offense', confidence: 'high',
  summary: 'Rain setter plus Water sweepers.', winPath: 'Preserve the Water answer.',
  recommendedLead: { pokemon: 'Garchomp', rating: 'safe', reason: 'Info lead.' },
  backupLeads: [], avoidLeads: [], leadRules: [],
  preserveTargets: [], mainThreats: [], dangerRules: [],
  earlyPriorities: [], uncertainties: [],
};

function response(overrides: Partial<PreviewPlanResponse>): PreviewPlanResponse {
  return {
    battleId: 'b', format: 'gen9nationaldex', provider: 'anthropic', mode: 'auto',
    source: 'model', model: 'claude-sonnet-4-6', latencyMs: 10, plan,
    ...overrides,
  } as PreviewPlanResponse;
}

describe('renderMatchupPlanCard source labeling', () => {
  it('shows a model chip for model plans', () => {
    const el = renderMatchupPlanCard(response({ source: 'model' }));
    const chip = el.querySelector('.sc-plan-chip.model');
    expect(chip?.textContent).toContain('claude-sonnet-4-6');
    expect(el.querySelector('.sc-plan-chip.heuristic')).toBeNull();
  });

  it('shows an amber heuristic chip with the reason for fallbacks', () => {
    const el = renderMatchupPlanCard(response({
      source: 'fallback', model: null,
      fallbackReason: 'model preview failed: timeout',
    }));
    const chip = el.querySelector<HTMLElement>('.sc-plan-chip.heuristic');
    expect(chip?.textContent).toContain('heuristic');
    expect(chip?.title).toContain('timeout');
  });
});
