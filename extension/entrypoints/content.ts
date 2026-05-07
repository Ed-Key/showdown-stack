// Showdown Copilot — content script running in MAIN world (page context).
// Reads Showdown's app / Dex globals directly, fetches the local poke-engine
// /analyze/stream endpoint, streams NDJSON updates into a floating panel.
import { parseBattlePostMortem, type BattlePostMortem } from '../utils/post-mortem';
import {
  norm, padMoves, padMovesWithPriors, resolveTypes, computeOpponentStats,
  buildMyPokemon, buildOppPokemon, emptyPokemon, translateSideConditions,
  extractVolatileStatuses, extractVolatileDurations, computeProtectStreak,
  buildSide, deriveLastUsedMove, lookupMovePriority, applyBotSpeedModifierChain,
  extractTurnMoveOrder, detectWeather, detectTerrain, isTrickRoom,
  buildPlanHMeta, translate,
  TYPE_CHART, DEFAULT_SC, STATUS,
} from '../lib/translate';
import { snapshotState, snapshotSide } from '../lib/snapshot';
import { mountExpandableCard } from '../lib/tabs';
import { fetchBeliefSnapshot } from '../lib/belief-snapshot';
import { buildDamageMatrix, type DamageMatrix } from '../lib/damage-matrix';
import { computeThreats, type ThreatsReport } from '../lib/threats';
import { renderMatrix } from '../panels/matrix';
import { renderThreats } from '../panels/threats';

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

    // ---- UI -------------------------------------------------------------
    const panel = document.createElement('div');
    panel.id = 'sc-panel';
    panel.innerHTML = `
      <div class="sc-pinned">
        <div class="sc-header">Copilot — idle</div>
        <div class="sc-conflict" style="display:none"></div>
        <div class="sc-best">—</div>
        <div class="sc-stats">—</div>
        <div class="sc-pv">PV: —</div>
        <div class="sc-alts">—</div>
        <div class="sc-notes-header" title="Battle note (press N for per-turn notes)">📝 Battle note <span class="sc-notes-toggle">[show]</span></div>
        <div class="sc-notes-body" style="display:none"><textarea class="sc-battle-note" placeholder="Free-form notes for this battle..." spellcheck="false"></textarea></div>
      </div>
      <div class="sc-cards"></div>
    `;

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
      #sc-panel .sc-pinned {
        border-bottom: 1px solid #333;
        padding-bottom: 6px;
        margin-bottom: 6px;
      }
      #sc-panel .sc-cards .sc-card {
        border-top: 1px solid #2a2a2a;
      }
      #sc-panel .sc-card-header {
        cursor: pointer;
        user-select: none;
        font-size: 11px;
        padding: 4px 0;
        display: flex;
        justify-content: space-between;
      }
      #sc-panel .sc-card-toggle {
        color: #888;
      }
      #sc-panel .sc-conflict {
        background: #5a1f1f;
        color: #ffd0d0;
        padding: 4px 6px;
        border-radius: 3px;
        margin: 4px 0;
        font-size: 11px;
      }
      #sc-panel .sc-conflict.warn {
        background: #5a4a1f;
        color: #fff0c0;
      }
      #sc-panel .sc-conflict.info {
        background: #2a2a2a;
        color: #aaa;
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
      #sc-panel .sc-matrix-legend {
        font-size: 10px; color: #888; margin: 4px 0 6px 0;
        line-height: 1.7;
      }
      #sc-panel .sc-matrix-legend-chip {
        display: inline-block; padding: 1px 6px; margin-right: 4px;
        border: 1px solid #2a2a2a; border-radius: 2px;
        font-size: 9px; color: #ccc;
      }
      #sc-panel .sc-matrix-scroll {
        overflow-x: auto; max-width: 100%;
      }
      #sc-panel .sc-matrix { border-collapse: collapse; font-size: 10px; }
      #sc-panel .sc-matrix th, #sc-panel .sc-matrix td {
        padding: 2px 4px; border: 1px solid #2a2a2a; text-align: center;
        white-space: nowrap;
      }
      #sc-panel .sc-matrix .sc-row-label { text-align: left; color: #aaa; }
      /* Damage-tier color tokens — applied to BOTH matrix cells AND legend chips
         so the chip swatches match the cell colors users see in the table. */
      #sc-panel .sc-immune { color: #666; }
      #sc-panel .sc-warn { background: #4a3a1f; color: #ffd9a8; }
      #sc-panel .sc-2hko { background: #5a3a1f; color: #ffd9a8; }
      #sc-panel .sc-ohko { background: #5a1f1f; color: #fff; font-weight: bold; }
      #sc-panel .sc-matrix-empty { color: #888; font-style: italic; padding: 4px; }
      #sc-panel .sc-threats-header { font-size: 11px; color: #aaa; padding: 2px 0; }
      #sc-panel .sc-threats-section { font-size: 11px; padding: 4px 0; border-top: 1px dotted #2a2a2a; }
      #sc-panel .sc-threat-row { padding: 1px 0; color: #ddd; }
      #sc-panel .sc-conf { color: #888; font-size: 10px; }
      #sc-panel .sc-empty { color: #666; font-size: 10px; padding: 2px 0; }
      #sc-panel .sc-tp-row {
        display: flex; justify-content: space-between;
        font-size: 11px; padding: 1px 0;
      }
      #sc-panel .sc-tp-name { color: #ddd; flex: 1; }
      #sc-panel .sc-tp-stat {
        color: #aaa; font-size: 10px;
        margin-left: 8px; white-space: nowrap;
      }
    `;
    document.head.appendChild(style);
    document.body.appendChild(panel);

    const hdrEl = panel.querySelector<HTMLDivElement>('.sc-header')!;
    const conflictEl = panel.querySelector<HTMLDivElement>('.sc-conflict')!;
    const bestEl = panel.querySelector<HTMLDivElement>('.sc-best')!;
    const statsEl = panel.querySelector<HTMLDivElement>('.sc-stats')!;
    const pvEl = panel.querySelector<HTMLDivElement>('.sc-pv')!;
    const altsEl = panel.querySelector<HTMLDivElement>('.sc-alts')!;
    const cardsRoot = panel.querySelector<HTMLDivElement>('.sc-cards')!;
    const notesHeaderEl = panel.querySelector<HTMLDivElement>('.sc-notes-header')!;
    const notesBodyEl = panel.querySelector<HTMLDivElement>('.sc-notes-body')!;
    const notesToggleEl = panel.querySelector<HTMLSpanElement>('.sc-notes-toggle')!;
    const battleNoteTextarea = panel.querySelector<HTMLTextAreaElement>('.sc-battle-note')!;

    // ---- Damage matrix card --------------------------------------------
    // Expandable card showing opp→us damage % per (attacker, defender) pair,
    // computed via @smogon/calc with belief-driven move/item/spread choices.
    // Refreshed on toggle-to-expand and on every successful decision render
    // (only when expanded — avoids the calc cost when collapsed).
    const matrixCard = mountExpandableCard(cardsRoot, 'matrix', '⚔ Damage matrix');
    let lastMatrix: DamageMatrix | null = null;

    async function refreshMatrix(b: any, br: any): Promise<void> {
      if (!b || !br?.id) return;
      const myTeam = (b.myPokemon || []).map((p: any) => buildMyPokemon(p, null, win));
      const oppTeam = (b.farSide?.pokemon || []).map((p: any) => buildOppPokemon(p, win));
      if (!myTeam.length || !oppTeam.length) {
        lastMatrix = null;
        renderMatrix(matrixCard.body, null);
        return;
      }
      const snap = await fetchBeliefSnapshot('http://localhost:7271', br.id);
      const beliefByOpp: Record<string, any> = {};
      for (const [species, b2] of Object.entries(snap?.opponents || {})) {
        beliefByOpp[species] = b2;
      }
      // Stage 1: render opp-attacking-mine only — most useful threat view.
      // Stage 1.5 will add the symmetric matrix for team-preview lead pick.
      lastMatrix = buildDamageMatrix({
        attackers: oppTeam, defenders: myTeam,
        beliefByDefender: beliefByOpp,
        field: { weather: detectWeather(b) || '', terrain: detectTerrain(b) || '' },
        attackerSide: 'opp',
      });
      renderMatrix(matrixCard.body, lastMatrix);
    }

    // Refresh when user expands the card (avoids work when collapsed).
    matrixCard.toggleBtn.addEventListener('click', () => {
      if (matrixCard.isExpanded) {
        const room = win.app?.curRoom;
        if (room?.battle && room?.id) refreshMatrix(room.battle, room);
      }
    });

    // ---- Threats card ---------------------------------------------------
    // Surfaces the highest-damage threats from opp's active mon (and bench)
    // against your team, derived from the same DamageMatrix used by the
    // matrix card. Reads `lastMatrix` directly — refreshThreats requires
    // that refreshMatrix has already populated it.
    const threatsCard = mountExpandableCard(cardsRoot, 'threats', '⚠ Threats');
    let lastThreats: ThreatsReport | null = null;

    function refreshThreats(b: any): void {
      if (!b || !lastMatrix) {
        lastThreats = null;
        renderThreats(threatsCard.body, null);
        return;
      }
      const myActive = b.mySide?.active?.[0];
      const oppActive = b.farSide?.active?.[0];
      if (!myActive || !oppActive) {
        lastThreats = null;
        renderThreats(threatsCard.body, null);
        return;
      }
      const myTeam = (b.myPokemon || []).map((p: any) => buildMyPokemon(p, null, win));
      const oppTeam = (b.farSide?.pokemon || []).map((p: any) => buildOppPokemon(p, win));
      lastThreats = computeThreats({
        matrix: lastMatrix,
        myActive: buildMyPokemon(myActive, null, win),
        oppActive: buildOppPokemon(oppActive, win),
        myTeam, oppTeam,
        threshold: { warn: 50, danger: 80 },
      });
      renderThreats(threatsCard.body, lastThreats);
    }

    // Refresh on toggle-to-expand. Threats reads lastMatrix; if it's not
    // populated yet (matrix card never expanded), force a matrix refresh
    // first and chain the threats compute on completion.
    threatsCard.toggleBtn.addEventListener('click', () => {
      if (threatsCard.isExpanded) {
        const room = win.app?.curRoom;
        if (room?.battle && room?.id) {
          refreshMatrix(room.battle, room).then(() => refreshThreats(room.battle));
        }
      }
    });

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
    // a Pokemon. Filter engine response to switches; if engine surfaced no
    // switches in its top-K, show plain-text "manual pick required" guidance
    // (Stage 2 will replace this with damage-matrix-driven recommendation).
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
      // Engine had no switch in top-K. Show plain-text guidance.
      hdrEl.textContent = 'Copilot — force switch (engine returned no switch)';
      bestEl.textContent = '— manual pick required';
      statsEl.textContent = 'engine top-K had only moves; matrix card may help';
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

      // Team Preview: per-mon stats leaderboard (Stage 1.5, Option C).
      // Shows survives N/6 + threatens N/6 for each of my mons in team order
      // (no sorting, no recommendation). Also refreshes the ⚔ matrix card so
      // it doesn't show prior-battle data when the new battle's TP arrives.
      if (req?.teamPreview) {
        const myTeam = b.myPokemon || [];
        const oppTeam = b.farSide?.pokemon || [];
        if (myTeam.length && oppTeam.length >= 1) {
          const mySnaps = myTeam.map((p: any) => buildMyPokemon(p, null, win));
          const oppSnaps = oppTeam.map((p: any) => buildOppPokemon(p, win));

          fetchBeliefSnapshot('http://localhost:7271', br?.id || '').then((snap: any) => {
            const beliefByOpp: Record<string, any> = {};
            for (const [sp, b2] of Object.entries(snap?.opponents || {})) {
              beliefByOpp[sp] = b2;
            }
            const field = {
              weather: detectWeather(b) || '',
              terrain: detectTerrain(b) || '',
            };
            const myAtk = buildDamageMatrix({
              attackers: mySnaps, defenders: oppSnaps,
              beliefByDefender: beliefByOpp,
              field, attackerSide: 'mine',
            });
            const oppAtk = buildDamageMatrix({
              attackers: oppSnaps, defenders: mySnaps,
              beliefByDefender: beliefByOpp,
              field, attackerSide: 'opp',
            });

            const oppCount = oppSnaps.length;
            const rows = mySnaps.map((m: any) => {
              const myCells = myAtk.cells.filter((c: any) => c.attacker === m.species);
              const threatens = new Set(
                myCells.filter((c: any) => c.ohko).map((c: any) => c.defender),
              ).size;
              const oppCellsAgainstMe = oppAtk.cells.filter((c: any) => c.defender === m.species);
              const koMe = new Set(
                oppCellsAgainstMe.filter((c: any) => c.ohko).map((c: any) => c.attacker),
              ).size;
              const survives = oppCount - koMe;
              return { species: m.species, survives, threatens };
            });

            hdrEl.textContent = 'Copilot — team preview';
            bestEl.textContent = 'pick a lead — numbers below';
            statsEl.textContent =
              `${mySnaps.length} candidates · ${oppCount} opps · matrix below for cell detail`;
            pvEl.innerHTML = rows.map((r: any) =>
              `<div class="sc-tp-row"><span class="sc-tp-name">${r.species}</span>` +
              `<span class="sc-tp-stat">survives ${r.survives}/${oppCount}</span>` +
              `<span class="sc-tp-stat">threatens ${r.threatens}/${oppCount}</span></div>`,
            ).join('');
            altsEl.textContent = '';
          }).catch((err: any) => {
            console.warn('[sc:team-preview] leaderboard fetch failed', err);
            hdrEl.textContent = 'Copilot — team preview';
            bestEl.textContent = '—';
            statsEl.textContent = `${myTeam.length} v ${oppTeam.length} (belief fetch failed)`;
          });

          // Refresh the ⚔ matrix card so it tracks the new battle, not the
          // prior one (fixes the stale-from-previous-battle bug). Threats
          // refreshes after matrix completes (fire-and-forget chain) so it
          // sees the new lastMatrix.
          if (matrixCard.isExpanded || threatsCard.isExpanded) {
            refreshMatrix(b, br).then(() => refreshThreats(b));
          }

          lastKey = key;
        } else {
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
        const payload: any = translate(b, req, win);
        // Tuning constants live in this main() closure (caller-owned),
        // so we tack them on after translate() returns.
        payload.timeLimitMs = ANALYSIS_TIME_MS;
        payload.updateIntervalMs = UPDATE_INTERVAL_MS;
        payload._planH = buildPlanHMeta(b, br, win);
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
        if (matrixCard.isExpanded || threatsCard.isExpanded) {
          refreshMatrix(b, br).then(() => refreshThreats(b));
        }
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
