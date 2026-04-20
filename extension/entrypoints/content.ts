// Showdown Copilot — content script running in MAIN world (page context).
// Reads Showdown's app / Dex globals directly, fetches the local poke-engine
// /analyze/stream endpoint, streams NDJSON updates into a floating panel.

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

    function resolveTypes(speciesName: string): string[] {
      try {
        if (win.Dex && win.Dex.species) {
          const sp = win.Dex.species.get(speciesName);
          if (sp?.types?.length) return sp.types;
        }
        if (win.BattlePokedex) {
          const entry = win.BattlePokedex[norm(speciesName)] || win.BattlePokedex[speciesName];
          if (entry?.types) return entry.types;
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

    function buildSide(mons: any[], activeIdx: number, boosts: any) {
      const out = mons.slice();
      while (out.length < 6) out.push(emptyPokemon());
      return {
        pokemon: out.slice(0, 6),
        activeIndex: activeIdx,
        sideConditions: { ...DEFAULT_SC },
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
        sideOne: buildSide(myMons, myActiveIdx, myActive?.boosts),
        sideTwo: buildSide(oppMons, oppActiveIdx, oppActive?.boosts),
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

    async function requestAnalysis(payload: any) {
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
              renderUpdate(JSON.parse(line));
            } catch {}
          }
        }
        if (buffer.trim()) {
          try { renderUpdate(JSON.parse(buffer)); } catch {}
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

    setInterval(() => {
      const rooms = win.app?.rooms;
      if (!rooms) return;
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
        return;
      }
      const b = br.battle;
      const t = b.turn || 0;
      const rqid = b.request?.rqid ?? 0;
      const key = `${br.id}:${t}:${rqid}`;
      if (key === lastKey) return;
      lastKey = key;

      // Team Preview: run the fast heuristic (no engine call)
      if (b.request?.teamPreview) {
        const myTeam = b.myPokemon || [];
        const oppTeam = b.farSide?.pokemon || [];
        if (myTeam.length && oppTeam.length) {
          const ranked = myTeam
            .map((m: any) => ({
              name: m.speciesForme || m.species,
              score: leadScore(m, oppTeam),
            }))
            .sort((a: any, b: any) => b.score - a.score);
          const best = ranked[0];
          hdrEl.textContent = 'Copilot — team preview';
          bestEl.textContent = `→ ${best.name}`;
          statsEl.textContent = `matchup score ${best.score.toFixed(1)} across 6 opps`;
          pvEl.textContent = 'PV: heuristic (not MCTS)';
          altsEl.textContent = ranked
            .slice(1, 4)
            .map((r: any) => `${r.name} (${r.score.toFixed(1)})`)
            .join(' | ');
        } else {
          hdrEl.textContent = 'Copilot — team preview';
          bestEl.textContent = 'waiting for opponent preview';
          statsEl.textContent = '';
        }
        return;
      }

      const pendingDecision = !!b.request && !b.request.wait;
      if (!pendingDecision) {
        if (hdrEl.textContent.includes('idle')) {
          hdrEl.textContent = `Copilot — watching (turn ${t})`;
          statsEl.textContent = 'Waiting for your next decision…';
        }
        return;
      }
      if (!b.myPokemon?.length) return;

      try {
        const payload = translate(b);
        const tag = b.request?.forceSwitch ? `force-switch (t${t})` : `turn ${t}`;
        statsEl.textContent = `decision: ${tag} — requesting…`;
        requestAnalysis(payload);
      } catch (e: any) {
        console.error('[sc] translate error', e);
        hdrEl.textContent = 'Copilot — translate error';
        bestEl.textContent = e.message || 'bad state';
      }
    }, POLL_MS);

    console.log('[Showdown Copilot] extension loaded — world=MAIN, fetch-based');
  },
});
