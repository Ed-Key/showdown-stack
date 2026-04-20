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
    const hpPct = p.hp || 0;
    const revealed = (p.moveTrack || []).map(m => ({ id: norm(m[0]), pp: 8 }));
    return {
      species, level: p.level || 100,
      types: resolveTypes(speciesRaw),
      hp: Math.max(1, Math.round(hpPct * 2.5)),
      maxhp: 250,
      ability: norm(p.ability || p.baseAbility || 'none'),
      item: norm(p.item || 'none'),
      nature: 'Serious',
      evs: { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 },
      attack: 200, defense: 150,
      specialAttack: 200, specialDefense: 150, speed: 180,
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

  setInterval(() => {
    const rooms = pageWin.app && pageWin.app.rooms;
    if (!rooms) return;
    const br = Object.values(rooms).find(r => r && r.battle && !r.battle.ended);
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
      // Leave the last-completed analysis visible; don't overwrite
      return;
    }
    if (!b.myPokemon || !b.myPokemon.length) return;
    // Team Preview: engine has no "pick a lead" mode — skip analysis and show
    // a clear message instead of silently failing.
    if (b.request && b.request.teamPreview) {
      hdrEl.textContent = 'Copilot — team preview';
      bestEl.textContent = 'pick a lead manually';
      statsEl.textContent = 'MCTS lead-selection not yet implemented';
      pvEl.textContent = 'PV: —';
      altsEl.textContent = 'Follow-up: Phase 2 feature';
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
