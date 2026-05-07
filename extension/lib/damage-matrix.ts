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
  // Reset the once-per-batch warn flag so users see one warning per matrix
  // build if their snapshot stats stop matching the synthesized base stats.
  warnedSnapStatMismatch = false;
  for (const atk of opts.attackers) {
    for (const def of opts.defenders) {
      const moves = movesForMon(atk, opts.beliefByDefender?.[normalizeName(atk.species)]);
      for (const moveSpec of moves) {
        cells.push(computeCell(gen, atk, def, moveSpec, opts.field, opts.attackerSide));
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

// Heuristic: infer the defender's investment profile from the snapshot's
// Def vs SpD ratio + speed. The previous always-0 spread caused calc to use
// untrained defenders while we divide by the snapshot's invested maxhp,
// producing inflated damage % (off-by-up-to-50%). This four-bucket rule
// approximates conventional walls/offensive sets well enough for the
// matchup matrix.
function defenderEvs(snap: PokemonSnapshot): { hp: number; def: number; spd: number } {
  const d = snap.defense;
  const sd = snap.specialDefense;
  // Use a slightly looser ratio (1.25) than the spec's 1.1 to avoid
  // mistaking moderately-bulky offensive mons (e.g., a Lando-T at 247/287)
  // for dedicated walls. A ratio under 1.25 maps to mixed bulk.
  if (d >= sd * 1.25) return { hp: 252, def: 252, spd: 4 }; // physical wall
  if (sd >= d * 1.25) return { hp: 252, def: 4, spd: 252 }; // special wall
  if (snap.speed > 300) return { hp: 0, def: 0, spd: 0 }; // offensive mon
  return { hp: 252, def: 128, spd: 128 }; // mixed bulk
}

function natureFor(snap: PokemonSnapshot): string {
  const a = snap.attack;
  const s = snap.specialAttack;
  if (a >= s * 1.1) return 'Adamant';
  if (s >= a * 1.1) return 'Modest';
  return 'Hardy';
}

function defenderNature(snap: PokemonSnapshot): string {
  const d = snap.defense;
  const sd = snap.specialDefense;
  // Match the threshold used by defenderEvs() — see comment there.
  if (d >= sd * 1.25) return 'Impish'; // +Def, -SpA
  if (sd >= d * 1.25) return 'Calm';   // +SpD, -Atk
  if (snap.speed > 300) return 'Hardy';
  return 'Hardy';
}

// @smogon/calc stores ability/item as opaque strings and compares them by
// strict equality (e.g. `hasAbility('Levitate')`) — lowercase or
// concatenated forms silently no-op. Look up the canonical Title-Case name
// via the gen's data tables; fall back to the input if the lookup fails.
function canonicalAbility(
  gen: ReturnType<typeof Generations.get>,
  raw: string | undefined,
): string | undefined {
  if (!raw || raw === 'none') return undefined;
  const id = raw.toLowerCase().replace(/[^a-z0-9]/g, '');
  const found = gen.abilities.get(id as any);
  return (found?.name as unknown as string) ?? raw;
}

function canonicalItem(
  gen: ReturnType<typeof Generations.get>,
  raw: string | undefined,
): string | undefined {
  if (!raw || raw === 'none') return undefined;
  const id = raw.toLowerCase().replace(/[^a-z0-9]/g, '');
  const found = gen.items.get(id as any);
  return (found?.name as unknown as string) ?? raw;
}

// Module-level flag so we only warn once per buildDamageMatrix() call when
// the stat-back-solve fails verification. Reset at the top of every build.
let warnedSnapStatMismatch = false;

// For the "mine" side, the snapshot carries true Showdown-computed stats.
// We back-solve a synthetic baseStats override that, with EVs=0/IVs=31/Hardy,
// produces rawStats matching the snapshot within ±1 (integer rounding).
// This avoids the heuristic guessing wrong (e.g. constructing a 252 SpA Modest
// Heatran when the user actually runs a 252 HP / 252+ SpD Calm wall).
//
// Stat formula (gen 3+, neutral nature, IV=31, EV=0):
//   HP    = floor(((2B + 31) * L) / 100) + L + 10
//   Other = floor(((2B + 31) * L) / 100) + 5
// Inverting (and accepting ±1 rounding error):
//   B_hp = round(((stat - L - 10) * 100 / L - 31) / 2)
//   B_X  = round(((stat - 5) * 100 / L - 31) / 2)
function buildPokemonForCalc(
  gen: ReturnType<typeof Generations.get>,
  snap: PokemonSnapshot,
  role: 'attacker' | 'defender',
  isMine: boolean,
): Pokemon {
  const commonOpts: any = {
    level: snap.level,
    item: canonicalItem(gen, snap.item) as any,
    ability: canonicalAbility(gen, snap.ability) as any,
    teraType: snap.terastallized && snap.teraType ? (snap.teraType as any) : undefined,
  };
  if (role === 'defender') {
    commonOpts.curHP = snap.hp;
  }

  if (isMine) {
    const L = Math.max(1, snap.level);
    const back = {
      hp: Math.round(((snap.maxhp - L - 10) * 100 / L - 31) / 2),
      atk: Math.round(((snap.attack - 5) * 100 / L - 31) / 2),
      def: Math.round(((snap.defense - 5) * 100 / L - 31) / 2),
      spa: Math.round(((snap.specialAttack - 5) * 100 / L - 31) / 2),
      spd: Math.round(((snap.specialDefense - 5) * 100 / L - 31) / 2),
      spe: Math.round(((snap.speed - 5) * 100 / L - 31) / 2),
    };
    const synth = new Pokemon(gen, snap.species, {
      ...commonOpts,
      nature: 'Hardy' as any,
      evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
      ivs: { hp: 31, atk: 31, def: 31, spa: 31, spd: 31, spe: 31 },
      overrides: { baseStats: back } as any,
    });
    // Verify ±1; if mismatch, fall through to the heuristic path with a
    // single warning per build batch so we never throw.
    const ok =
      Math.abs(synth.rawStats.hp - snap.maxhp) <= 1 &&
      Math.abs(synth.rawStats.atk - snap.attack) <= 1 &&
      Math.abs(synth.rawStats.def - snap.defense) <= 1 &&
      Math.abs(synth.rawStats.spa - snap.specialAttack) <= 1 &&
      Math.abs(synth.rawStats.spd - snap.specialDefense) <= 1 &&
      Math.abs(synth.rawStats.spe - snap.speed) <= 1;
    if (ok) return synth;
    if (!warnedSnapStatMismatch) {
      // eslint-disable-next-line no-console
      console.warn(
        '[damage-matrix] back-solved baseStats off by >1 for own-side mon; falling back to heuristic',
        { species: snap.species, snap, synth: synth.rawStats },
      );
      warnedSnapStatMismatch = true;
    }
    // fall through to heuristic
  }

  // Heuristic path (opp side, or own-side fallback when verification fails).
  if (role === 'attacker') {
    const atkEvs = attackerEvs(snap);
    return new Pokemon(gen, snap.species, {
      ...commonOpts,
      nature: natureFor(snap) as any,
      evs: { hp: 0, atk: atkEvs.atk, def: 0, spa: atkEvs.spa, spd: 0, spe: 4 },
    });
  }
  const defEvs = defenderEvs(snap);
  return new Pokemon(gen, snap.species, {
    ...commonOpts,
    nature: defenderNature(snap) as any,
    evs: defEvs,
  });
}

function computeCell(
  gen: ReturnType<typeof Generations.get>,
  atk: PokemonSnapshot,
  def: PokemonSnapshot,
  moveSpec: { id: string; source: 'revealed' | 'modal'; pct?: number },
  field: { weather: string; terrain: string },
  attackerSide: 'mine' | 'opp',
): MatrixCell {
  try {
    const attacker = buildPokemonForCalc(gen, atk, 'attacker', attackerSide === 'mine');
    const defender = buildPokemonForCalc(gen, def, 'defender', attackerSide === 'opp');
    const move = new Move(gen, moveSpec.id);
    const fieldOpts: any = {};
    if (field.weather) fieldOpts.weather = field.weather;
    if (field.terrain) fieldOpts.terrain = field.terrain;
    const fieldObj = new Field(fieldOpts);
    const result = calculate(gen, attacker, defender, move, fieldObj);
    const range = result.range();
    const min = range[0] ?? 0;
    const max = range[1] ?? 0;
    // result.kochance() throws when damage is 0 (immunity / no-op). Treat
    // that as a clean "immune" cell rather than letting the exception flow
    // up and produce a default cell with immune:false.
    let koChance = 0;
    let koN = 0;
    if (max > 0) {
      try {
        const ko = result.kochance();
        koChance = ko.chance ?? 0;
        koN = ko.n ?? 0;
      } catch {
        // KO chance not computable for this scenario — leave at 0/0.
      }
    }
    // Use the calc's HP as the denominator so the % is internally consistent
    // with the calc's view of the defender (the snapshot HP can diverge when
    // the snapshot reflects different EV/IV/nature investment than our
    // heuristic chose).
    const denom = defender.rawStats.hp || def.maxhp || 1;
    return {
      attacker: atk.species,
      defender: def.species,
      move: moveSpec.id,
      moveSource: moveSpec.source,
      modalPct: moveSpec.pct,
      dmgPctMin: Math.round((min / denom) * 100),
      dmgPctMax: Math.round((max / denom) * 100),
      ohko: koChance >= 1.0 && koN === 1,
      twoHko: koChance >= 1.0 && koN === 2,
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
