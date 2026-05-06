// Showdown Copilot — content script running in MAIN world (page context).
// Reads Showdown's app / Dex globals directly, fetches the local poke-engine
// /analyze/stream endpoint, streams NDJSON updates into a floating panel.
import { priorMovesForSpecies } from '../utils/chaos-priors';
import { parseBattlePostMortem, type BattlePostMortem } from '../utils/post-mortem';

export default defineContentScript({
  matches: ['https://play.pokemonshowdown.com/*'],
  runAt: 'document_idle',
  world: 'MAIN',

  main() {
    // page-context globals (declared loose so TS doesn't choke)
    const win: any = window;

    // Plan H proxy on :7271 forwards to engine on :7267 with belief-aware
    // opp-Pokemon overlays. If the proxy isn't running the request fails;
    // start it with `python -m showdown_copilot.proxy`. To bypass the proxy
    // entirely (e.g., when only the engine is running), point this at :7267.
    const ENGINE_URL = 'http://localhost:7271/analyze/stream';
    const POLL_MS = 500;
    const ANALYSIS_TIME_MS = 6000;
    const UPDATE_INTERVAL_MS = 400;

    // ---- Gen 9 type chart (for lead-selection heuristic) -----------------
    const TYPE_CHART: Record<string, Record<string, number>> = {
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

    const DEFAULT_SC = {
      auroraVeil: 0, craftyShield: 0, healingWish: 0, lightScreen: 0,
      luckyChant: 0, lunarDance: 0, matBlock: 0, mist: 0,
      protect: 0, quickGuard: 0, reflect: 0, safeguard: 0,
      spikes: 0, stealthRock: 0, stickyWeb: 0, tailwind: 0,
      toxicCount: 0, toxicSpikes: 0, wideGuard: 0,
    };

    const STATUS: Record<string, string> = {
      brn: 'Burn', frz: 'Freeze', par: 'Paralyze',
      psn: 'Poison', slp: 'Sleep', tox: 'Toxic',
    };

    // ---- helpers ---------------------------------------------------------
    const norm = (s: any) =>
      (s || '').toString().toLowerCase().replace(/[^a-z0-9]/g, '');

    const padMoves = (arr: any[]) => {
      const m = arr.slice(0, 4);
      while (m.length < 4) m.push({ id: 'none', pp: 0 });
      return m;
    };

    function padMovesWithPriors(revealed: any[], speciesDisplay: string) {
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

    function resolveTypes(speciesName: string): string[] {
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

    function computeOpponentStats(speciesName: string, level: number) {
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

    function typeMult(atkType: string, defTypes: string[]) {
      let m = 1;
      for (const dt of defTypes || []) {
        const eff = TYPE_CHART[atkType]?.[dt];
        if (eff !== undefined) m *= eff;
      }
      return m;
    }

    function leadScore(myMon: any, oppTeam: any[]) {
      const myTypes = resolveTypes(myMon.speciesForme || myMon.species);
      let total = 0;
      for (const opp of oppTeam) {
        const oppName = opp.species?.name || opp.speciesForme || opp.species;
        const oppTypes = resolveTypes(oppName);
        let myBest = 0, theirBest = 0;
        for (const t of myTypes) myBest = Math.max(myBest, typeMult(t, oppTypes));
        for (const t of oppTypes) theirBest = Math.max(theirBest, typeMult(t, myTypes));
        total += myBest - theirBest;
      }
      return total;
    }

    // ---- Lead matrix: archetype detection + per-archetype lead pick ------
    // Hand-tuned for the v2 Tyranitar team (Diancie/Heatran/Tyranitar/
    // Gholdengo/Dragonite/Urshifu-Rapid-Strike). When opp archetype matches
    // and our team has the suggested lead, returns {lead, reason, archetype};
    // otherwise returns null and the panel falls back to the leadScore
    // heuristic. See analysis/team-build/2026-04-29-natdex-team-v2-tyranitar.md §5.
    const LM_RAIN = new Set(['pelipper']);
    const LM_SUN = new Set(['torkoal', 'charizardmegay', 'ninetalesalola']);
    const LM_ZOROARK = new Set(['zoroark', 'zoroarkhisui']);
    const LM_TR = new Set(['hatterene', 'indeedeefemale', 'magearna', 'porygon2']);
    const LM_STALL = new Set([
      'alomomola', 'toxapex', 'chansey', 'blissey', 'clodsire',
      'pecharunt', 'corviknight', 'gliscor', 'dondozo', 'ferrothorn',
    ]);
    const LM_HO = new Set([
      'volcarona', 'ironvaliant', 'ceruledge', 'ogerponwellspring',
      'dianciemega', 'diancie', 'gholdengo', 'ironbundle',
    ]);
    const LM_HAZARD_PAIRS: Array<Set<string>> = [
      new Set(['garchomp', 'irontreads']),
      new Set(['garchomp', 'landorustherian']),
      new Set(['landorustherian', 'heatran']),
    ];

    function detectOppArchetype(oppSpeciesNorm: Set<string>): string {
      const has = (set: Set<string>) => {
        for (const s of set) if (oppSpeciesNorm.has(s)) return true;
        return false;
      };
      const intersectCount = (set: Set<string>) => {
        let n = 0;
        for (const s of set) if (oppSpeciesNorm.has(s)) n++;
        return n;
      };
      if (has(LM_RAIN)) return 'rain';
      if (has(LM_SUN)) return 'sun';
      if (has(LM_ZOROARK)) return 'zoroark_ho';
      if (has(LM_TR)) return 'trick_room';
      if (oppSpeciesNorm.has('tyranitar') && oppSpeciesNorm.has('excadrill')) return 'sand_ho';
      if (intersectCount(LM_STALL) >= 3) return 'stall';
      for (const pair of LM_HAZARD_PAIRS) {
        let hits = 0;
        for (const s of pair) if (oppSpeciesNorm.has(s)) hits++;
        if (hits === pair.size) return 'hazard_stack';
      }
      if (intersectCount(LM_HO) >= 3) return 'hyper_offense';
      return 'balance_or_unknown';
    }

    const LEAD_BY_ARCHETYPE: Record<string, { lead: string; reason: string }> = {
      rain:                { lead: 'Tyranitar',   reason: 'Sand cancels rain on switch-in.' },
      sun:                 { lead: 'Tyranitar',   reason: 'Sand cancels sun + Stone Edge OHKOs CharY (Rock 4x).' },
      zoroark_ho:          { lead: 'Tyranitar',   reason: 'Pursuit traps Zoroark on Illusion-drop (Dark 2x).' },
      trick_room:          { lead: 'Diancie',     reason: 'Magic Bounce + Diamond Storm chunks Hatterene/setup.' },
      sand_ho:             { lead: 'Heatran',     reason: 'Magma Storm + Taunt blunts Excadrill setup.' },
      stall:               { lead: 'Heatran',     reason: 'Magma Storm traps Alo/Toxapex; Taunt blocks recovery.' },
      hazard_stack:        { lead: 'Diancie',     reason: 'Magic Bounce reflects opp rocks; Earth Power 2HKOs Heatran.' },
      hyper_offense:       { lead: 'Diancie',     reason: 'Magic Bounce + Diamond Storm OHKOs +0 Volc.' },
      balance_or_unknown:  { lead: 'Diancie',     reason: 'Default — Magic Bounce protects vs hazards.' },
    };

    function leadMatrixRecommendation(
      myTeam: any[], oppTeam: any[]
    ): { lead: string; reason: string; archetype: string; myMon: any } | null {
      if (!myTeam.length || !oppTeam.length) return null;
      const oppNorm = new Set(
        oppTeam.map((p: any) => norm(p.species?.name || p.speciesForme || p.species || ''))
      );
      const archetype = detectOppArchetype(oppNorm);
      const pick = LEAD_BY_ARCHETYPE[archetype];
      if (!pick) return null;
      const target = norm(pick.lead);
      const myMon = myTeam.find(
        (m: any) => norm(m.speciesForme || m.species || '') === target
      );
      if (!myMon) return null;  // user is on a different team — fall back to heuristic
      return { lead: pick.lead, reason: pick.reason, archetype, myMon };
    }

    // ---- translation: Showdown battle → poke-engine payload --------------
    function buildMyPokemon(p: any, activeMoves: any[] | null = null) {
      const speciesRaw = p.speciesForme || p.species;
      return {
        species: norm(speciesRaw),
        level: p.level || 100,
        types: resolveTypes(speciesRaw),
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

    function buildOppPokemon(p: any) {
      const speciesRaw = p.speciesForme || p.species?.name || p.species;
      const level = p.level || 100;
      const hpPct = Math.min(100, Math.max(0, p.hp || 0));
      const revealed = (p.moveTrack || []).map((m: [string, number]) => ({
        id: norm(m[0]), pp: 8,
      }));
      const computed = computeOpponentStats(speciesRaw, level);
      const maxhp = computed.maxhp;
      const hp = Math.max(0, Math.round(hpPct * maxhp / 100));
      return {
        species: norm(speciesRaw),
        level,
        types: resolveTypes(speciesRaw),
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

    function emptyPokemon() {
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
    const SC_KEY_MAP: Record<string, keyof typeof DEFAULT_SC> = {
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

    function translateSideConditions(raw: any) {
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
    const PROTECT_FAMILY_MOVE_IDS = new Set([
      'protect', 'detect', 'banefulbunker', 'burningbulwark', 'kingsshield',
      'obstruct', 'silktrap', 'spikyshield', 'endure',
    ]);

    // Volatile statuses the engine actually models. Anything else from
    // Showdown's `active.volatiles` map is dropped — sending unknowns wouldn't
    // crash (engine's FromStr defaults to NONE) but pollutes the hashset and
    // hides debugging signal. Keep this in sync with the variants listed in
    // poke-engine `genx/state.rs:115-225`.
    const ENGINE_VOLATILE_STATUSES = new Set([
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
    const VOLATILE_STATUSES_REQUIRING_COMPANION_DATA = new Set([
      'DISABLE',
    ]);

    // Showdown stores active-Pokemon volatiles as an object keyed by
    // lowercase compact id (e.g. {taunt: [...], protosynthesisspe: [...]}).
    // Map to the engine's uppercase enum names, dropping anything the engine
    // doesn't model (formechange, typeadd, airballoon, transform, ...).
    function extractVolatileStatuses(active: any): string[] {
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
    function extractVolatileDurations(active: any): {
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
    function computeProtectStreak(b: any): { p1: number; p2: number } {
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

    function buildSide(
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
    function deriveLastUsedMove(active: any, builtMons: any[], activeIdx: number): string {
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

    function lookupMovePriority(moveId: string): number {
      if (PRIORITY_PLUS_ONE.has(moveId)) return 1;
      if (PRIORITY_PLUS_TWO.has(moveId)) return 2;
      if (PRIORITY_PLUS_THREE.has(moveId)) return 3;
      return 0;
    }

    // Apply the bot's modifier chain to its known speed stat. Mirrors
    // stats.py order: boost → paralysis → tailwind → choicescarf → proto.
    function applyBotSpeedModifierChain(opts: {
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
    function extractTurnMoveOrder(b: any, turn: number): {
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
    function detectWeather(b: any): string | null {
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

    function detectTerrain(b: any): string | null {
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

    function isTrickRoom(b: any): boolean {
      const fields: any[] = b?.pseudoWeather || [];
      return fields.some((f: any) => (f?.[0] || '').toString().toLowerCase() === 'trickroom');
    }

    // Plan H proxy metadata. Attached to the BattleRequest payload after
    // translate() so the proxy can build a per-battle BeliefTracker keyed
    // on a stable battleId, key reveals by normalized species (matching
    // buildOppPokemon's `species` field), and pick the right format chaos
    // cache. The engine ignores unknown top-level fields, so this is safe
    // when the request is sent directly to :7267 instead of the proxy.
    function buildPlanHMeta(b: any, br: any) {
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

    function translate(b: any, req: any = null) {
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
        buildMyPokemon(p, i === myActiveIdx ? myActiveMoves : null));
      const oppMons = (farSide?.pokemon || []).map(buildOppPokemon);
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
        timeLimitMs: ANALYSIS_TIME_MS,
        updateIntervalMs: UPDATE_INTERVAL_MS,
      };
    }

    // ---- UI -------------------------------------------------------------
    const panel = document.createElement('div');
    panel.id = 'sc-panel';
    panel.innerHTML = [
      '<div class="sc-header">Copilot — idle</div>',
      '<div class="sc-lead-matrix" style="display:none"></div>',
      '<div class="sc-best">—</div>',
      '<div class="sc-stats">—</div>',
      '<div class="sc-pv">PV: —</div>',
      '<div class="sc-alts">—</div>',
      '<div class="sc-notes-header" title="Battle note (press N for per-turn notes)">📝 Battle note <span class="sc-notes-toggle">[show]</span></div>',
      '<div class="sc-notes-body" style="display:none"><textarea class="sc-battle-note" placeholder="Free-form notes for this battle..." spellcheck="false"></textarea></div>',
    ].join('');

    const style = document.createElement('style');
    style.textContent = `
      #sc-panel {
        position: fixed; bottom: 20px; right: 20px;
        width: 320px; background: #1a1a1a; color: #eee;
        border: 2px solid #4af; border-radius: 6px;
        padding: 10px 12px; font-family: ui-monospace, "Menlo", monospace;
        font-size: 12px; z-index: 2147483647;
        box-shadow: 0 4px 16px rgba(0,0,0,0.5);
        user-select: none;
      }
      #sc-panel .sc-header { font-weight: bold; margin-bottom: 6px; color: #4af; }
      #sc-panel .sc-lead-matrix {
        font-size: 11px; color: #fc6; margin: -2px 0 6px 0;
        padding: 4px 6px; border-left: 2px solid #fc6;
        background: rgba(255,200,80,0.08); word-break: break-word;
      }
      #sc-panel .sc-best { font-size: 17px; font-weight: bold; color: #7fe; margin: 4px 0; }
      #sc-panel .sc-stats { font-size: 11px; color: #888; margin-bottom: 6px; }
      #sc-panel .sc-pv { font-size: 11px; color: #ddd; margin-bottom: 4px; word-break: break-word; }
      #sc-panel .sc-alts { font-size: 11px; color: #ccc; word-break: break-word; }
      #sc-panel .sc-notes-header {
        font-size: 11px; color: #fc6; margin-top: 8px; cursor: pointer;
        border-top: 1px dashed #333; padding-top: 6px;
      }
      #sc-panel .sc-notes-toggle { color: #888; font-style: italic; margin-left: 4px; }
      #sc-panel .sc-notes-body { margin-top: 4px; }
      #sc-panel .sc-battle-note {
        width: 100%; box-sizing: border-box;
        background: #0e0e0e; color: #ddd; border: 1px solid #333;
        border-radius: 4px; padding: 4px 6px;
        font: inherit; font-size: 11px; min-height: 40px; max-height: 200px;
        resize: vertical;
      }
      #sc-note-modal {
        position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
        background: rgba(0,0,0,0.6); display: none;
        align-items: center; justify-content: center;
        z-index: 2147483646;
      }
      #sc-note-modal.visible { display: flex; }
      #sc-note-modal .sc-note-box {
        background: #1a1a1a; border: 2px solid #4af; border-radius: 6px;
        padding: 14px 18px; min-width: 380px;
        font-family: ui-monospace, "Menlo", monospace;
      }
      #sc-note-modal .sc-note-label {
        color: #4af; font-size: 12px; font-weight: bold; margin-bottom: 6px;
      }
      #sc-note-modal .sc-note-input {
        width: 100%; box-sizing: border-box;
        background: #0e0e0e; color: #eee; border: 1px solid #333;
        border-radius: 4px; padding: 6px 8px;
        font: inherit; font-size: 13px;
      }
      #sc-note-modal .sc-note-hint {
        color: #888; font-size: 10px; margin-top: 6px;
      }
    `;
    document.head.appendChild(style);
    document.body.appendChild(panel);

    const hdrEl = panel.querySelector<HTMLDivElement>('.sc-header')!;
    const leadMatrixEl = panel.querySelector<HTMLDivElement>('.sc-lead-matrix')!;
    const bestEl = panel.querySelector<HTMLDivElement>('.sc-best')!;
    const statsEl = panel.querySelector<HTMLDivElement>('.sc-stats')!;
    const pvEl = panel.querySelector<HTMLDivElement>('.sc-pv')!;
    const altsEl = panel.querySelector<HTMLDivElement>('.sc-alts')!;
    const notesHeaderEl = panel.querySelector<HTMLDivElement>('.sc-notes-header')!;
    const notesBodyEl = panel.querySelector<HTMLDivElement>('.sc-notes-body')!;
    const notesToggleEl = panel.querySelector<HTMLSpanElement>('.sc-notes-toggle')!;
    const battleNoteTextarea = panel.querySelector<HTMLTextAreaElement>('.sc-battle-note')!;

    // ---- Annotation feature ---------------------------------------------
    // Per-turn notes (keyboard 'N') + per-battle freeform notes (textarea).
    // Stored under sc:turn-notes:<battleId> and sc:battle-note:<battleId>
    // during play; merged into the final post-mortem at persist time.
    // Also fire-and-forget POSTed to the proxy at /annotation so they
    // land on disk at analysis/play-notes/YYYY-MM-DD.jsonl independently
    // of localStorage capture.
    const PROXY_NOTE_URL = 'http://localhost:7271/annotation';

    // Updated on every engine tick so the keyboard handler always knows
    // the current battle. null if no live battle.
    const annotationState: { battleId: string | null; turn: number } = {
      battleId: null,
      turn: 0,
    };

    function readTurnNotes(battleId: string): Record<string, string> {
      try {
        return JSON.parse(localStorage.getItem(`sc:turn-notes:${battleId}`) || '{}');
      } catch {
        return {};
      }
    }
    function writeTurnNote(battleId: string, turn: number, text: string): void {
      const notes = readTurnNotes(battleId);
      if (text.trim() === '') delete notes[String(turn)];
      else notes[String(turn)] = text;
      localStorage.setItem(`sc:turn-notes:${battleId}`, JSON.stringify(notes));
    }
    function readBattleNote(battleId: string): string {
      return localStorage.getItem(`sc:battle-note:${battleId}`) || '';
    }
    function writeBattleNote(battleId: string, text: string): void {
      if (text === '') localStorage.removeItem(`sc:battle-note:${battleId}`);
      else localStorage.setItem(`sc:battle-note:${battleId}`, text);
    }
    function postAnnotation(payload: {
      battleId: string;
      turn: number;
      kind: 'turn' | 'battle';
      text: string;
    }): void {
      // Fire-and-forget — do not block UX. localStorage is the fallback.
      fetch(PROXY_NOTE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, timestampMs: Date.now() }),
        keepalive: true,
      }).catch(() => { /* proxy down — localStorage still has it */ });
    }

    // Modal overlay (separate from panel so z-index / centering works cleanly)
    const noteModal = document.createElement('div');
    noteModal.id = 'sc-note-modal';
    noteModal.innerHTML = [
      '<div class="sc-note-box">',
      '  <div class="sc-note-label">Note for T<span class="sc-note-turn">?</span>:</div>',
      '  <input type="text" class="sc-note-input" maxlength="500" placeholder="What did you notice?" />',
      '  <div class="sc-note-hint">Enter to save · Esc to cancel</div>',
      '</div>',
    ].join('');
    document.body.appendChild(noteModal);
    const noteModalTurnEl = noteModal.querySelector<HTMLSpanElement>('.sc-note-turn')!;
    const noteModalInput = noteModal.querySelector<HTMLInputElement>('.sc-note-input')!;

    function openNoteModal(): void {
      if (!annotationState.battleId) return;
      const turn = annotationState.turn;
      noteModalTurnEl.textContent = String(turn);
      const existing = readTurnNotes(annotationState.battleId)[String(turn)] || '';
      noteModalInput.value = existing;
      noteModal.classList.add('visible');
      noteModalInput.focus();
      noteModalInput.select();
    }
    function closeNoteModal(): void {
      noteModal.classList.remove('visible');
      noteModalInput.value = '';
    }
    function saveNoteFromModal(): void {
      const battleId = annotationState.battleId;
      if (!battleId) { closeNoteModal(); return; }
      const turn = annotationState.turn;
      const text = noteModalInput.value.trim();
      writeTurnNote(battleId, turn, text);
      if (text) postAnnotation({ battleId, turn, kind: 'turn', text });
      closeNoteModal();
    }

    noteModalInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        saveNoteFromModal();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        closeNoteModal();
      }
      e.stopPropagation();
    });

    // Global keyboard handler — opens modal on plain 'n' key when no input
    // is focused. Wrapped in capture-phase to avoid Showdown handlers eating it.
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'n' && e.key !== 'N') return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const ae = document.activeElement;
      const tag = (ae?.tagName || '').toUpperCase();
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (ae as HTMLElement)?.isContentEditable) return;
      if (!annotationState.battleId) return;
      e.preventDefault();
      e.stopPropagation();
      openNoteModal();
    }, true);

    // Per-battle note: collapsible textarea + autosave (debounced 600ms).
    let battleNoteSaveTimer: number | null = null;
    notesHeaderEl.addEventListener('click', () => {
      const showing = notesBodyEl.style.display !== 'none';
      notesBodyEl.style.display = showing ? 'none' : 'block';
      notesToggleEl.textContent = showing ? '[show]' : '[hide]';
    });
    battleNoteTextarea.addEventListener('input', () => {
      const battleId = annotationState.battleId;
      if (!battleId) return;
      if (battleNoteSaveTimer !== null) clearTimeout(battleNoteSaveTimer);
      battleNoteSaveTimer = window.setTimeout(() => {
        const text = battleNoteTextarea.value;
        writeBattleNote(battleId, text);
        if (text.trim()) postAnnotation({ battleId, turn: 0, kind: 'battle', text });
      }, 600);
    });

    // When the active battle changes, swap textarea contents to that battle's note.
    let lastAnnotationBattleId: string | null = null;
    function syncBattleNoteUi(): void {
      const battleId = annotationState.battleId;
      if (battleId === lastAnnotationBattleId) return;
      lastAnnotationBattleId = battleId;
      battleNoteTextarea.value = battleId ? readBattleNote(battleId) : '';
    }

    // ---- Battle history (for post-game analysis) ------------------------
    type DecisionRecord = {
      battleId: string;
      turn: number;
      rqid: number;
      tStartMs: number;
      tEndMs?: number;
      forceSwitch: boolean;
      state: any;
      payload: any;
      updates: any[];
      final?: any;
    };
    type BattleResult = {
      battleId: string;
      winner?: string;
      turns: number;
      endedAtMs: number;
    };
    const scHistory: DecisionRecord[] = [];
    const scResults: BattleResult[] = [];
    let lastEndedBattleId: string | null = null;

    const dumpedBattleIds = new Set<string>();

    function persistPostMortem(pm: BattlePostMortem): void {
      // Overlay any in-battle annotations from temp localStorage keys
      // onto the parsed post-mortem before persisting.
      const turnNotes = readTurnNotes(pm.battleId);
      const battleNote = readBattleNote(pm.battleId);
      if (battleNote) pm.battleNote = battleNote;
      for (const t of pm.turns) {
        const note = turnNotes[String(t.turn)];
        if (note) t.userNote = note;
      }
      const key = `sc:postmortem:${pm.battleId}`;
      const json = JSON.stringify(pm);
      try {
        localStorage.setItem(key, json);
        return;
      } catch (e) {
        if (!(e instanceof DOMException) || e.name !== 'QuotaExceededError') throw e;
      }
      // Quota hit: prune oldest until it fits or store is empty.
      const all: { key: string; endedAtMs: number }[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith('sc:postmortem:')) continue;
        try {
          const o = JSON.parse(localStorage.getItem(k) || '{}');
          if (typeof o.endedAtMs === 'number') all.push({ key: k, endedAtMs: o.endedAtMs });
        } catch {}
      }
      all.sort((a, b) => a.endedAtMs - b.endedAtMs);
      let pruned = 0;
      for (const { key: oldKey } of all) {
        localStorage.removeItem(oldKey);
        pruned++;
        try {
          localStorage.setItem(key, json);
          console.log(`[sc:postmortem] pruned ${pruned} old battle(s) to fit new dump`);
          return;
        } catch {
          // keep pruning
        }
      }
      console.error('[sc:postmortem] could not fit new dump even after pruning all entries');
    }

    (win as any).__scHistory = () => scHistory;
    (win as any).__scResults = () => scResults;
    (win as any).__scSummary = () =>
      scHistory.map(r => ({
        battle: r.battleId,
        turn: r.turn,
        myActive: r.state?.myActive,
        myHp: r.state?.my?.activeHpPct,
        oppActive: r.state?.oppActive,
        oppHp: r.state?.opp?.activeHpPct,
        pick: r.final?.bestMove,
        conf: r.final ? Math.round((r.final.confidence || 0) * 100) + '%' : null,
        sims: r.final?.sims,
        depth: r.final?.depth,
        took: r.tEndMs ? r.tEndMs - r.tStartMs + 'ms' : null,
        pv: r.final?.pv,
      }));
    (win as any).__scDumpBattle = () => JSON.stringify(scHistory, null, 2);

    function snapshotSide(s: any) {
      const active = s?.active?.[0];
      const mvTrack = (active?.moveTrack || []).map((m: [string, number]) => m[0]);
      return {
        activeSpecies: active?.species?.name || active?.speciesForme || null,
        activeHp: active?.hp ?? null,
        activeMaxhp: active?.maxhp ?? null,
        activeHpPct: active?.maxhp
          ? Math.round(((active.hp || 0) / active.maxhp) * 100)
          : null,
        status: active?.status || null,
        item: active?.item || null,
        ability: active?.ability || active?.baseAbility || null,
        boosts: active?.boosts || {},
        revealedMoves: mvTrack,
        team: (s?.pokemon || []).map((p: any) => ({
          species: p.species?.name || p.speciesForme || p.species,
          fainted: !!p.fainted,
          hpPct: p.maxhp ? Math.round(((p.hp || 0) / p.maxhp) * 100) : null,
          status: p.status || null,
        })),
        sideConditions: s?.sideConditions || {},
      };
    }

    function snapshotState(b: any) {
      return {
        turn: b.turn,
        weather: b.weather || 'none',
        pseudoWeather: (b.pseudoWeather || []).map((pw: any) => pw[0]),
        myActive: b.mySide?.active?.[0]?.species?.name
          || b.mySide?.active?.[0]?.speciesForme || null,
        oppActive: b.farSide?.active?.[0]?.species?.name
          || b.farSide?.active?.[0]?.speciesForme || null,
        my: snapshotSide(b.mySide),
        opp: snapshotSide(b.farSide),
      };
    }

    function pct(v: number | undefined) {
      return ((v || 0) * 100).toFixed(0) + '%';
    }

    function labelMove(moveStr: string): string {
      if (!moveStr) return '—';
      const n = norm(moveStr);
      const team = win.app?.curRoom?.battle?.myPokemon || [];
      for (const p of team) {
        if (norm(p.speciesForme || p.species) === n) {
          return `→ ${p.speciesForme || p.species}`;
        }
      }
      return moveStr.toLowerCase();
    }

    function renderUpdate(u: any) {
      const arrow = u.event === 'final' ? '▲' : '•';
      const conf = ((u.confidence || 0) * 100).toFixed(0);
      bestEl.textContent = `${labelMove(u.bestMove)}  ${arrow} ${conf}%`;
      statsEl.textContent =
        `sims ${(u.sims || 0).toLocaleString()}  depth ${u.depth || 0}` +
        (u.error ? `  ERROR: ${u.error}` : '');
      pvEl.textContent = `PV: ${(u.pv || []).join(' → ') || '—'}`;
      altsEl.textContent = (u.alternatives || [])
        .slice(0, 3)
        .map((a: any) => `${labelMove(a.move)} ${((a.confidence || 0) * 100).toFixed(0)}%`)
        .join(' | ') || '—';
    }

    // ---- engine call with native fetch streaming ------------------------
    let abortCtrl: AbortController | null = null;

    // When forceSwitch is true, the engine's bestMove may be a move like
    // "THUNDERPUNCH" that the user can't legally pick — Showdown only accepts
    // a Pokemon. Filter engine response to switches; fall back to leadScore
    // heuristic vs opp's current active if engine surfaced no switches in
    // its top-K.
    function applyForceSwitchOverride(u: any) {
      const b = win.app?.curRoom?.battle;
      if (!b) return;
      const myTeam = b.myPokemon || [];
      const findSpecies = (moveStr: string): string | null => {
        if (!moveStr) return null;
        const n = norm(moveStr);
        for (const p of myTeam) {
          if (norm(p.speciesForme || p.species) === n) {
            return p.speciesForme || p.species;
          }
        }
        return null;
      };
      const allPicks = [
        { move: u.bestMove, confidence: u.confidence || 0 },
        ...((u.alternatives || []).map((a: any) => ({ move: a.move, confidence: a.confidence || 0 }))),
      ];
      const switchesFromEngine = allPicks
        .map((p: any) => ({ species: findSpecies(p.move), confidence: p.confidence }))
        .filter((s: any) => s.species);
      if (switchesFromEngine.length) {
        const best = switchesFromEngine[0];
        hdrEl.textContent = 'Copilot — force switch';
        bestEl.textContent = `→ ${best.species}  ▲ ${pct(best.confidence)}`;
        altsEl.textContent = switchesFromEngine.slice(1, 4)
          .map((s: any) => `→ ${s.species} ${pct(s.confidence)}`)
          .join(' | ') || '—';
        statsEl.textContent = 'engine-ranked switch';
        return;
      }
      // Heuristic fallback: leadScore vs opp's current active
      const myActive = b.mySide?.active?.[0];
      const myActiveSpecies = norm(myActive?.species?.name || myActive?.speciesForme || '');
      const oppActive = b.farSide?.active?.[0];
      if (!oppActive) return;
      const candidates = myTeam
        .filter((p: any) => !p.fainted && norm(p.speciesForme || p.species) !== myActiveSpecies)
        .map((m: any) => ({
          species: m.speciesForme || m.species,
          score: leadScore(m, [oppActive]),
        }))
        .sort((a: any, b: any) => b.score - a.score);
      if (!candidates.length) return;
      const best = candidates[0];
      hdrEl.textContent = 'Copilot — force switch (heuristic)';
      bestEl.textContent = `→ ${best.species}  [heuristic ${best.score.toFixed(1)}]`;
      altsEl.textContent = candidates.slice(1, 4)
        .map((c: any) => `${c.species} (${c.score.toFixed(1)})`)
        .join(' | ') || '—';
      statsEl.textContent = 'heuristic — engine had no switch in top-K';
    }

    // Test hook: lets the controller synthetically trigger the force-switch
    // override from the console/MCP without waiting for a live force switch.
    (win as any).__scTestForceSwitch = (u: any) => applyForceSwitchOverride(u);

    function handleEngineUpdate(u: any, record: DecisionRecord | null) {
      renderUpdate(u);
      if (!record) return;
      record.updates.push(u);
      if (u.event === 'final' || u.error) {
        record.final = u;
        record.tEndMs = Date.now();
        // Force-switch post-processing: panel must recommend a Pokemon.
        if (record.forceSwitch && u.bestMove && !u.error) {
          applyForceSwitchOverride(u);
        }
        const alts = (u.alternatives || [])
          .slice(0, 3)
          .map((a: any) => `${a.move} ${pct(a.confidence)}`)
          .join(', ') || 'none';
        const pv = (u.pv || []).join(' → ') || '—';
        console.log(
          `[sc:battle] T${record.turn} FINAL → ${u.bestMove} ` +
          `(${pct(u.confidence)}) | sims ${(u.sims || 0).toLocaleString()} ` +
          `depth ${u.depth || 0} | ${record.tEndMs! - record.tStartMs}ms` +
          (record.forceSwitch ? ' [forceSwitch: post-filtered]' : '')
        );
        console.log(`[sc:battle] T${record.turn} PV: ${pv} | alts: ${alts}`);
      }
    }

    async function requestAnalysis(payload: any, record: DecisionRecord | null) {
      if (abortCtrl) abortCtrl.abort();
      abortCtrl = new AbortController();
      const myCtrl = abortCtrl;
      hdrEl.textContent = 'Copilot — analyzing…';
      try {
        const resp = await fetch(ENGINE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: myCtrl.signal,
        });
        if (!resp.ok || !resp.body) {
          hdrEl.textContent = `Copilot — HTTP ${resp.status}`;
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              handleEngineUpdate(JSON.parse(line), record);
            } catch {}
          }
        }
        if (buffer.trim()) {
          try { handleEngineUpdate(JSON.parse(buffer), record); } catch {}
        }
        hdrEl.textContent = 'Copilot — ready';
      } catch (e: any) {
        if (e.name === 'AbortError') return;
        hdrEl.textContent = 'Copilot — error (engine down?)';
        bestEl.textContent = e.message || 'fetch failed';
      } finally {
        if (abortCtrl === myCtrl) abortCtrl = null;
      }
    }

    // ---- main loop -------------------------------------------------------
    let lastKey: string | null = null;
    let debugLogOnce = false;
    // Debug: log only on transitions so console isn't spammed at 2Hz.
    let lastBranch: string | null = null;
    let lastReqSig: string | null = null;
    const trace = (branch: string, extra?: Record<string, unknown>) => {
      if (branch === lastBranch) return;
      lastBranch = branch;
      console.log(`[sc:trace] ${branch}`, extra ?? {});
    };
    // Expose a live probe so the user can run `window.__scDebug()` in
    // DevTools and see exactly what the loop sees on demand.
    (win as any).__scDebug = () => {
      const rooms = win.app?.rooms;
      const cur = win.app?.curRoom;
      const br = cur?.battle ? cur : null;
      const b = br?.battle;
      const req = br?.request || b?.request;
      return {
        lastKey, lastBranch, lastReqSig,
        roomIds: rooms ? Object.keys(rooms) : null,
        curRoomId: cur?.id,
        turn: b?.turn,
        ended: b?.ended,
        myPokemonLen: b?.myPokemon?.length ?? null,
        farSidePokemonLen: b?.farSide?.pokemon?.length ?? null,
        req: req ? {
          rqid: req.rqid, wait: req.wait, teamPreview: req.teamPreview,
          forceSwitch: req.forceSwitch, hasActive: !!req.active,
        } : null,
      };
    };

    (win as any).__scPostMortem = (battleId?: string): BattlePostMortem | null => {
      const cur = (win as any).app?.curRoom;
      const curB = cur?.battle;
      const id = battleId ?? cur?.id ?? null;
      if (!id) return null;
      const raw = localStorage.getItem(`sc:postmortem:${id}`);
      if (raw) {
        try { return JSON.parse(raw) as BattlePostMortem; } catch { return null; }
      }
      // Fallback: re-parse live if this is the current battle and stepQueue is available.
      if (cur?.id === id && curB?.stepQueue) {
        const battleRecords = scHistory.filter(r => r.battleId === id);
        const mySideId = (curB.mySide?.sideid || curB.mySide?.id || 'p1') as 'p1' | 'p2';
        return parseBattlePostMortem(
          battleRecords as any,
          (curB.stepQueue || []).slice(),
          {
            battleId: id,
            format: curB.tier || 'unknown',
            myUsername: curB.mySide?.name || 'unknown',
            mySideId,
            opponent: curB.farSide?.name || 'unknown',
          },
        );
      }
      return null;
    };

    (win as any).__scPostMortemAll = (): BattlePostMortem[] => {
      const out: BattlePostMortem[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith('sc:postmortem:')) continue;
        try {
          const pm = JSON.parse(localStorage.getItem(k) || '');
          if (!pm || typeof pm.schemaVersion !== 'number' || pm.schemaVersion < 2) continue;
          out.push(pm as BattlePostMortem);
        } catch {}
      }
      out.sort((a, b) => b.endedAtMs - a.endedAtMs);
      return out;
    };

    (win as any).__scPostMortemClear = (battleId?: string): number => {
      if (battleId) {
        const k = `sc:postmortem:${battleId}`;
        const existed = localStorage.getItem(k) != null;
        localStorage.removeItem(k);
        return existed ? 1 : 0;
      }
      const keys: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('sc:postmortem:')) keys.push(k);
      }
      for (const k of keys) localStorage.removeItem(k);
      return keys.length;
    };

    (win as any).__scPostMortemMigrate = (): { cleared: number; kept: number } => {
      let cleared = 0;
      let kept = 0;
      const toRemove: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (!k || !k.startsWith('sc:postmortem:')) continue;
        try {
          const pm = JSON.parse(localStorage.getItem(k) || '');
          if (pm?.schemaVersion !== 2) {
            toRemove.push(k);
            cleared++;
          } else {
            kept++;
          }
        } catch {
          toRemove.push(k);
          cleared++;
        }
      }
      for (const k of toRemove) localStorage.removeItem(k);
      console.log(`[sc:postmortem] migrate cleared=${cleared} kept=${kept}`);
      return { cleared, kept };
    };

    setInterval(() => {
      const rooms = win.app?.rooms;
      if (!rooms) { trace('no-rooms'); return; }
      // Dump post-mortems for any ended battle rooms we haven't dumped yet.
      // Must run before the room-selection below, which filters out ended
      // battles — otherwise ended battles are never observed by this loop.
      for (const [roomId, room] of Object.entries(rooms)) {
        const eb = (room as any)?.battle;
        if (!eb?.ended || !roomId.startsWith('battle-') || dumpedBattleIds.has(roomId)) continue;
        try {
          const battleRecords = scHistory.filter(r => r.battleId === roomId);
          const mySideId = (eb.mySide?.sideid || eb.mySide?.id || 'p1') as 'p1' | 'p2';
          const pm = parseBattlePostMortem(
            battleRecords as any,
            (eb.stepQueue || []).slice(),
            {
              battleId: roomId,
              format: eb.tier || 'unknown',
              myUsername: eb.mySide?.name || 'unknown',
              mySideId,
              opponent: eb.farSide?.name || 'unknown',
            },
          );
          persistPostMortem(pm);
          dumpedBattleIds.add(roomId);
          console.log(`[sc:postmortem] dumped ${pm.turns.length} turns for ${roomId}`);
        } catch (e) {
          console.error('[sc:postmortem] parse/dump failed', e);
        }
      }
      // Prefer app.curRoom — that's the battle the user is actually viewing
      const cur = win.app.curRoom;
      let br: any = null;
      if (cur?.battle && !cur.battle.ended) {
        br = cur;
      } else {
        br = Object.values(rooms).find(
          (r: any) => r?.battle && !r.battle.ended && (r.battle.turn || 0) >= 1
        );
        if (!br) {
          br = Object.values(rooms).find((r: any) => r?.battle && !r.battle.ended);
        }
      }
      if (!br && !debugLogOnce) {
        debugLogOnce = true;
        console.log(
          '[sc] rooms snapshot:',
          Object.keys(rooms).map((k) => {
            const r = (rooms as any)[k];
            return { id: k, hasBattle: !!r?.battle, ended: r?.battle?.ended, turn: r?.battle?.turn };
          })
        );
      }
      if (!br) {
        if (lastKey) {
          hdrEl.textContent = 'Copilot — idle (no active battle)';
          lastKey = null;
        }
        trace('no-battle-room');
        return;
      }
      const b = br.battle;
      const t = b.turn || 0;
      // Annotation state — kept in sync so the keyboard handler always knows
      // current battleId + turn. Sync the per-battle note UI on battle switch.
      annotationState.battleId = br.id || null;
      annotationState.turn = t;
      syncBattleNoteUi();
      // Detect battle ending so we can log an end-of-battle summary exactly once.
      if (b.ended && lastEndedBattleId !== br.id) {
        lastEndedBattleId = br.id;
        const myName = b.mySide?.name || 'you';
        const oppName = b.farSide?.name || 'opp';
        const winner = b.winner || null;
        const result: BattleResult = {
          battleId: br.id, winner: winner || undefined,
          turns: t, endedAtMs: Date.now(),
        };
        scResults.push(result);
        console.log(
          `[sc:battle] END ${br.id} — ${winner
            ? (winner === myName ? 'WIN' : winner === oppName ? 'LOSS' : `winner=${winner}`)
            : 'draw/unknown'} in ${t} turns`
        );
      }
      // Showdown stores the current decision request on the room, not on
      // the battle object. b.request is usually null; br.request is the
      // real source of truth for team preview / move select / force switch.
      const req = br.request || b.request;
      const rqid = req?.rqid ?? 0;
      const key = `${br.id}:${t}:${rqid}`;
      // Log every distinct (rqid, wait, teamPreview, forceSwitch) tuple we see
      // so we can tell if Showdown ever emits a wait-request with the same
      // rqid as the real move-select (the prime suspect for turn 1 skipping).
      const reqSig = req
        ? `rqid=${req.rqid} wait=${!!req.wait} tp=${!!req.teamPreview} fs=${!!req.forceSwitch} t=${t}`
        : `no-req t=${t}`;
      if (reqSig !== lastReqSig) {
        lastReqSig = reqSig;
        console.log('[sc:req]', reqSig, { key, lastKey });
      }
      if (key === lastKey) { trace(`cache-skip key=${key}`); return; }

      // Team Preview: lead matrix first, type-heuristic as fallback (no engine call)
      if (req?.teamPreview) {
        const myTeam = b.myPokemon || [];
        const oppTeam = b.farSide?.pokemon || [];
        if (myTeam.length && oppTeam.length >= 1) {
          const ranked = myTeam
            .map((m: any) => ({
              name: m.speciesForme || m.species,
              score: leadScore(m, oppTeam),
            }))
            .sort((a: any, b: any) => b.score - a.score);
          const matrix = leadMatrixRecommendation(myTeam, oppTeam);
          hdrEl.textContent = 'Copilot — team preview';
          if (matrix) {
            // Display name in mega form when leading Diancie (the matrix's
            // suggested form) — base form is what's in BattleRequest, but
            // the user sees "Diancie-Mega" in the team builder.
            const display = matrix.lead === 'Diancie' ? 'Diancie-Mega' : matrix.lead;
            // Sticky line — survives subsequent turn renders so the user can
            // refer back to the lead-matrix call mid-battle. Cleared on
            // battle change via the post-match deinit (see scHistory reset).
            leadMatrixEl.textContent = `Lead matrix: → ${display} · opp = ${matrix.archetype.replace(/_/g, ' ')} · ${matrix.reason}`;
            leadMatrixEl.style.display = 'block';
            console.log('[sc:lead-matrix]', {
              archetype: matrix.archetype, lead: display, reason: matrix.reason,
              oppTeam: oppTeam.map((p: any) => p.species?.name || p.speciesForme || p.species),
            });
            bestEl.textContent = `→ ${display}`;
            statsEl.textContent = `opp archetype: ${matrix.archetype.replace(/_/g, ' ')} · matrix lead`;
            pvEl.textContent = `Why: ${matrix.reason}`;
            altsEl.textContent = 'heuristic ranks: ' + ranked
              .slice(0, 3)
              .map((r: any) => `${r.name} (${r.score.toFixed(1)})`)
              .join(' | ');
          } else {
            // No archetype match — show heuristic, hide sticky matrix line.
            leadMatrixEl.style.display = 'none';
            console.log('[sc:lead-matrix]', {
              archetype: 'no-match',
              oppTeam: oppTeam.map((p: any) => p.species?.name || p.speciesForme || p.species),
            });
            const best = ranked[0];
            bestEl.textContent = `→ ${best.name}`;
            statsEl.textContent = `matchup score ${best.score.toFixed(1)} across ${oppTeam.length} opps · type heuristic`;
            pvEl.textContent = 'PV: heuristic (no archetype match)';
            altsEl.textContent = ranked
              .slice(1, 4)
              .map((r: any) => `${r.name} (${r.score.toFixed(1)})`)
              .join(' | ');
          }
          lastKey = key; // done — opp data was ready
        } else {
          // Not ready: opp preview not loaded yet. DON'T cache — retry next poll.
          hdrEl.textContent = 'Copilot — team preview';
          bestEl.textContent = 'waiting for opponent preview…';
          statsEl.textContent = `my team: ${myTeam.length}, opp team: ${oppTeam.length}`;
        }
        return;
      }

      const pendingDecision = !!req && !req.wait;
      if (!pendingDecision) {
        // Not a decision point (mid-animation, wait, etc.) — update header
        // so user sees we're tracking. DO NOT cache lastKey here: Showdown
        // emits a wait-request and then clears `wait` on the SAME rqid, so
        // caching makes the next poll (which IS a real decision) silent-skip
        // forever via the key===lastKey branch. Re-enter every poll; the
        // text updates are idempotent.
        hdrEl.textContent = `Copilot — watching (turn ${t})`;
        if (!statsEl.textContent.startsWith('sims ')) {
          statsEl.textContent = 'Waiting for your next decision…';
        }
        trace(`wait-req (not cached) key=${key}`, { reqSig });
        return;
      }
      if (!b.myPokemon?.length) {
        trace(`no-mypokemon key=${key}`);
        return;
      }

      try {
        const payload: any = translate(b, req);
        payload._planH = buildPlanHMeta(b, br);
        // Guard: refuse to POST if active opp's types didn't resolve. Empty
        // types silently downgrade to Typeless on the engine side, which
        // broke immunity checks (observed 2026-04-20: Togekiss with types=[]
        // → engine picked Earthquake on Flying). Retry next poll instead;
        // the Dex usually populates within 1-2 ticks after a switch.
        const oppActive = payload.sideTwo.pokemon[payload.sideTwo.activeIndex];
        if (oppActive && oppActive.species !== 'none' &&
            (!oppActive.types || oppActive.types.length === 0)) {
          trace(`opp-types-unresolved key=${key}`, { species: oppActive.species });
          hdrEl.textContent = 'Copilot — waiting for opp types…';
          return; // don't cache lastKey — retry next poll
        }
        const tag = req?.forceSwitch ? `force-switch (t${t})` : `turn ${t}`;
        statsEl.textContent = `decision: ${tag} — requesting…`;
        const record: DecisionRecord = {
          battleId: br.id,
          turn: t, rqid,
          tStartMs: Date.now(),
          forceSwitch: !!req?.forceSwitch,
          state: snapshotState(b),
          payload,
          updates: [],
        };
        scHistory.push(record);
        const hazMy = Object.entries(record.state.my.sideConditions || {})
          .filter(([_, v]: any) => v).map(([k]) => k).join(',') || 'none';
        const hazOpp = Object.entries(record.state.opp.sideConditions || {})
          .filter(([_, v]: any) => v).map(([k]) => k).join(',') || 'none';
        console.log(
          `[sc:battle] T${t} PRE (${tag}) — ${record.state.myActive} ` +
          `${record.state.my.activeHpPct}% vs ${record.state.oppActive} ${record.state.opp.activeHpPct}% ` +
          `| weather=${record.state.weather} | hazards my=${hazMy} opp=${hazOpp}`
        );
        console.log('[sc] firing analysis', { turn: t, rqid, forceSwitch: !!req?.forceSwitch });
        requestAnalysis(payload, record);
        lastKey = key;
      } catch (e: any) {
        console.error('[sc] translate error', e);
        hdrEl.textContent = 'Copilot — translate error';
        bestEl.textContent = e.message || 'bad state';
        lastKey = key;
      }
    }, POLL_MS);

    console.log('[Showdown Copilot] extension loaded — world=MAIN, fetch-based');
  },
});
