import { describe, expect, it } from 'vitest';
import { evaluatePlanFit } from '../../lib/plan-fit';
import type { MatchupPlan } from '../../lib/matchup-plan';

const basePlan: MatchupPlan = {
  archetype: 'bulky stall/control',
  confidence: 'high',
  summary: 'Opponent preview shows disruption and recovery loops.',
  winPath: 'Create progress before passive control stabilizes.',
  recommendedLead: {
    pokemon: 'Garchomp',
    rating: 'safe',
    reason: 'Default information lead.',
  },
  backupLeads: [],
  avoidLeads: [],
  leadRules: [{
    ifOpponentLead: 'Gliscor',
    prefer: ['Earthquake', 'Dragon Tail'],
    avoid: ['Stealth Rock'],
    reason: 'Gliscor can deny passive openings with Taunt.',
  }],
  preserveTargets: [{
    pokemon: 'Ogerpon-Wellspring',
    reason: 'Primary progress maker into bulky water support.',
    priority: 'high',
  }],
  mainThreats: [],
  dangerRules: [{
    id: 'ogerpon_alomomola_status_loop',
    severity: 'high',
    rule: 'Do not let Ogerpon-Wellspring spend slow setup turns in an Alomomola status loop.',
    trigger: { myActive: 'Ogerpon-Wellspring', oppActive: 'Alomomola' },
  }],
  earlyPriorities: [],
  uncertainties: [],
};

describe('evaluatePlanFit', () => {
  it('flags passive lead recommendations into Gliscor and suggests a preferred move', () => {
    const result = evaluatePlanFit({
      plan: basePlan,
      turn: 1,
      action: 'Stealth Rock',
      alternatives: [
        { move: 'Earthquake', confidence: 0.45 },
        { move: 'Dragon Tail', confidence: 0.32 },
      ],
      myActive: { species: 'Garchomp', hp: 100, maxhp: 100 },
      oppActive: { species: 'Gliscor' },
    });

    expect(result.rating).toBe('risky');
    expect(result.reason).toContain('Taunt');
    expect(result.preferredAlternative?.action).toBe('Earthquake');
  });

  it('flags danger-rule matchups as plan violations', () => {
    const result = evaluatePlanFit({
      plan: basePlan,
      turn: 8,
      action: 'Swords Dance',
      alternatives: [{ move: 'Ivy Cudgel', confidence: 0.51 }],
      myActive: { species: 'Ogerpon-Wellspring', hp: 80, maxhp: 100 },
      oppActive: { species: 'Alomomola' },
    });

    expect(result.rating).toBe('violates_plan');
    expect(result.matchedRuleId).toBe('ogerpon_alomomola_status_loop');
  });

  it('returns good when no preview rule conflicts with the recommendation', () => {
    const result = evaluatePlanFit({
      plan: basePlan,
      turn: 3,
      action: 'Earthquake',
      alternatives: [],
      myActive: { species: 'Garchomp', hp: 100, maxhp: 100 },
      oppActive: { species: 'Heatran' },
    });

    expect(result.rating).toBe('good');
  });
});
