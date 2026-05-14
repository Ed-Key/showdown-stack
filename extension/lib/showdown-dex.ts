/**
 * Thin wrappers around Showdown's in-page BattleMovedex / BattlePokedex
 * globals. Used to map move names → element type and species → primary type
 * for TCG card theming.
 *
 * Falls back to 'Normal' when the dex is unavailable (test env, or running
 * before Showdown finishes booting) — the TCG type map sends that to the
 * neutral colorless palette, so the card is never broken visually.
 */

function normalizeId(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}

interface MovedexEntry {
  type?: string;
}

interface PokedexEntry {
  types?: string[];
}

interface ShowdownGlobals {
  BattleMovedex?: Record<string, MovedexEntry>;
  BattlePokedex?: Record<string, PokedexEntry>;
}

function getGlobals(): ShowdownGlobals {
  if (typeof window === 'undefined') return {};
  return window as unknown as ShowdownGlobals;
}

export function getMoveType(moveName: string): string {
  if (!moveName) return 'Normal';
  const { BattleMovedex } = getGlobals();
  if (!BattleMovedex) return 'Normal';
  const id = normalizeId(moveName);
  const entry = BattleMovedex[id];
  return entry?.type ?? 'Normal';
}

/**
 * Returns true if `name` looks like a switch-to-this-Pokemon recommendation
 * rather than a move. The engine surfaces both kinds in the same field, and
 * we distinguish them by dex membership: if it's a known species, it's a
 * switch; if it's a known move, it's a move. Unknown names default to false
 * (treated as a move) so we don't accidentally promote a typo'd move name
 * into the switch UI.
 */
export function isSwitchOption(name: string): boolean {
  if (!name) return false;
  const { BattlePokedex, BattleMovedex } = getGlobals();
  if (!BattlePokedex) return false;
  const id = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (BattleMovedex && BattleMovedex[id]) return false;
  return !!BattlePokedex[id];
}

export function getPokemonPrimaryType(species: string): string {
  if (!species) return 'Normal';
  const { BattlePokedex } = getGlobals();
  if (!BattlePokedex) return 'Normal';
  // Showdown stores species under both the formed ID and the base ID;
  // try a sequence of fallbacks (e.g. "zamazentacrowned" → "zamazenta").
  const id = normalizeId(species);
  const tryKeys = [id, id.replace(/(mega|gmax|crowned|hero|bloodmoon|origin|therian)$/, '')];
  for (const k of tryKeys) {
    const entry = BattlePokedex[k];
    if (entry?.types && entry.types.length > 0) return entry.types[0];
  }
  return 'Normal';
}
