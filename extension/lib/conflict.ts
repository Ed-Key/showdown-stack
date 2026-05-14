// extension/lib/conflict.ts

import type { ThreatsReport } from './threats';
import type { PokemonSnapshot } from './types';

export type SafeSwitch = {
  /** Species name from myTeam (display form, not normalized). */
  species: string;
  /** Worst (highest) damage% this mon takes from any opp-active onField move. */
  worstDmgPct: number;
};

export type ConflictWarning = {
  level: 'strong' | 'warn' | 'pivot' | 'info';
  message: string;
  /**
   * Non-OHKO switch targets ranked safest-first. Empty when every benchmon
   * is also OHKO'd or there's no usable threat data. Populated for strong /
   * warn / pivot conflicts so the user has an immediate "switch to this"
   * answer instead of having to manually scan the threats panel.
   */
  safeSwitches?: SafeSwitch[];
};

/**
 * Rank non-active, non-fainted bench mons by worst-case damage from the
 * opp's currently-active threats. Excludes any mon taking ≥100% (OHKO).
 * Returns up to `topN` survivors, lowest damage first.
 */
export function computeSafeSwitches(
  threats: ThreatsReport,
  myActive: PokemonSnapshot,
  myTeam: PokemonSnapshot[],
  topN: number = 3,
): SafeSwitch[] {
  const candidates = myTeam.filter(
    p => p.species !== myActive.species && (p.hp ?? 1) > 0,
  );
  const ranked: SafeSwitch[] = candidates.map(p => {
    let worst = 0;
    for (const t of threats.onField) {
      const v = t.victims.find(v => v.species === p.species);
      if (v && v.dmgPct > worst) worst = v.dmgPct;
    }
    return { species: p.species, worstDmgPct: worst };
  });
  return ranked
    .filter(s => s.worstDmgPct < 100)
    .sort((a, b) => a.worstDmgPct - b.worstDmgPct)
    .slice(0, topN);
}

export function detectConflict(opts: {
  engineRecommendation: { move: string; isSwitch: boolean; switchTarget?: string };
  threats: ThreatsReport;
  myActive: PokemonSnapshot;
  oppActive: PokemonSnapshot;
  myTeam: PokemonSnapshot[];
}): ConflictWarning | null {
  const { engineRecommendation: rec, threats, myActive, oppActive, myTeam } = opts;

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
      safeSwitches: computeSafeSwitches(threats, myActive, myTeam),
    };
  }

  // Rule 2: POSSIBLE CONFLICT
  if (!rec.isSwitch && worst?.victim?.ohko && Math.abs(oppActive.speed - myActive.speed) < 30) {
    return {
      level: 'warn',
      message: `POSSIBLE CONFLICT: ${oppActive.species} ${worst.t.oppMove} OHKOs ${myActive.species}; speed tier unclear (Scarf?).`,
      safeSwitches: computeSafeSwitches(threats, myActive, myTeam),
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
        safeSwitches: computeSafeSwitches(threats, myActive, myTeam),
      };
    }
  }

  // Rule 4: DISAGREEMENT (informational)
  // (We'd need the matrix to find "highest damage" - defer to caller passing best matrix move)
  // Stage 4.1 keeps this stub; expand when needed.

  return null;
}
