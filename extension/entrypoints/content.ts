// Showdown Copilot — content script running in MAIN world (page context).
// Reads Showdown's app / Dex globals directly, fetches the local poke-engine
// /analyze/stream endpoint, streams NDJSON updates into a floating panel.
import { priorMovesForSpecies } from '../utils/chaos-priors';

export default defineContentScript({
  matches: ['https://play.pokemonshowdown.com/*'],
  runAt: 'document_idle',
  world: 'MAIN',

  main() {
    // page-context globals (declared loose so TS doesn't choke)
    const win: any = window;

    const ENGINE_URL = 'http://localhost:7267/analyze/stream';
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

    // ---- translation: Showdown battle → poke-engine payload --------------
    function buildMyPokemon(p: any) {
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
        moves: padMoves((p.moves || []).map((m: string) => ({ id: norm(m), pp: 8 }))),
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

    function buildSide(mons: any[], activeIdx: number, boosts: any, rawSide: any) {
      const out = mons.slice();
      while (out.length < 6) out.push(emptyPokemon());
      return {
        pokemon: out.slice(0, 6),
        activeIndex: activeIdx,
        sideConditions: translateSideConditions(rawSide?.sideConditions),
        volatileStatuses: [],
        boosts: {
          attack: boosts?.atk || 0,
          defense: boosts?.def || 0,
          specialAttack: boosts?.spa || 0,
          specialDefense: boosts?.spd || 0,
          speed: boosts?.spe || 0,
        },
        forceTrapped: false,
      };
    }

    function translate(b: any) {
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
      const myMons = (b.myPokemon || []).map(buildMyPokemon);
      const oppMons = (farSide?.pokemon || []).map(buildOppPokemon);
      const oppActive = farSide?.active?.[0];
      let oppActiveIdx = 0;
      if (oppActive && farSide?.pokemon) {
        oppActiveIdx = farSide.pokemon.findIndex((p: any) => p === oppActive);
        if (oppActiveIdx < 0) oppActiveIdx = 0;
      }
      const weather = (b.weather || '').toLowerCase();
      return {
        sideOne: buildSide(myMons, myActiveIdx, myActive?.boosts, mySide),
        sideTwo: buildSide(oppMons, oppActiveIdx, oppActive?.boosts, farSide),
        weather: {
          weatherType: weather || 'none',
          turnsRemaining: b.weatherTimeLeft || -1,
        },
        terrain: { terrainType: 'none', turnsRemaining: -1 },
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
      '<div class="sc-best">—</div>',
      '<div class="sc-stats">—</div>',
      '<div class="sc-pv">PV: —</div>',
      '<div class="sc-alts">—</div>',
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
      #sc-panel .sc-best { font-size: 17px; font-weight: bold; color: #7fe; margin: 4px 0; }
      #sc-panel .sc-stats { font-size: 11px; color: #888; margin-bottom: 6px; }
      #sc-panel .sc-pv { font-size: 11px; color: #ddd; margin-bottom: 4px; word-break: break-word; }
      #sc-panel .sc-alts { font-size: 11px; color: #ccc; word-break: break-word; }
    `;
    document.head.appendChild(style);
    document.body.appendChild(panel);

    const hdrEl = panel.querySelector<HTMLDivElement>('.sc-header')!;
    const bestEl = panel.querySelector<HTMLDivElement>('.sc-best')!;
    const statsEl = panel.querySelector<HTMLDivElement>('.sc-stats')!;
    const pvEl = panel.querySelector<HTMLDivElement>('.sc-pv')!;
    const altsEl = panel.querySelector<HTMLDivElement>('.sc-alts')!;

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

    setInterval(() => {
      const rooms = win.app?.rooms;
      if (!rooms) { trace('no-rooms'); return; }
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

      // Team Preview: run the fast heuristic (no engine call)
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
          const best = ranked[0];
          hdrEl.textContent = 'Copilot — team preview';
          bestEl.textContent = `→ ${best.name}`;
          statsEl.textContent = `matchup score ${best.score.toFixed(1)} across ${oppTeam.length} opps`;
          pvEl.textContent = 'PV: heuristic (not MCTS)';
          altsEl.textContent = ranked
            .slice(1, 4)
            .map((r: any) => `${r.name} (${r.score.toFixed(1)})`)
            .join(' | ');
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
        const payload = translate(b);
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
