// Pure translation logic: Showdown battle/request objects → poke-engine payload.
// Extracted from extension/entrypoints/content.ts so the logic is testable
// outside the page (no MAIN-world `window` closure required). Functions that
// access page globals (`win.Dex`, `win.BattlePokedex`) accept `win: any` as
// an explicit parameter; everything else is fully pure.
import { priorMovesForSpecies } from '../utils/chaos-priors';

// ---- Gen 9 type chart ------------------------------------------------------
// (used by content.ts's leadScore/leadMatrix heuristics — re-exported here
// because content.ts's autonomous-bot heuristics still consume it. Task 0.6
// will delete those heuristics; once that lands this constant becomes
// internal to translate.ts, since translate proper does not use it.)
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

export const DEFAULT_SC = {
  auroraVeil: 0, craftyShield: 0, healingWish: 0, lightScreen: 0,
  luckyChant: 0, lunarDance: 0, matBlock: 0, mist: 0,
  protect: 0, quickGuard: 0, reflect: 0, safeguard: 0,
  spikes: 0, stealthRock: 0, stickyWeb: 0, tailwind: 0,
  toxicCount: 0, toxicSpikes: 0, wideGuard: 0,
};

export const STATUS: Record<string, string> = {
  brn: 'Burn', frz: 'Freeze', par: 'Paralyze',
  psn: 'Poison', slp: 'Sleep', tox: 'Toxic',
};

// ---- helpers ---------------------------------------------------------
export const norm = (s: any) =>
  (s || '').toString().toLowerCase().replace(/[^a-z0-9]/g, '');

export const padMoves = (arr: any[]) => {
  const m = arr.slice(0, 4);
  while (m.length < 4) m.push({ id: 'none', pp: 0 });
  return m;
};

export function padMovesWithPriors(revealed: any[], speciesDisplay: string) {
  const existingIds = new Set(revealed.map((m: any) => m.id));
  const priors = priorMovesForSpecies(speciesDisplay);
  const merged: any[] = revealed.slice(0, 4);
  for (const pm of priors) {
    if (merged.length >= 4) break;
    if (!existingIds.has(pm)) {
      merged.push({ id: pm, pp: 8 });
      existingIds.add(pm);
    }
  }
  while (merged.length < 4) merged.push({ id: 'none', pp: 0 });
  return merged;
}

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

// ---- translation: Showdown battle → poke-engine payload --------------
export function buildMyPokemon(p: any, activeMoves: any[] | null = null, win: any) {
  const speciesRaw = p.speciesForme || p.species;
  return {
    species: norm(speciesRaw),
    level: p.level || 100,
    types: resolveTypes(speciesRaw, win),
    hp: p.hp || 0,
    maxhp: p.maxhp || 1,
    ability: norm(p.ability || p.baseAbility || 'none'),
    item: norm(p.item || 'none'),
    nature: 'Serious',
    evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
    attack: p.stats?.atk || 100,
    defense: p.stats?.def || 100,
    specialAttack: p.stats?.spa || 100,
    specialDefense: p.stats?.spd || 100,
    speed: p.stats?.spe || 100,
    status: STATUS[(p.status || '').toLowerCase()] || 'None',
    restTurns: 0, sleepTurns: 0, weightKg: 0.0,
    moves: padMoves((p.moves || []).map((m: string) => {
      const id = norm(m);
      const rm = activeMoves?.find((am: any) => norm(am.id) === id);
      return {
        id,
        pp: rm?.pp ?? 8,
        disabled: !!rm?.disabled,
      };
    })),
    terastallized: !!p.terastallized,
    teraType: p.teraType || '',
  };
}

export function buildOppPokemon(p: any, win: any) {
  const speciesRaw = p.speciesForme || p.species?.name || p.species;
  const level = p.level || 100;
  const hpPct = Math.min(100, Math.max(0, p.hp || 0));
  const revealed = (p.moveTrack || []).map((m: [string, number]) => ({
    id: norm(m[0]), pp: 8,
  }));
  const computed = computeOpponentStats(speciesRaw, level, win);
  const maxhp = computed.maxhp;
  const hp = Math.max(0, Math.round(hpPct * maxhp / 100));
  return {
    species: norm(speciesRaw),
    level,
    types: resolveTypes(speciesRaw, win),
    hp, maxhp,
    ability: norm(p.ability || p.baseAbility || computed.ability || 'none'),
    item: norm(p.item || 'none'),
    nature: 'Serious',
    evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
    attack: computed.stats.atk,
    defense: computed.stats.def,
    specialAttack: computed.stats.spa,
    specialDefense: computed.stats.spd,
    speed: computed.stats.spe,
    status: STATUS[(p.status || '').toLowerCase()] || 'None',
    restTurns: 0, sleepTurns: 0, weightKg: 0.0,
    moves: padMovesWithPriors(revealed, speciesRaw),
    terastallized: !!p.terastallized,
    teraType: '',
  };
}

export function emptyPokemon() {
  return {
    species: 'none', level: 1, types: [],
    hp: 0, maxhp: 0, ability: 'none', item: 'none',
    nature: 'Serious',
    evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
    attack: 0, defense: 0, specialAttack: 0, specialDefense: 0, speed: 0,
    status: 'None', restTurns: 0, sleepTurns: 0, weightKg: 0.0,
    moves: [0, 0, 0, 0].map(() => ({ id: 'none', pp: 0 })),
    terastallized: false, teraType: '',
  };
}

// Map Showdown's lowercased effect IDs to poke-engine's camelCase keys.
export const SC_KEY_MAP: Record<string, keyof typeof DEFAULT_SC> = {
  spikes: 'spikes',
  stealthrock: 'stealthRock',
  stickyweb: 'stickyWeb',
  toxicspikes: 'toxicSpikes',
  reflect: 'reflect',
  lightscreen: 'lightScreen',
  auroraveil: 'auroraVeil',
  tailwind: 'tailwind',
  safeguard: 'safeguard',
  mist: 'mist',
  luckychant: 'luckyChant',
  healingwish: 'healingWish',
  lunardance: 'lunarDance',
  matblock: 'matBlock',
  quickguard: 'quickGuard',
  wideguard: 'wideGuard',
  craftyshield: 'craftyShield',
};

export function translateSideConditions(raw: any) {
  const out = { ...DEFAULT_SC };
  if (!raw) return out;
  // Showdown format: { "spikes": [displayName, layerCount, duration, ...] }
  for (const [key, val] of Object.entries(raw)) {
    const engineKey = SC_KEY_MAP[key];
    if (!engineKey) continue;
    let count = 1;
    if (Array.isArray(val)) count = (val as any[])[1] || 1;
    else if (typeof val === 'number') count = val;
    out[engineKey] = count;
  }
  return out;
}

// Move IDs that share Showdown's Protect stall-counter mechanic. The
// engine's CONSECUTIVE_PROTECT_CHANCE = 1/3 applies to all of these,
// so every successful use of any of them stacks the same counter.
export const PROTECT_FAMILY_MOVE_IDS = new Set([
  'protect', 'detect', 'banefulbunker', 'burningbulwark', 'kingsshield',
  'obstruct', 'silktrap', 'spikyshield', 'endure',
]);

// Volatile statuses the engine actually models. Anything else from
// Showdown's `active.volatiles` map is dropped — sending unknowns wouldn't
// crash (engine's FromStr defaults to NONE) but pollutes the hashset and
// hides debugging signal. Keep this in sync with the variants listed in
// poke-engine `genx/state.rs:115-225`.
export const ENGINE_VOLATILE_STATUSES = new Set([
  'AQUARING', 'ATTRACT', 'BIDE', 'BOUNCE', 'CHARGE', 'CONFUSION',
  'CURSE', 'DEFENSECURL', 'DESTINYBOND', 'DIG', 'DISABLE', 'DIVE',
  'ELECTRIFY', 'ELECTROSHOT', 'EMBARGO', 'ENCORE', 'ENDURE',
  'FLASHFIRE', 'FLINCH', 'FLY', 'FOCUSENERGY', 'FOLLOWME', 'FORESIGHT',
  'FREEZESHOCK', 'GASTROACID', 'GEOMANCY', 'GLAIVERUSH', 'GRUDGE',
  'HEALBLOCK', 'HELPINGHAND', 'ICEBURN', 'IMPRISON', 'INGRAIN',
  'KINGSSHIELD', 'LASERFOCUS', 'LEECHSEED', 'LIGHTSCREEN', 'LOCKEDMOVE',
  'MAGICCOAT', 'MAGNETRISE', 'MAXGUARD', 'METEORBEAM', 'MINIMIZE',
  'MIRACLEEYE', 'MUSTRECHARGE', 'NIGHTMARE', 'NORETREAT', 'OCTOLOCK',
  'PARTIALLYTRAPPED', 'PERISH4', 'PERISH3', 'PERISH2', 'PERISH1',
  'PHANTOMFORCE', 'POWDER', 'POWERSHIFT', 'POWERTRICK', 'PROTECT',
  'PROTOSYNTHESISATK', 'PROTOSYNTHESISDEF', 'PROTOSYNTHESISSPA',
  'PROTOSYNTHESISSPD', 'PROTOSYNTHESISSPE', 'QUARKDRIVEATK',
  'QUARKDRIVEDEF', 'QUARKDRIVESPA', 'QUARKDRIVESPD', 'QUARKDRIVESPE',
  'RAGE', 'RAGEPOWDER', 'RAZORWIND', 'REFLECT', 'ROOST', 'SALTCURE',
  'SHADOWFORCE', 'SKULLBASH', 'SKYATTACK', 'SKYDROP', 'SILKTRAP',
  'SLOWSTART', 'SMACKDOWN', 'SNATCH', 'SOLARBEAM', 'SOLARBLADE',
  'SPARKLINGARIA', 'SPIKYSHIELD', 'SPOTLIGHT', 'STOCKPILE',
  'SUBSTITUTE', 'SYRUPBOMB', 'TARSHOT', 'TAUNT', 'TELEKINESIS',
  'THROATCHOP', 'TRUANT', 'TORMENT', 'TYPECHANGE', 'UNBURDEN',
  'UPROAR', 'YAWN',
]);

// Volatiles that the engine REQUIRES additional companion fields for.
// Sending them without those fields panics the engine in MCTS rollouts.
//   - DISABLE  still needs the disabled-move reference, which we don't
//              currently surface, so keep filtering it out.
// ENCORE/TAUNT/YAWN/LOCKEDMOVE/CONFUSION/SLOWSTART are now unblocked
// because we wire `volatile_status_durations` (+ `last_used_move` for
// ENCORE) through to the engine in `buildSide` below.
export const VOLATILE_STATUSES_REQUIRING_COMPANION_DATA = new Set([
  'DISABLE',
]);

// Showdown stores active-Pokemon volatiles as an object keyed by
// lowercase compact id (e.g. {taunt: [...], protosynthesisspe: [...]}).
// Map to the engine's uppercase enum names, dropping anything the engine
// doesn't model (formechange, typeadd, airballoon, transform, ...).
export function extractVolatileStatuses(active: any): string[] {
  const v = active?.volatiles;
  if (!v || typeof v !== 'object') return [];
  const out: string[] = [];
  for (const key of Object.keys(v)) {
    const upper = key.toUpperCase();
    if (!ENGINE_VOLATILE_STATUSES.has(upper)) continue;
    if (VOLATILE_STATUSES_REQUIRING_COMPANION_DATA.has(upper)) continue;
    out.push(upper);
  }
  return out;
}

// Engine tick directions for `volatile_status_durations` (state.rs:723,
// genx/generate_instructions.rs:2086+/3263+/3187+). Values matter because
// the engine panics on out-of-range values:
//   taunt:      counts UP   — valid 0/1 (ticks to 2 → removed). Set to 1
//               while active.
//   yawn:       counts UP   — valid 0/1 (1 → puts target to sleep). Set
//               to 1 while active.
//   encore:     counts UP   — valid 0/1 (2 → removed). Set to 1 while
//               active. ENCORE additionally needs last_used_move.
//   lockedmove: counts UP   — valid 0/1 (2 → confused). Set to 1.
//   slowstart:  counts DOWN — 5..1 (0 → removed). Pass Showdown's
//               turnsLeft directly when present, else default 5.
//   confusion:  no panic on 0 but the engine increments for self-hits.
//               Set to 1 when active.
// Showdown's per-volatile value array is `[displayName, turnsLeft, ...]`
// — but turnsLeft is not always present. We default to safe values.
export function extractVolatileDurations(active: any): {
  confusion: number; encore: number; lockedmove: number;
  slowstart: number; taunt: number; yawn: number;
} {
  const out = {
    confusion: 0, encore: 0, lockedmove: 0,
    slowstart: 0, taunt: 0, yawn: 0,
  };
  const v = active?.volatiles;
  if (!v || typeof v !== 'object') return out;
  const readTurnsLeft = (key: string): number | null => {
    const entry = v[key];
    if (Array.isArray(entry) && typeof entry[1] === 'number') return entry[1];
    return null;
  };
  if (v.taunt) out.taunt = 1;
  if (v.yawn) out.yawn = 1;
  if (v.encore) out.encore = 1;
  if (v.lockedmove) out.lockedmove = 1;
  if (v.confusion) out.confusion = 1;
  if (v.slowstart) {
    // SlowStart is a 5-turn countdown. Pass Showdown's reported
    // turnsLeft when available; otherwise assume freshly applied (5).
    const left = readTurnsLeft('slowstart');
    out.slowstart = left != null && left > 0 ? left : 5;
  }
  return out;
}

// Compute consecutive successful Protect-family count for each side's
// active Pokemon by walking the canonical replay buffer. Showdown does
// NOT expose this in `request` JSON or in `sideConditions`, so we have
// to reconstruct it from the protocol stream.
//
// Counting rule (matches engine's `side_conditions.protect`):
//  - +1 on any successful Protect-family move
//  - reset to 0 on: switch/drag/replace, faint, non-protect move,
//    failed Protect (`|-fail|<actor>` immediately after the |move| line),
//    or `|cant|` (Pokemon couldn't move).
export function computeProtectStreak(b: any): { p1: number; p2: number } {
  const stepQueue: string[] = b?.stepQueue || [];
  const streaks = { p1: 0, p2: 0 };
  // Pre-scan to map (line index → line) so we can peek ahead for |-fail|.
  for (let i = 0; i < stepQueue.length; i++) {
    const line = stepQueue[i] || '';
    const parts = line.split('|');
    const kind = parts[1];
    if (kind === 'switch' || kind === 'drag' || kind === 'replace') {
      const actor = parts[2] || '';
      const sideKey = actor.startsWith('p1') ? 'p1' : actor.startsWith('p2') ? 'p2' : null;
      if (sideKey) streaks[sideKey] = 0;
    } else if (kind === 'faint') {
      const actor = parts[2] || '';
      const sideKey = actor.startsWith('p1') ? 'p1' : actor.startsWith('p2') ? 'p2' : null;
      if (sideKey) streaks[sideKey] = 0;
    } else if (kind === 'cant') {
      const actor = parts[2] || '';
      const sideKey = actor.startsWith('p1') ? 'p1' : actor.startsWith('p2') ? 'p2' : null;
      if (sideKey) streaks[sideKey] = 0;
    } else if (kind === 'move') {
      const actor = parts[2] || '';
      const sideKey = actor.startsWith('p1') ? 'p1' : actor.startsWith('p2') ? 'p2' : null;
      if (!sideKey) continue;
      const moveId = norm(parts[3] || '');
      if (PROTECT_FAMILY_MOVE_IDS.has(moveId)) {
        // Look ahead within the same turn for an immediate |-fail| line
        // attributed to this actor — that's how Showdown signals that
        // the random stall-counter check failed.
        let failed = false;
        for (let j = i + 1; j < Math.min(i + 6, stepQueue.length); j++) {
          const peek = stepQueue[j] || '';
          const pp = peek.split('|');
          if (pp[1] === 'turn' || pp[1] === 'move') break;  // next event boundary
          if (pp[1] === '-fail' && (pp[2] || '').startsWith(sideKey)) {
            failed = true;
            break;
          }
        }
        if (failed) streaks[sideKey] = 0;
        else streaks[sideKey] += 1;
      } else {
        streaks[sideKey] = 0;
      }
    }
  }
  return streaks;
}

export function buildSide(
  mons: any[], activeIdx: number, boosts: any, rawSide: any,
  req: any = null, protectStreak: number = 0,
  activeVolatiles: string[] = [],
  lastUsedMove: string = 'move:none',
  activeVolatileDurations: {
    confusion: number; encore: number; lockedmove: number;
    slowstart: number; taunt: number; yawn: number;
  } | null = null,
) {
  const out = mons.slice();
  while (out.length < 6) out.push(emptyPokemon());
  const sc = translateSideConditions(rawSide?.sideConditions);
  // Override with the reconstructed streak — Showdown never sets this
  // key on sideConditions, so the value coming out of translate is 0.
  sc.protect = protectStreak;
  // Substitute HP — Showdown does NOT expose live sub HP to spectators,
  // so derive maxhp/4 (standard sub HP at creation) when the SUBSTITUTE
  // volatile is set on the active Pokemon. Without this the engine
  // treats moves as if no sub exists (Sub-Roost Dragonite, Sub-CM Latios,
  // Sub-Toxic Gliscor were all being mis-evaluated). Engine reads via
  // Side.substitute_health (state.rs:1002).
  let substituteHealth: number | undefined;
  if (activeVolatiles.includes('SUBSTITUTE')) {
    const activeMaxhp = out[activeIdx]?.maxhp || 0;
    if (activeMaxhp > 0) {
      substituteHealth = Math.floor(activeMaxhp / 4);
    }
  }
  return {
    pokemon: out.slice(0, 6),
    activeIndex: activeIdx,
    sideConditions: sc,
    volatileStatuses: activeVolatiles,
    boosts: {
      attack: boosts?.atk || 0,
      defense: boosts?.def || 0,
      specialAttack: boosts?.spa || 0,
      specialDefense: boosts?.spd || 0,
      speed: boosts?.spe || 0,
    },
    forceTrapped: !!req?.active?.[0]?.trapped,
    lastUsedMove,
    ...(substituteHealth !== undefined ? { substituteHealth } : {}),
    ...(activeVolatileDurations !== null
      ? { volatileStatusDurations: activeVolatileDurations }
      : {}),
  };
}

// Derive the LastUsedMove string for the engine schema by looking up
// the active Pokemon's most recently used move against the moves[]
// array we've already built. The engine's `LastUsedMove::deserialize`
// (state.rs:63-76) accepts:
//   - `move:<idx>` — index 0..3 into the active's moves[]
//   - `switch:<idx>` — pokemon index in team
//   - `move:none`   — nothing previous (note: bare `none` panics)
// First-cut: only emit move:<idx>; default to move:none for switches
// or unknown. That's a safe default that doesn't trigger choice-lock
// logic incorrectly.
export function deriveLastUsedMove(active: any, builtMons: any[], activeIdx: number): string {
  const lastMoveId = norm(active?.lastMove?.id || '');
  if (!lastMoveId) return 'move:none';
  const activeMon = builtMons[activeIdx];
  if (!activeMon || !Array.isArray(activeMon.moves)) return 'move:none';
  const idx = activeMon.moves.findIndex((m: any) => norm(m?.id || '') === lastMoveId);
  if (idx < 0 || idx > 3) return 'move:none';
  return `move:${idx}`;
}

// ---- Phase 2: priority-move lookup + speed-modifier chain --------
// Mirrors showdown_copilot/speed_inference_hooks.py:lookup_move_priority
// and stats.py:apply_bot_speed_modifier_chain. Kept inline (no shared
// module) because the extension build already inlines content.ts.
const PRIORITY_PLUS_ONE = new Set([
  'aquajet', 'bulletpunch', 'iceshard', 'machpunch', 'quickattack',
  'shadowsneak', 'suckerpunch', 'vacuumwave', 'watershuriken',
  'accelerock', 'jetpunch', 'icicleshard',
]);
const PRIORITY_PLUS_TWO = new Set(['extremespeed', 'feint']);
const PRIORITY_PLUS_THREE = new Set(['fakeout', 'firstimpression']);

export function lookupMovePriority(moveId: string): number {
  if (PRIORITY_PLUS_ONE.has(moveId)) return 1;
  if (PRIORITY_PLUS_TWO.has(moveId)) return 2;
  if (PRIORITY_PLUS_THREE.has(moveId)) return 3;
  return 0;
}

// Apply the bot's modifier chain to its known speed stat. Mirrors
// stats.py order: boost → paralysis → tailwind → choicescarf → proto.
export function applyBotSpeedModifierChain(opts: {
  baseSpeed: number;
  boostStage: number;       // -6..+6
  hasTailwind: boolean;
  isParalyzed: boolean;
  hasChoiceScarf: boolean;
  hasProtosynthesisSpe: boolean;
}): number {
  const boostMult: Record<number, number> = {
    '-6': 2 / 8, '-5': 2 / 7, '-4': 2 / 6, '-3': 2 / 5, '-2': 2 / 4, '-1': 2 / 3,
    '0': 1.0,
    '1': 3 / 2, '2': 4 / 2, '3': 5 / 2, '4': 6 / 2, '5': 7 / 2, '6': 8 / 2,
  };
  let s = Math.trunc(opts.baseSpeed * (boostMult[opts.boostStage] ?? 1));
  if (opts.isParalyzed) s = Math.trunc(s / 2);   // gen 9 default
  if (opts.hasTailwind) s = s * 2;
  if (opts.hasChoiceScarf) s = Math.trunc(s * 1.5);
  if (opts.hasProtosynthesisSpe) s = Math.trunc(s * 1.5);
  return s;
}

// Parse battle.stepQueue for the move events of a specific turn.
// stepQueue is the canonical replay buffer Showdown maintains; each
// entry is a single protocol line like "|move|p2a: Mon|EQ|p1a: Tusk".
// Returns {moveLog, skipFlags} for the just-finished turn N (i.e.
// events between |turn|N| and |turn|N+1|, exclusive).
export function extractTurnMoveOrder(b: any, turn: number): {
  moveLog: Array<{ side: string; species: string; moveId: string; priority: number }>;
  skipFlags: string[];
} {
  const stepQueue: string[] = b?.stepQueue || [];
  const moveLog: Array<{ side: string; species: string; moveId: string; priority: number }> = [];
  const skipFlags: string[] = [];
  let inTurn = false;
  for (const line of stepQueue) {
    const parts = line.split('|');
    // parts[0] is "" (line starts with |); parts[1] is the kind.
    if (parts[1] === 'turn') {
      const t = parseInt(parts[2] || '0', 10);
      if (t === turn) { inTurn = true; continue; }
      if (t > turn) break;
    }
    if (!inTurn) continue;
    const kind = parts[1];
    if (kind === 'move') {
      const actor = parts[2] || '';
      const moveName = parts[3] || '';
      let side = '';
      if (actor.includes('a:')) side = actor.split('a:')[0];
      else if (actor.includes('b:')) side = actor.split('b:')[0];
      const species = actor.includes(': ')
        ? actor.split(': ').slice(1).join(': ').trim()
        : '';
      const moveId = norm(moveName);
      moveLog.push({ side, species, moveId, priority: lookupMovePriority(moveId) });
    } else if (kind === 'cant') {
      skipFlags.push('cant');
    } else if (kind === 'switch') {
      skipFlags.push('switch');
    } else if (kind === '-activate') {
      const joined = line.toLowerCase();
      if (joined.endsWith('confusion')) skipFlags.push('confusion');
      else if (joined.includes('quick claw')) skipFlags.push('quick_claw');
      else if (joined.includes('quick draw')) skipFlags.push('quick_draw');
    } else if (kind === '-enditem') {
      const joined = line.toLowerCase();
      if (joined.includes('custap berry') || joined.includes('custapberry')) {
        skipFlags.push('custap');
      }
    }
  }
  return { moveLog, skipFlags };
}

// Showdown-side weather/terrain/TR detection for the proxy.
export function detectWeather(b: any): string | null {
  const w = (b?.weather || '').toLowerCase();
  // Showdown emits lowercase keys; map to Plan H expected strings.
  const map: Record<string, string> = {
    raindance: 'RainDance',
    sunnyday: 'SunnyDay',
    sandstorm: 'Sandstorm',
    hail: 'Hail',
    snow: 'Snow',
  };
  return map[w] || null;
}

export function detectTerrain(b: any): string | null {
  const fields: any[] = b?.pseudoWeather || [];
  for (const f of fields) {
    const id = (f?.[0] || '').toString().toLowerCase();
    if (id === 'electricterrain') return 'ELECTRIC_TERRAIN';
    if (id === 'grassyterrain') return 'GRASSY_TERRAIN';
    if (id === 'mistyterrain') return 'MISTY_TERRAIN';
    if (id === 'psychicterrain') return 'PSYCHIC_TERRAIN';
  }
  return null;
}

export function isTrickRoom(b: any): boolean {
  const fields: any[] = b?.pseudoWeather || [];
  return fields.some((f: any) => (f?.[0] || '').toString().toLowerCase() === 'trickroom');
}

// Plan H proxy metadata. Attached to the BattleRequest payload after
// translate() so the proxy can build a per-battle BeliefTracker keyed
// on a stable battleId, key reveals by normalized species (matching
// buildOppPokemon's `species` field), and pick the right format chaos
// cache. The engine ignores unknown top-level fields, so this is safe
// when the request is sent directly to :7267 instead of the proxy.
export function buildPlanHMeta(b: any, br: any, win: any) {
  const farSide = b?.farSide;
  const oppMonsRaw = farSide?.pokemon || [];
  const oppRevealedMoves: Record<string, string[]> = {};
  for (const p of oppMonsRaw) {
    const speciesRaw = p?.speciesForme || p?.species?.name || p?.species || '';
    const key = norm(speciesRaw);
    if (!key) continue;
    const moves = (p?.moveTrack || [])
      .map((m: [string, number]) => norm(m?.[0]))
      .filter((s: string) => !!s);
    oppRevealedMoves[key] = moves;
  }

  // ---- Phase 2: speed-inference metadata ----
  // Showdown sends decision requests at the START of each turn, so
  // when we see turn=N, we can extract move-order for turn N-1.
  const currentTurn = b?.turn || 0;
  const justFinishedTurn = currentTurn - 1;
  let oppMoveOrderThisTurn: any = null;
  let myActiveSpeedPostModifiers = 0;
  const myActive = b?.mySide?.active?.[0];
  const oppActive = farSide?.active?.[0];

  // Compute bot's post-modifier speed (used as the threshold for
  // opp's unknown speed). Read directly from the Showdown side state.
  if (myActive && b?.myPokemon) {
    // Find the corresponding myPokemon entry to get the actual speed stat.
    const target = norm(myActive.species?.name || myActive.speciesForme);
    const myMon = (b.myPokemon || []).find(
      (p: any) => norm(p.speciesForme || p.species) === target,
    );
    const baseSpeed = myMon?.stats?.spe || 0;
    if (baseSpeed > 0) {
      const isParalyzed = (myActive?.status || '').toLowerCase() === 'par';
      const tailwindActive = !!(b?.mySide?.sideConditions?.tailwind);
      const itemId = norm(myMon?.item || '');
      const hasChoiceScarf = itemId === 'choicescarf';
      // protosynthesisspe detection: look in volatileStatuses on active
      const volStatuses: any[] = myActive?.volatileStatuses || [];
      const hasProtoSpe = volStatuses.some((v: any) =>
        ((v?.[0] || v || '') + '').toLowerCase().includes('protosynthesisspe'),
      );
      myActiveSpeedPostModifiers = applyBotSpeedModifierChain({
        baseSpeed,
        boostStage: myActive?.boosts?.spe || 0,
        hasTailwind: tailwindActive,
        isParalyzed,
        hasChoiceScarf,
        hasProtosynthesisSpe: hasProtoSpe,
      });
    }
  }

  if (justFinishedTurn >= 1) {
    const { moveLog, skipFlags } = extractTurnMoveOrder(b, justFinishedTurn);
    const myRole = (b?.mySide?.sideid || '').toLowerCase() || null;
    const activeOppSpeciesRaw = oppActive?.speciesForme || oppActive?.species?.name || '';
    oppMoveOrderThisTurn = {
      turn: justFinishedTurn,
      moveLog,
      skipFlags,
      myRole,
      activeOppSpecies: norm(activeOppSpeciesRaw),
      myActiveSpeedPostModifiers,
    };
  }

  return {
    battleId: br?.id || '',
    format: b?.tier || 'gen9ou',
    oppRevealedMoves,
    // Phase 2 fields (proxy reads when present; absent = Phase 1 client)
    oppMoveOrderThisTurn,
    weather: detectWeather(b),
    terrain: detectTerrain(b),
    inTrickRoom: isTrickRoom(b),
  };
}

export function translate(b: any, req: any = null, win: any) {
  const mySide = b.mySide;
  const farSide = b.farSide;
  const myActive = mySide?.active?.[0];
  let myActiveIdx = 0;
  if (myActive && b.myPokemon) {
    const target = norm(myActive.species?.name || myActive.speciesForme);
    myActiveIdx = b.myPokemon.findIndex((p: any) =>
      norm(p.speciesForme || p.species) === target);
    if (myActiveIdx < 0) myActiveIdx = 0;
  }
  // Overlay Showdown's per-move `disabled` flag and current PP from
  // req.active[0].moves onto the active-slot Pokemon only. On force-switch
  // / team-preview requests `req.active` is undefined, so activeMoves is
  // null and we fall back to the existing pp=8, disabled=false defaults.
  const myActiveMoves = req?.active?.[0]?.moves ?? null;
  const myMons = (b.myPokemon || []).map((p: any, i: number) =>
    buildMyPokemon(p, i === myActiveIdx ? myActiveMoves : null, win));
  const oppMons = (farSide?.pokemon || []).map((p: any) => buildOppPokemon(p, win));
  const oppActive = farSide?.active?.[0];
  let oppActiveIdx = 0;
  if (oppActive && farSide?.pokemon) {
    oppActiveIdx = farSide.pokemon.findIndex((p: any) => p === oppActive);
    if (oppActiveIdx < 0) oppActiveIdx = 0;
  }
  const weather = (b.weather || '').toLowerCase();
  // Reconstruct each side's consecutive Protect-family count from the
  // protocol stream — Showdown does NOT expose this in `request` JSON.
  // Engine uses (1/3)^N for the next attempt's success chance.
  const protectStreaks = computeProtectStreak(b);
  const mySideId = (b.mySide?.sideid || b.mySide?.id || 'p1') as 'p1' | 'p2';
  const myStreak = mySideId === 'p2' ? protectStreaks.p2 : protectStreaks.p1;
  const oppStreak = mySideId === 'p2' ? protectStreaks.p1 : protectStreaks.p2;
  // Active-Pokemon volatile statuses — was hardcoded `[]` so the engine
  // was blind to Taunt, Encore, Substitute, Booster Energy direction
  // (Protosynthesis/Quark Drive), Locked Move (Outrage), Slow Start,
  // Leech Seed, Magma Storm trap, etc. (P0 audit finding 2026-04-29.)
  const myVolatiles = extractVolatileStatuses(myActive);
  const oppVolatiles = extractVolatileStatuses(oppActive);
  // last_used_move — gives engine choice-lock memory (Scarf Urshifu
  // locked into Surging Strikes, CB Dragonite locked into Outrage,
  // etc.). Without this the engine treats all 4 moves as legal.
  const myLastUsed = deriveLastUsedMove(myActive, myMons, myActiveIdx);
  const oppLastUsed = deriveLastUsedMove(oppActive, oppMons, oppActiveIdx);
  // volatile_status_durations — engine panics if TAUNT/YAWN/ENCORE/
  // LOCKEDMOVE are set with a 0 counter; SLOWSTART needs a >0 counter
  // for the wears-off tick to run. Required to lift the previous
  // companion-data filter on those volatiles.
  const myDurations = extractVolatileDurations(myActive);
  const oppDurations = extractVolatileDurations(oppActive);
  return {
    sideOne: buildSide(myMons, myActiveIdx, myActive?.boosts, mySide, req, myStreak, myVolatiles, myLastUsed, myDurations),
    sideTwo: buildSide(oppMons, oppActiveIdx, oppActive?.boosts, farSide, null, oppStreak, oppVolatiles, oppLastUsed, oppDurations),
    weather: {
      weatherType: weather || 'none',
      // Preserve 0 (last turn of weather); only fall back to -1 when truly absent.
      turnsRemaining: typeof b.weatherTimeLeft === 'number' ? b.weatherTimeLeft : -1,
    },
    terrain: (() => {
      const t = detectTerrain(b);
      if (!t) return { terrainType: 'none', turnsRemaining: -1 };
      // Engine's Terrain enum has no underscore (ELECTRICTERRAIN, not
      // ELECTRIC_TERRAIN); detectTerrain returns the underscore form for
      // _planH.terrain compatibility, so strip it before sending here.
      const tId = t.toLowerCase().replace('_', '');
      const entry = (b.pseudoWeather || []).find(
        (pw: any) => (pw?.[0] || '').toString().toLowerCase() === tId,
      );
      const turns = typeof entry?.[2] === 'number' ? entry[2] : 5;
      return { terrainType: t.replace('_', ''), turnsRemaining: turns };
    })(),
    trickRoom: (b.pseudoWeather || []).some((pw: any) => pw[0] === 'trickroom'),
    // Note: timeLimitMs / updateIntervalMs are tuning constants owned by
    // the page-side caller (content.ts). Caller is responsible for
    // attaching them onto the returned payload before POSTing.
  };
}
