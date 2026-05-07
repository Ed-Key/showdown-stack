// extension/lib/conflict.ts

import type { ThreatsReport } from './threats';
import type { PokemonSnapshot } from './types';

export type ConflictWarning = {
  level: 'strong' | 'warn' | 'pivot' | 'info';
  message: string;
};

export function detectConflict(opts: {
  engineRecommendation: { move: string; isSwitch: boolean; switchTarget?: string };
  threats: ThreatsReport;
  myActive: PokemonSnapshot;
  oppActive: PokemonSnapshot;
  myTeam: PokemonSnapshot[];
}): ConflictWarning | null {
  const { engineRecommendation: rec, threats, myActive, oppActive } = opts;

  // Find the worst threat from opp's active mon vs my active
  const onFieldVsMe = threats.onField
    .map(t => ({ t, victim: t.victims.find(v => v.species === myActive.species) }))
    .filter(x => x.victim)
    .sort((a, b) => (b.victim!.dmgPct - a.victim!.dmgPct));
  const worst = onFieldVsMe[0];

  // Rule 1: STRONG CONFLICT
  if (!rec.isSwitch && worst?.victim?.ohko && oppActive.speed > myActive.speed) {
    return {
      level: 'strong',
      message: `STRONG CONFLICT: ${oppActive.species} ${worst.t.oppMove} guaranteed OHKO ${myActive.species}. Engine may not see this.`,
    };
  }

  // Rule 2: POSSIBLE CONFLICT
  if (!rec.isSwitch && worst?.victim?.ohko && Math.abs(oppActive.speed - myActive.speed) < 30) {
    return {
      level: 'warn',
      message: `POSSIBLE CONFLICT: ${oppActive.species} ${worst.t.oppMove} OHKOs ${myActive.species}; speed tier unclear (Scarf?).`,
    };
  }

  // Rule 3: PIVOT-INTO-DEATH
  if (rec.isSwitch && rec.switchTarget) {
    const pivotVictim = threats.onField
      .flatMap(t => t.victims.filter(v => v.species === rec.switchTarget))
      .sort((a, b) => b.dmgPct - a.dmgPct)[0];
    if (pivotVictim?.ohko) {
      return {
        level: 'pivot',
        message: `PIVOT WARNING: switching to ${rec.switchTarget} — ${oppActive.species} OHKOs it too. Pick a different switch.`,
      };
    }
  }

  // Rule 4: DISAGREEMENT (informational)
  // (We'd need the matrix to find "highest damage" - defer to caller passing best matrix move)
  // Stage 4.1 keeps this stub; expand when needed.

  return null;
}
