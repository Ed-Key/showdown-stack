// extension/lib/threats.ts

import type { DamageMatrix, MatrixCell } from './damage-matrix';
import type { PokemonSnapshot } from './types';

export type Threat = {
  oppSpecies: string;
  oppMove: string;
  moveSource: 'revealed' | 'modal';
  modalPct?: number;
  victims: { species: string; dmgPct: number; ohko: boolean; twoHko: boolean }[];
  speedAdvantage: 'opp_faster' | 'me_faster' | 'tied' | 'unknown';
};

export type ThreatsReport = {
  onField: Threat[];          // threats from the opp's currently active mon
  incoming: Threat[];         // threats from opp's bench (modal pivots)
  speedNote: string;          // e.g. "Garchomp faster than 4/6 of yours"
};

export function computeThreats(opts: {
  matrix: DamageMatrix;       // opp-attacks-mine matrix
  myActive: PokemonSnapshot;
  oppActive: PokemonSnapshot;
  myTeam: PokemonSnapshot[];
  oppTeam: PokemonSnapshot[];
  threshold: { warn: number; danger: number };  // % HP, e.g. {warn:50, danger:80}
}): ThreatsReport {
  const cellsByOpp = groupBy(opts.matrix.cells, c => c.attacker);
  const onFieldCells = cellsByOpp.get(opts.oppActive.species) ?? [];

  const onField = topThreats(onFieldCells, opts.threshold);
  const incomingCells = opts.oppTeam
    .filter(p => p.species !== opts.oppActive.species)
    .flatMap(p => cellsByOpp.get(p.species) ?? []);
  const incoming = topThreats(incomingCells, opts.threshold).slice(0, 5);

  const oppFasterCount = opts.myTeam.filter(m => opts.oppActive.speed > m.speed).length;
  const speedNote = `${opts.oppActive.species} faster than ${oppFasterCount}/${opts.myTeam.length} of yours`;

  return { onField, incoming, speedNote };
}

function topThreats(cells: MatrixCell[], threshold: { warn: number; danger: number }): Threat[] {
  // Group by (oppSpecies, oppMove)
  const byMove = new Map<string, MatrixCell[]>();
  for (const c of cells) {
    if (c.dmgPctMax < threshold.warn) continue;
    const key = `${c.attacker}::${c.move}`;
    if (!byMove.has(key)) byMove.set(key, []);
    byMove.get(key)!.push(c);
  }
  const out: Threat[] = [];
  for (const [key, group] of byMove) {
    const [oppSpecies, oppMove] = key.split('::');
    const sample = group[0];
    out.push({
      oppSpecies, oppMove,
      moveSource: sample.moveSource,
      modalPct: sample.modalPct,
      victims: group.map(c => ({
        species: c.defender, dmgPct: c.dmgPctMax,
        ohko: c.ohko, twoHko: c.twoHko,
      })),
      speedAdvantage: 'unknown',  // computed by caller for context
    });
  }
  // Sort: any OHKO first, then by max damage desc
  out.sort((a, b) => {
    const aMax = Math.max(...a.victims.map(v => v.dmgPct));
    const bMax = Math.max(...b.victims.map(v => v.dmgPct));
    return bMax - aMax;
  });
  return out;
}

function groupBy<T, K>(arr: T[], key: (t: T) => K): Map<K, T[]> {
  const m = new Map<K, T[]>();
  for (const x of arr) {
    const k = key(x);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(x);
  }
  return m;
}
