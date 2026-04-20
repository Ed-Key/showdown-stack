// ==UserScript==
// @name         Showdown Copilot
// @namespace    https://github.com/Ed-Key/showdown-copilot
// @version      0.1.0
// @description  Live MCTS advice panel for Pokémon Showdown battles via local poke-engine /analyze/stream
// @author       Ed-Key
// @match        https://play.pokemonshowdown.com/*
// @connect      localhost
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // Showdown's `app` / `Dex` live on the page's window, not Tampermonkey's
  // sandboxed window. unsafeWindow exposes the real page window.
  const pageWin = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;

  const ENGINE_URL = 'http://localhost:7267/analyze/stream';
  const POLL_MS = 500;
  const ANALYSIS_TIME_MS = 6000;
  const UPDATE_INTERVAL_MS = 400;

  // Minimal type table. Unknown species fall back to []; engine still works
  // because it has its own base-stats table keyed by species name.
  const TYPES = {
    delphox: ['Fire', 'Psychic'], stonjourner: ['Rock'],
    hatterene: ['Psychic', 'Fairy'], blissey: ['Normal'],
    froslass: ['Ice', 'Ghost'], swanna: ['Water', 'Flying'],
    mukalola: ['Poison', 'Dark'], manaphy: ['Water'],
    emboar: ['Fire', 'Fighting'], azelf: ['Psychic'],
    luxray: ['Electric'], staraptor: ['Normal', 'Flying'],
    dudunsparcethreesegment: ['Normal'], dudunsparce: ['Normal'],
    overqwil: ['Dark', 'Poison'], decidueyehisui: ['Grass', 'Fighting'],
    terrakion: ['Rock', 'Fighting'], zacian: ['Fairy'],
    garchomp: ['Dragon', 'Ground'], kingambit: ['Dark', 'Steel'],
    gholdengo: ['Steel', 'Ghost'], greattusk: ['Ground', 'Fighting'],
    ironvaliant: ['Fairy', 'Fighting'], roaringmoon: ['Dragon', 'Dark'],
    dragapult: ['Dragon', 'Ghost'], tinglu: ['Dark', 'Ground'],
    chienpao: ['Dark', 'Ice'], ironmoth: ['Fire', 'Poison'],
    landorustherian: ['Ground', 'Flying'], cinderace: ['Fire'],
    corviknight: ['Flying', 'Steel'], toxapex: ['Poison', 'Water'],
    ferrothorn: ['Grass', 'Steel'], clefable: ['Fairy'],
    heatran: ['Fire', 'Steel'], skeledirge: ['Fire', 'Ghost'],
    slowkinggalar: ['Poison', 'Psychic'],
    breloom: ['Grass', 'Fighting'], gallade: ['Psychic', 'Fighting'],
    infernape: ['Fire', 'Fighting'], hawlucha: ['Fighting', 'Flying'],
    blaziken: ['Fire', 'Fighting'],
  };

  const DEFAULT_SC = {
    auroraVeil: 0, craftyShield: 0, healingWish: 0, lightScreen: 0,
    luckyChant: 0, lunarDance: 0, matBlock: 0, mist: 0,
    protect: 0, quickGuard: 0, reflect: 0, safeguard: 0,
    spikes: 0, stealthRock: 0, stickyWeb: 0, tailwind: 0,
    toxicCount: 0, toxicSpikes: 0, wideGuard: 0,
  };

  const STATUS = {
    brn: 'Burn', frz: 'Freeze', par: 'Paralyze',
    psn: 'Poison', slp: 'Sleep', tox: 'Toxic',
  };

  function norm(s) {
    return (s || '').toString().toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  // Gen 9 type chart — entries where attack does non-1x damage. Omitted
  // entries default to 1x. Used for lead-selection heuristic at team preview.
  const TYPE_CHART = {
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

  function typeMult(atkType, defTypes) {
    let m = 1;
    for (const dt of defTypes || []) {
      const eff = TYPE_CHART[atkType] && TYPE_CHART[atkType][dt];
      if (eff !== undefined) m *= eff;
    }
    return m;
  }

  // Score: how good is `myMon` as a lead against `oppTeam`?
  // For each opp mon: (my best STAB effectiveness vs them) - (their best STAB vs me).
  // Summed across all 6 opp mons. Higher = better lead.
  function leadScore(myMon, oppTeam) {
    const myTypes = resolveTypes(myMon.speciesForme || myMon.species);
    let total = 0;
    for (const opp of oppTeam) {
      const oppName = (opp.species && opp.species.name) || opp.speciesForme || opp.species;
      const oppTypes = resolveTypes(oppName);
      let myBest = 0, theirBest = 0;
      for (const t of myTypes) myBest = Math.max(myBest, typeMult(t, oppTypes));
      for (const t of oppTypes) theirBest = Math.max(theirBest, typeMult(t, myTypes));
      total += myBest - theirBest;
    }
    return total;
  }

  // Compute opponent's likely stats from Showdown's base-stat data. Much
  // better than flat placeholders (atk=200, def=150 etc.) — the engine's
  // damage calculations need real numbers to give meaningful advice.
  //
  // Assumption: opponent is running a standard offensive spread (252 EVs in
  // their better attacking stat + 252 Spe, neutral nature, 31 IVs). Not
  // perfect — a defensive mon with 252 HP/Def would be wrong — but this
  // delta vs. reality is way smaller than placeholders vs. reality.
  //
  // Formula (gen 3+):
  //   HP    = floor((2*base + IV + EV/4) * L / 100) + L + 10
  //   Other = floor(floor((2*base + IV + EV/4) * L / 100) + 5) * natureMult
  function computeOpponentStats(speciesName, level) {
    const fallback = {
      maxhp: 250,
      stats: { atk: 200, def: 150, spa: 200, spd: 150, spe: 180 },
      ability: 'none',
    };
    try {
      const sp = pageWin.Dex && pageWin.Dex.species
        ? pageWin.Dex.species.get(speciesName) : null;
      if (!sp || !sp.baseStats) return fallback;
      const bs = sp.baseStats;
      const isSpecial = (bs.spa || 0) > (bs.atk || 0);
      const atkEV = isSpecial ? 0 : 252;
      const spaEV = isSpecial ? 252 : 0;
      const spe252 = 252;
      const statCore = (base, ev) =>
        Math.floor((2 * base + 31 + Math.floor(ev / 4)) * level / 100);
      return {
        maxhp: statCore(bs.hp, 0) + level + 10,
        stats: {
          atk: statCore(bs.atk, atkEV) + 5,
          def: statCore(bs.def, 0) + 5,
          spa: statCore(bs.spa, spaEV) + 5,
          spd: statCore(bs.spd, 0) + 5,
          spe: statCore(bs.spe, spe252) + 5,
        },
        ability: (sp.abilities && sp.abilities[0])
          ? norm(sp.abilities[0]) : 'none',
      };
    } catch (e) {
      return fallback;
    }
  }

  // Resolve types for any species. Tries the local hardcoded table first
  // (common cases, fast), then falls back to Showdown's own Dex which has
  // every species. Without this, unknown species end up with types=[] and
  // the engine can't know about immunities — e.g., Gliscor (Ground/Flying)
  // looked like a clean Earthquake target because the Flying type was missing.
  function resolveTypes(speciesName) {
    const n = norm(speciesName);
    if (TYPES[n]) return TYPES[n];
    try {
      const dex = pageWin.Dex || (pageWin.BattlePokedex && pageWin);
      if (pageWin.Dex && pageWin.Dex.species) {
        const sp = pageWin.Dex.species.get(speciesName);
        if (sp && sp.types && sp.types.length) return sp.types;
      }
      // Fallback to BattlePokedex (older API surface on play.pokemonshowdown.com)
      if (pageWin.BattlePokedex) {
        const entry = pageWin.BattlePokedex[n] || pageWin.BattlePokedex[speciesName];
        if (entry && entry.types) return entry.types;
      }
    } catch (e) { /* fall through to empty */ }
    return [];
  }

  function padMoves(arr) {
    const m = arr.slice(0, 4);
    while (m.length < 4) m.push({ id: 'none', pp: 0 });
    return m;
  }

  function buildMyPokemon(p) {
    const species = norm(p.speciesForme || p.species);
    return {
      species, level: p.level || 100,
      types: resolveTypes(p.speciesForme || p.species),
      hp: p.hp || 0, maxhp: p.maxhp || 1,
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
      moves: padMoves((p.moves || []).map(m => ({ id: norm(m), pp: 8 }))),
      terastallized: !!p.terastallized,
      teraType: p.teraType || '',
    };
  }

  function buildOppPokemon(p) {
    const speciesRaw = p.speciesForme || (p.species && p.species.name) || p.species;
    const species = norm(speciesRaw);
    const level = p.level || 100;
    const hpPct = p.hp || 0;
    const revealed = (p.moveTrack || []).map(m => ({ id: norm(m[0]), pp: 8 }));
    const computed = computeOpponentStats(speciesRaw, level);
    const maxhp = computed.maxhp;
    // hpPct is 0-100 in Showdown for opp mons; scale to absolute
    const hp = Math.max(0, Math.round(hpPct * maxhp / 100));
    // Prefer revealed ability over inferred default
    const ability = norm(p.ability || p.baseAbility || computed.ability || 'none');
    return {
      species, level,
      types: resolveTypes(speciesRaw),
      hp, maxhp,
      ability,
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
      moves: padMoves(revealed),
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

  function buildSide(mons, activeIdx, boosts) {
    const out = mons.slice();
    while (out.length < 6) out.push(emptyPokemon());
    return {
      pokemon: out.slice(0, 6),
      activeIndex: activeIdx,
      sideConditions: { ...DEFAULT_SC },
      volatileStatuses: [],
      boosts: {
        attack: boosts.atk || 0,
        defense: boosts.def || 0,
        specialAttack: boosts.spa || 0,
        specialDefense: boosts.spd || 0,
        speed: boosts.spe || 0,
      },
      forceTrapped: false,
    };
  }

  function translate(b) {
    const mySide = b.mySide, farSide = b.farSide;
    const myActive = mySide && mySide.active && mySide.active[0];
    let myActiveIdx = 0;
    if (myActive && b.myPokemon) {
      const activeName = norm(myActive.species?.name || myActive.speciesForme);
      myActiveIdx = b.myPokemon.findIndex(p =>
        norm(p.speciesForme || p.species) === activeName);
      if (myActiveIdx < 0) myActiveIdx = 0;
    }
    const myMons = (b.myPokemon || []).map(buildMyPokemon);
    const oppMons = (farSide?.pokemon || []).map(buildOppPokemon);
    const oppActive = farSide?.active?.[0];
    let oppActiveIdx = 0;
    if (oppActive && farSide?.pokemon) {
      oppActiveIdx = farSide.pokemon.findIndex(p => p === oppActive);
      if (oppActiveIdx < 0) oppActiveIdx = 0;
    }
    const weather = (b.weather || '').toLowerCase();
    return {
      sideOne: buildSide(myMons, myActiveIdx, myActive?.boosts || {}),
      sideTwo: buildSide(oppMons, oppActiveIdx, oppActive?.boosts || {}),
      weather: {
        weatherType: weather || 'none',
        turnsRemaining: b.weatherTimeLeft || -1,
      },
      terrain: { terrainType: 'none', turnsRemaining: -1 },
      trickRoom: (b.pseudoWeather || []).some(pw => pw[0] === 'trickroom'),
      timeLimitMs: ANALYSIS_TIME_MS,
      updateIntervalMs: UPDATE_INTERVAL_MS,
    };
  }

  // UI ----------------------------------------------------------------
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

  const hdrEl = panel.querySelector('.sc-header');
  const bestEl = panel.querySelector('.sc-best');
  const statsEl = panel.querySelector('.sc-stats');
  const pvEl = panel.querySelector('.sc-pv');
  const altsEl = panel.querySelector('.sc-alts');

  // Detect if a "move" string is actually a switch target (species name).
  // Engine outputs switches with the species name in ALL_CAPS_NO_SEP,
  // e.g. KELDEORESOLUTE means "switch to Keldeo-Resolute". We match against
  // my current team's species to decide.
  function labelMove(moveStr) {
    if (!moveStr) return '—';
    const n = moveStr.toLowerCase().replace(/[^a-z0-9]/g, '');
    const team = (pageWin.app?.curRoom?.battle?.myPokemon || []);
    for (const p of team) {
      const s = (p.speciesForme || p.species || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      if (s && s === n) {
        return `→ ${p.speciesForme || p.species}`;
      }
    }
    // Regular move: just lowercase-pretty
    return moveStr.toLowerCase();
  }

  function renderUpdate(u) {
    const arrow = u.event === 'final' ? '▲' : '•';
    const conf = ((u.confidence || 0) * 100).toFixed(0);
    bestEl.textContent = `${labelMove(u.bestMove)}  ${arrow} ${conf}%`;
    statsEl.textContent =
      `sims ${(u.sims || 0).toLocaleString()}  depth ${u.depth || 0}` +
      (u.error ? `  ERROR: ${u.error}` : '');
    pvEl.textContent = `PV: ${(u.pv || []).join(' → ') || '—'}`;
    altsEl.textContent = (u.alternatives || [])
      .slice(0, 3)
      .map(a => `${labelMove(a.move)} ${((a.confidence || 0) * 100).toFixed(0)}%`)
      .join(' | ') || '—';
  }

  // Engine call -------------------------------------------------------
  let inFlight = null;

  function requestAnalysis(payload) {
    if (inFlight && inFlight.abort) {
      try { inFlight.abort(); } catch (e) { /* ignore */ }
    }
    hdrEl.textContent = 'Copilot — analyzing…';
    let lastLen = 0;
    inFlight = GM_xmlhttpRequest({
      method: 'POST',
      url: ENGINE_URL,
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify(payload),
      onprogress: (resp) => {
        const text = resp.responseText || '';
        if (text.length <= lastLen) return;
        const chunk = text.slice(lastLen);
        lastLen = text.length;
        chunk.split('\n').forEach(line => {
          if (!line.trim()) return;
          try {
            const u = JSON.parse(line);
            renderUpdate(u);
          } catch (e) { /* partial line — ignore */ }
        });
      },
      onload: (resp) => {
        // GM_xmlhttpRequest in Tampermonkey MV3 sometimes delivers the full
        // body at once via onload instead of incrementally via onprogress.
        // Parse here too — lastLen ensures we don't double-render.
        const text = resp.responseText || '';
        if (text.length > lastLen) {
          const chunk = text.slice(lastLen);
          lastLen = text.length;
          chunk.split('\n').forEach(line => {
            if (!line.trim()) return;
            try {
              const u = JSON.parse(line);
              renderUpdate(u);
            } catch (e) { /* ignore */ }
          });
        }
        hdrEl.textContent = 'Copilot — ready';
        inFlight = null;
      },
      onerror: () => {
        hdrEl.textContent = 'Copilot — error (engine down?)';
        bestEl.textContent = 'engine unreachable';
        inFlight = null;
      },
      ontimeout: () => {
        hdrEl.textContent = 'Copilot — timeout';
        inFlight = null;
      },
      timeout: ANALYSIS_TIME_MS + 5000,
    });
  }

  // Main loop ---------------------------------------------------------
  // Trigger key combines room + turn + request-id (rqid). Showdown increments
  // rqid on every decision prompt — move-select AND force-switch after faint —
  // so we re-analyze whenever you're asked to decide, not just on new turns.
  let lastKey = null;

  let debugLogOnce = false;
  setInterval(() => {
    const rooms = pageWin.app && pageWin.app.rooms;
    if (!rooms) return;
    // Prefer curRoom if it's a battle — more accurate than iteration order.
    // Fall back to finding any non-ended battle room.
    const cur = pageWin.app.curRoom;
    let br = null;
    if (cur && cur.battle && !cur.battle.ended) {
      br = cur;
    } else {
      br = Object.values(rooms).find(r => r && r.battle && !r.battle.ended && (r.battle.turn || 0) >= 1);
      // If still nothing, fall back to any non-ended battle (for pre-turn states)
      if (!br) {
        br = Object.values(rooms).find(r => r && r.battle && !r.battle.ended);
      }
    }
    // One-shot debug when we first miss detection — helps diagnose future bugs
    if (!br && !debugLogOnce) {
      debugLogOnce = true;
      console.log('[sc] rooms snapshot:', Object.keys(rooms).map(k => {
        const r = rooms[k];
        return { id: k, hasBattle: !!r?.battle, ended: r?.battle?.ended, turn: r?.battle?.turn };
      }));
    }
    if (!br) {
      if (lastKey) {
        hdrEl.textContent = 'Copilot — idle (no active battle)';
        lastKey = null;
      }
      return;
    }
    const b = br.battle;
    const t = b.turn || 0;
    const rqid = (b.request && b.request.rqid) || 0;
    const key = `${br.id}:${t}:${rqid}`;
    if (key === lastKey) return;
    lastKey = key;
    // Only analyze when there's actually a decision to make. After you submit
    // a move, b.request gets cleared / rqid bumps — we'd otherwise fire a
    // redundant analysis and clobber the real stats display with "requesting…".
    const pendingDecision = !!b.request && !b.request.wait;
    if (!pendingDecision) {
      // We're in a battle but no decision is pending. Clear stale "idle"
      // header so the user knows the script sees the battle. Keep any
      // previous analysis result (bestEl/pvEl/altsEl) on screen — don't
      // overwrite with placeholder data.
      const cur = hdrEl.textContent || '';
      if (cur.includes('idle')) {
        hdrEl.textContent = `Copilot — watching (turn ${t})`;
        statsEl.textContent = 'Waiting for your next decision…';
      }
      return;
    }
    if (!b.myPokemon || !b.myPokemon.length) return;
    // Team Preview: engine can't run MCTS here (no "active" pokemon yet).
    // Use a type-matchup heuristic across all 6 opp mons to rank our 6 leads.
    // Fast (no engine call), reasonable quality, beats no advice.
    if (b.request && b.request.teamPreview) {
      const myTeam = b.myPokemon || [];
      const oppTeam = (br.battle.farSide && br.battle.farSide.pokemon) || [];
      if (myTeam.length && oppTeam.length) {
        const ranked = myTeam
          .map(m => ({
            name: m.speciesForme || m.species,
            score: leadScore(m, oppTeam),
          }))
          .sort((a, b) => b.score - a.score);
        const best = ranked[0];
        hdrEl.textContent = 'Copilot — team preview';
        bestEl.textContent = `→ ${best.name}`;
        statsEl.textContent = `matchup score ${best.score.toFixed(1)} across 6 opps`;
        pvEl.textContent = `PV: heuristic (not MCTS)`;
        altsEl.textContent = ranked
          .slice(1, 4)
          .map(r => `${r.name} (${r.score.toFixed(1)})`)
          .join(' | ');
      } else {
        hdrEl.textContent = 'Copilot — team preview';
        bestEl.textContent = 'waiting for opponent preview';
        statsEl.textContent = '';
      }
      return;
    }
    try {
      const payload = translate(b);
      // Tag the analysis so TUI shows what we're deciding for
      const tag = b.request?.forceSwitch ? `force-switch (t${t})` : `turn ${t}`;
      statsEl.textContent = `decision: ${tag} — requesting…`;
      requestAnalysis(payload);
    } catch (e) {
      console.error('[sc] translate error', e);
      hdrEl.textContent = 'Copilot — translate error';
      bestEl.textContent = e.message || 'bad state';
    }
  }, POLL_MS);

  console.log('[Showdown Copilot] userscript loaded — waiting for battle');
})();
