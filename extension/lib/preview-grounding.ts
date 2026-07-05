// Compacts the team-preview damage matrices into a prompt-sized grounding
// pack for the preview planner: notable cells (OHKOs + hits >= 50%) and
// per-mon survives/threatens counts. Spec §3 caps this at 24 cells so the
// pack stays ~small in the prompt.

import type { DamageMatrix, MatrixCell } from './damage-matrix';

export interface GroundingCell {
  attacker: string;
  defender: string;
  move: string;
  pct: string; // "68-81"
  ohko: boolean;
  direction: 'mine' | 'opp';
}

export interface MonSummary {
  species: string;
  survives: number;
  threatens: number;
}

export interface PreviewGrounding {
  damageCells: GroundingCell[];
  monSummaries: MonSummary[];
  source: string;
}

export const MAX_GROUNDING_CELLS = 24;
const MIN_NOTABLE_PCT = 50;

function bestCellPerPair(cells: MatrixCell[]): MatrixCell[] {
  const best = new Map<string, MatrixCell>();
  for (const cell of cells) {
    if (cell.immune) continue;
    const key = `${cell.attacker}|${cell.defender}`;
    const cur = best.get(key);
    if (!cur || cell.dmgPctMax > cur.dmgPctMax) best.set(key, cell);
  }
  return [...best.values()];
}

function toGroundingCell(cell: MatrixCell, direction: 'mine' | 'opp'): GroundingCell {
  return {
    attacker: cell.attacker,
    defender: cell.defender,
    move: cell.move,
    pct: `${Math.round(cell.dmgPctMin)}-${Math.round(cell.dmgPctMax)}`,
    ohko: cell.ohko,
    direction,
  };
}

function buildMonSummaries(myAtk: DamageMatrix | null, oppAtk: DamageMatrix | null): MonSummary[] {
  const myBest = bestCellPerPair(myAtk?.cells ?? []);
  const oppBest = bestCellPerPair(oppAtk?.cells ?? []);
  const mySpecies = [...new Set(myBest.map((c) => c.attacker))];
  const oppSpecies = new Set(oppBest.map((c) => c.attacker));
  if (!mySpecies.length && oppSpecies.size === 0) return [];
  return mySpecies.map((species) => {
    const threatens = new Set(myBest.filter((c) => c.attacker === species && c.ohko).map((c) => c.defender)).size;
    const koMe = new Set(oppBest.filter((c) => c.defender === species && c.ohko).map((c) => c.attacker)).size;
    return { species, threatens, survives: Math.max(0, oppSpecies.size - koMe) };
  });
}

export function buildPreviewGrounding(
  myAtk: DamageMatrix | null,
  oppAtk: DamageMatrix | null,
): PreviewGrounding | null {
  if (!myAtk && !oppAtk) return null;
  const notable = [
    ...bestCellPerPair(myAtk?.cells ?? []).map((c) => ({ cell: c, direction: 'mine' as const })),
    ...bestCellPerPair(oppAtk?.cells ?? []).map((c) => ({ cell: c, direction: 'opp' as const })),
  ];
  const ohkos = notable.filter((n) => n.cell.ohko);
  const strong = notable
    .filter((n) => !n.cell.ohko && n.cell.dmgPctMax >= MIN_NOTABLE_PCT)
    .sort((a, b) => b.cell.dmgPctMax - a.cell.dmgPctMax);
  const damageCells = [...ohkos, ...strong]
    .slice(0, MAX_GROUNDING_CELLS)
    .map((n) => toGroundingCell(n.cell, n.direction));
  return {
    damageCells,
    monSummaries: buildMonSummaries(myAtk, oppAtk),
    source: 'extension-damage-matrix',
  };
}
