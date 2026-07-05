import {
  actionMatches,
  normalizePlanToken,
  type MatchupPlan,
  type PlanFitResult,
} from './matchup-plan';

export interface PlanFitInput {
  plan: MatchupPlan | null;
  turn: number;
  action: string;
  alternatives?: { move?: string; name?: string; confidence?: number }[];
  myActive?: { species?: string; status?: string; hp?: number; maxhp?: number };
  oppActive?: { species?: string };
}

const SETUP_OR_SLOW_VALUE = new Set([
  'swordsdance',
  'quiverdance',
  'dragondance',
  'calmmind',
  'nastyplot',
  'stealthrock',
  'toxicspikes',
  'spikes',
  'recover',
  'roost',
  'protect',
]);

function hpPct(active?: { hp?: number; maxhp?: number }): number | null {
  if (!active || typeof active.hp !== 'number' || typeof active.maxhp !== 'number' || active.maxhp <= 0) {
    return null;
  }
  return Math.round((active.hp / active.maxhp) * 100);
}

function findAlternative(
  alternatives: PlanFitInput['alternatives'],
  prefer: string[],
  avoid: string[],
): string | null {
  const rows = alternatives ?? [];
  const preferred = rows.find((item) => actionMatches(String(item.move ?? item.name ?? ''), prefer));
  if (preferred) return String(preferred.move ?? preferred.name ?? '');
  const allowed = rows.find((item) => {
    const name = String(item.move ?? item.name ?? '');
    return name && !actionMatches(name, avoid);
  });
  return allowed ? String(allowed.move ?? allowed.name ?? '') : null;
}

function severityRating(severity: string | undefined): PlanFitResult['rating'] {
  return severity === 'high' ? 'violates_plan' : 'risky';
}

export function evaluatePlanFit(input: PlanFitInput): PlanFitResult {
  const { plan, action, myActive, oppActive } = input;
  if (!plan || !action) {
    return {
      rating: 'uncertain',
      reason: 'No matchup plan is available yet.',
    };
  }

  const actionNorm = normalizePlanToken(action);
  const mySpecies = normalizePlanToken(myActive?.species);
  const oppSpecies = normalizePlanToken(oppActive?.species);

  for (const rule of plan.leadRules ?? []) {
    if (!oppSpecies || normalizePlanToken(rule.ifOpponentLead) !== oppSpecies) continue;
    if (!actionMatches(action, rule.avoid ?? [])) continue;
    const alternative = findAlternative(input.alternatives, rule.prefer ?? [], rule.avoid ?? []);
    return {
      rating: 'risky',
      reason: rule.reason,
      preferredAlternative: alternative ? {
        action: alternative,
        reason: 'This follows the matchup lead rule better than the current recommendation.',
      } : undefined,
      strategicFallback: `Prefer ${rule.prefer.join(' or ') || 'an active line'} over ${rule.avoid.join(' or ') || 'a passive line'}.`,
    };
  }

  for (const rule of plan.dangerRules ?? []) {
    const trigger = rule.trigger ?? {};
    const triggerMine = normalizePlanToken(trigger.myActive);
    const triggerOpp = normalizePlanToken(trigger.oppActive);
    if (triggerMine && triggerMine !== mySpecies) continue;
    if (triggerOpp && triggerOpp !== oppSpecies) continue;
    return {
      rating: severityRating(rule.severity),
      reason: rule.rule,
      strategicFallback: 'Check whether this click advances the preview win path or preserves the key matchup piece.',
      matchedRuleId: rule.id,
    };
  }

  const preserve = (plan.preserveTargets ?? []).find(
    (target) => normalizePlanToken(target.pokemon) === mySpecies,
  );
  const status = normalizePlanToken(myActive?.status);
  const pct = hpPct(myActive);
  if (preserve && (status === 'psn' || status === 'tox' || status === 'brn' || (pct !== null && pct <= 35))) {
    const slowValue = SETUP_OR_SLOW_VALUE.has(actionNorm);
    if (slowValue) {
      return {
        rating: preserve.priority === 'high' ? 'violates_plan' : 'risky',
        reason: `${preserve.pokemon} is a preserve target, but this recommendation spends another slow turn while it is under pressure.`,
        strategicFallback: 'Prefer immediate progress or a preserving pivot unless this move wins immediately.',
      };
    }
    return {
      rating: 'risky',
      reason: `${preserve.pokemon} is a preserve target and is already under pressure.`,
      strategicFallback: 'Make sure this action is worth risking the matchup piece.',
    };
  }

  return {
    rating: 'good',
    reason: 'No matchup-plan conflict detected for the current recommendation.',
  };
}
