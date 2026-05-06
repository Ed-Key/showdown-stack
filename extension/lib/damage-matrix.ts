// extension/lib/damage-matrix.ts

import { calculate, Generations, Pokemon, Move, Field } from '@smogon/calc';
import type { PokemonSnapshot } from './types';
import type { OpponentBeliefSnapshot } from './belief-snapshot';

export type MatrixCell = {
  attacker: string;
  defender: string;
  move: string;
  moveSource: 'revealed' | 'modal'; // confidence indicator
  modalPct?: number; // if 'modal'
  dmgPctMin: number; // % of defender HP
  dmgPctMax: number;
  ohko: boolean;
  twoHko: boolean;
  immune: boolean;
};

export type DamageMatrix = {
  cells: MatrixCell[]; // flat list; UI groups for display
  attackerSide: 'mine' | 'opp'; // which team's mons are attackers in this matrix
  computedAt: number; // ms timestamp
};

export function buildDamageMatrix(opts: {
  attackers: PokemonSnapshot[];
  defenders: PokemonSnapshot[];
  beliefByDefender?: Record<string, OpponentBeliefSnapshot>; // attacker's perspective: defenders' belief
  field: { weather: string; terrain: string };
  attackerSide: 'mine' | 'opp';
}): DamageMatrix {
  const gen = Generations.get(9);
  const cells: MatrixCell[] = [];
  for (const atk of opts.attackers) {
    for (const def of opts.defenders) {
      const moves = movesForMon(atk, opts.beliefByDefender?.[normalizeName(atk.species)]);
      for (const moveSpec of moves) {
        cells.push(computeCell(gen, atk, def, moveSpec, opts.field));
      }
    }
  }
  return { cells, attackerSide: opts.attackerSide, computedAt: Date.now() };
}

function movesForMon(
  atk: PokemonSnapshot,
  belief?: OpponentBeliefSnapshot,
): Array<{ id: string; source: 'revealed' | 'modal'; pct?: number }> {
  // For my team, atk.moves is exact. For opp, mix revealed + modal top.
  if (!belief) {
    return atk.moves
      .filter(m => m.id && m.id !== 'none')
      .map(m => ({ id: m.id, source: 'revealed' }));
  }
  const revealed = belief.revealed.moves.map(m => ({ id: m, source: 'revealed' as const }));
  const revealedSet = new Set(belief.revealed.moves);
  const modal = belief.modal.moves
    .filter(m => !revealedSet.has(m.name))
    .slice(0, 4 - revealed.length)
    .map(m => ({ id: m.name, source: 'modal' as const, pct: m.pct }));
  return [...revealed, ...modal];
}

// Heuristic: assume the attacker invests 252 EVs in its primary attacking stat
// (Atk if physical-leaning, SpA if special-leaning, both if mixed-stat species),
// with an Adamant/Modest-style nature bonus implicit via 252 EVs. The defender
// gets neutral spread. This matches conventional competitive assumptions for
// Smogon damage calcs when raw stats aren't carried through to @smogon/calc.
function attackerEvs(snap: PokemonSnapshot): { atk: number; spa: number } {
  // Simple rule: max the larger of attack/specialAttack. If close, max both.
  const a = snap.attack;
  const s = snap.specialAttack;
  if (a >= s * 1.1) return { atk: 252, spa: 0 };
  if (s >= a * 1.1) return { atk: 0, spa: 252 };
  return { atk: 252, spa: 252 };
}

function defenderEvs(): { hp: number; def: number; spd: number } {
  return { hp: 0, def: 0, spd: 0 };
}

function natureFor(snap: PokemonSnapshot): string {
  const a = snap.attack;
  const s = snap.specialAttack;
  if (a >= s * 1.1) return 'Adamant';
  if (s >= a * 1.1) return 'Modest';
  return 'Hardy';
}

function computeCell(
  gen: ReturnType<typeof Generations.get>,
  atk: PokemonSnapshot,
  def: PokemonSnapshot,
  moveSpec: { id: string; source: 'revealed' | 'modal'; pct?: number },
  field: { weather: string; terrain: string },
): MatrixCell {
  try {
    const atkEvs = attackerEvs(atk);
    const attacker = new Pokemon(gen, atk.species, {
      level: atk.level,
      item: atk.item === 'none' ? undefined : atk.item,
      ability: atk.ability === 'none' ? undefined : atk.ability,
      teraType: atk.terastallized && atk.teraType ? (atk.teraType as any) : undefined,
      nature: natureFor(atk),
      evs: { hp: 0, atk: atkEvs.atk, def: 0, spa: atkEvs.spa, spd: 0, spe: 4 },
    });
    const defender = new Pokemon(gen, def.species, {
      level: def.level,
      item: def.item === 'none' ? undefined : def.item,
      ability: def.ability === 'none' ? undefined : def.ability,
      teraType: def.terastallized && def.teraType ? (def.teraType as any) : undefined,
      nature: 'Hardy',
      evs: defenderEvs(),
      curHP: def.hp,
    });
    const move = new Move(gen, moveSpec.id);
    const fieldOpts: any = {};
    if (field.weather) fieldOpts.weather = field.weather;
    if (field.terrain) fieldOpts.terrain = field.terrain;
    const fieldObj = new Field(fieldOpts);
    const result = calculate(gen, attacker, defender, move, fieldObj);
    const range = result.range();
    const ko = result.kochance();
    const min = range[0] ?? 0;
    const max = range[1] ?? 0;
    const koChance = ko.chance ?? 0;
    return {
      attacker: atk.species,
      defender: def.species,
      move: moveSpec.id,
      moveSource: moveSpec.source,
      modalPct: moveSpec.pct,
      dmgPctMin: Math.round((min / def.maxhp) * 100),
      dmgPctMax: Math.round((max / def.maxhp) * 100),
      ohko: koChance >= 1.0 && ko.n === 1,
      twoHko: koChance >= 1.0 && ko.n === 2,
      immune: max === 0,
    };
  } catch (err) {
    return {
      attacker: atk.species,
      defender: def.species,
      move: moveSpec.id,
      moveSource: moveSpec.source,
      modalPct: moveSpec.pct,
      dmgPctMin: 0,
      dmgPctMax: 0,
      ohko: false,
      twoHko: false,
      immune: false,
    };
  }
}

function normalizeName(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}
