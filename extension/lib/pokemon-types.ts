/**
 * Pokemon type chart, status mapping, and Showdown dex lookups. Extracted
 * from translate.ts during Phase 3 polish so the pure species/type-lookup
 * surface lives independent of the protocol translation logic.
 *
 * Note: `norm` is imported from translate.ts (circular import). This is safe
 * because no top-level evaluation here calls `norm` — only the functions
 * reference it lexically.
 */
import { norm } from './translate';

// ---- Gen 9 type chart ------------------------------------------------------
// Surfaces for leadScore / leadMatrix heuristics in consumer modules.
// Asymmetric: rows = attacking type, cols = defending type → multiplier.
export const TYPE_CHART: Record<string, Record<string, number>> = {
  Normal:   { Rock: 0.5, Ghost: 0, Steel: 0.5 },
  Fire:     { Fire: 0.5, Water: 0.5, Grass: 2, Ice: 2, Bug: 2, Rock: 0.5, Dragon: 0.5, Steel: 2 },
  Water:    { Fire: 2, Water: 0.5, Grass: 0.5, Ground: 2, Rock: 2, Dragon: 0.5 },
  Electric: { Water: 2, Electric: 0.5, Grass: 0.5, Ground: 0, Flying: 2, Dragon: 0.5 },
  Grass:    { Fire: 0.5, Water: 2, Grass: 0.5, Poison: 0.5, Ground: 2, Flying: 0.5, Bug: 0.5, Rock: 2, Dragon: 0.5, Steel: 0.5 },
  Ice:      { Fire: 0.5, Water: 0.5, Grass: 2, Ice: 0.5, Ground: 2, Flying: 2, Dragon: 2, Steel: 0.5 },
  Fighting: { Normal: 2, Ice: 2, Poison: 0.5, Flying: 0.5, Psychic: 0.5, Bug: 0.5, Rock: 2, Ghost: 0, Dark: 2, Steel: 2, Fairy: 0.5 },
  Poison:   { Grass: 2, Poison: 0.5, Ground: 0.5, Rock: 0.5, Ghost: 0.5, Steel: 0, Fairy: 2 },
  Ground:   { Fire: 2, Electric: 2, Grass: 0.5, Poison: 2, Flying: 0, Bug: 0.5, Rock: 2, Steel: 2 },
  Flying:   { Electric: 0.5, Grass: 2, Fighting: 2, Bug: 2, Rock: 0.5, Steel: 0.5 },
  Psychic:  { Fighting: 2, Poison: 2, Psychic: 0.5, Dark: 0, Steel: 0.5 },
  Bug:      { Fire: 0.5, Grass: 2, Fighting: 0.5, Poison: 0.5, Flying: 0.5, Psychic: 2, Ghost: 0.5, Dark: 2, Steel: 0.5, Fairy: 0.5 },
  Rock:     { Fire: 2, Ice: 2, Fighting: 0.5, Ground: 0.5, Flying: 2, Bug: 2, Steel: 0.5 },
  Ghost:    { Normal: 0, Psychic: 2, Ghost: 2, Dark: 0.5 },
  Dragon:   { Dragon: 2, Steel: 0.5, Fairy: 0 },
  Dark:     { Fighting: 0.5, Psychic: 2, Ghost: 2, Dark: 0.5, Fairy: 0.5 },
  Steel:    { Fire: 0.5, Water: 0.5, Electric: 0.5, Ice: 2, Rock: 2, Steel: 0.5, Fairy: 2 },
  Fairy:    { Fire: 0.5, Fighting: 2, Poison: 0.5, Dragon: 2, Dark: 2, Steel: 0.5 },
};

// Showdown status codes → engine-payload display strings.
export const STATUS: Record<string, string> = {
  brn: 'Burn', frz: 'Freeze', par: 'Paralyze',
  psn: 'Poison', slp: 'Sleep', tox: 'Toxic',
};

/**
 * Resolve a Showdown species name to its types array. Tries the page-global
 * Dex.species first (live, up-to-date), then falls back to BattlePokedex.
 * Returns [] when neither has the species — callers must handle empty types
 * (engine treats empty as Typeless, which broke immunity checks pre-fix).
 */
export function resolveTypes(speciesName: string, win: any): string[] {
  try {
    if (win.Dex && win.Dex.species) {
      const sp = win.Dex.species.get(speciesName);
      // .slice() so we own our array — Showdown's Dex returns a live
      // reference that can theoretically be mutated later, which would
      // make scHistory's stored payload diverge from what we POSTed.
      if (sp?.types?.length) return sp.types.slice();
    }
    if (win.BattlePokedex) {
      const entry = win.BattlePokedex[norm(speciesName)] || win.BattlePokedex[speciesName];
      if (entry?.types) return entry.types.slice();
    }
  } catch {}
  return [];
}

/**
 * Estimate an opponent's stats + ability from base stats + level. Used when
 * Showdown doesn't expose live stats (always, for opp Pokemon). Assumes max
 * EVs in the inferred-offensive stat + Speed; +5 buffer per stat for nature.
 * Falls back to defensible defaults if the dex lookup fails.
 */
export function computeOpponentStats(speciesName: string, level: number, win: any) {
  const fallback = {
    maxhp: 250,
    stats: { atk: 200, def: 150, spa: 200, spd: 150, spe: 180 },
    ability: 'none',
  };
  try {
    const sp = win.Dex?.species?.get(speciesName);
    if (!sp?.baseStats) return fallback;
    const bs = sp.baseStats;
    const isSpecial = (bs.spa || 0) > (bs.atk || 0);
    const atkEV = isSpecial ? 0 : 252;
    const spaEV = isSpecial ? 252 : 0;
    const spe252 = 252;
    const core = (base: number, ev: number) =>
      Math.floor((2 * base + 31 + Math.floor(ev / 4)) * level / 100);
    return {
      maxhp: core(bs.hp, 0) + level + 10,
      stats: {
        atk: core(bs.atk, atkEV) + 5,
        def: core(bs.def, 0) + 5,
        spa: core(bs.spa, spaEV) + 5,
        spd: core(bs.spd, 0) + 5,
        spe: core(bs.spe, spe252) + 5,
      },
      ability: sp.abilities?.[0] ? norm(sp.abilities[0]) : 'none',
    };
  } catch {
    return fallback;
  }
}
