// Showdown Copilot — content script running in MAIN world (page context).
// Reads Showdown's app / Dex globals directly, fetches the local poke-engine
// /analyze/stream endpoint, streams NDJSON updates into a floating panel.
import { parseBattlePostMortem, type BattlePostMortem } from '../utils/post-mortem';
import {
  norm,
  buildMyPokemon, buildOppPokemon,
  detectWeather, detectTerrain,
  buildPlanHMeta, translate,
} from '../lib/translate';
import { snapshotState } from '../lib/snapshot';
import { fetchBeliefSnapshot, type BeliefSnapshot } from '../lib/belief-snapshot';
import { buildDamageMatrix, type DamageMatrix } from '../lib/damage-matrix';
import { computeThreats, type ThreatsReport } from '../lib/threats';
import { detectConflict, type ConflictWarning } from '../lib/conflict';
import { fetchExplanation } from '../lib/explainer';
import {
  appendVal, computeTrend, formatTrendArrow, formatTrendTitle, isDesperate,
  renderSparkline, computeTrendArrow,
  type Trend,
} from '../lib/val-trend';
import { renderTcgCard, type TcgCardProps, type AlternativeMove } from '../panels/tcg-card';
import { getMoveType, getPokemonPrimaryType, isSwitchOption } from '../lib/showdown-dex';
import { renderConflictBanner, type ConflictBannerProps, type ConflictSeverity } from '../panels/conflict-banner';
import { renderThreatsPanel, type ThreatRow as PanelThreatRow } from '../panels/threats-panel';
import { renderPvChain, type PvStep } from '../panels/pv-chain';
import { mountOrReplace } from '../lib/panel-mount';
import { escapeHtml } from '../panels/_shared';
import {
  PROXY_BASE_URL, ENGINE_STREAM_URL, PROXY_ANNOTATION_URL, PROXY_POSTMORTEM_URL,
  POLL_MS, ANALYSIS_TIME_MS, UPDATE_INTERVAL_MS,
} from '../config';
import '../styles/tcg.css';
import '../styles/panel-chrome.css';

export default defineContentScript({
  matches: ['https://play.pokemonshowdown.com/*'],
  runAt: 'document_idle',
  world: 'MAIN',

  main() {
    // page-context globals (declared loose so TS doesn't choke)
    const win: any = window;

    // Inject the Google Fonts referenced by styles/tcg.css. Without this the
    // TCG card falls back to system fonts and the design breaks. The guard
    // makes the call idempotent in case main() is somehow invoked twice.
    function ensureGoogleFonts(): void {
      if (document.getElementById('sc-google-fonts')) return;
      const link = document.createElement('link');
      link.id = 'sc-google-fonts';
      link.rel = 'stylesheet';
      link.href = 'https://fonts.googleapis.com/css2?family=Bowlby+One+SC&family=Public+Sans:wght@400;600;700;800;900&family=Spectral:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400;700&display=swap';
      document.head.appendChild(link);
    }
    ensureGoogleFonts();

    // Plan H proxy on :7271 forwards to engine on :7267 with belief-aware
    // opp-Pokemon overlays. If the proxy isn't running the request fails;
    // start it with `python -m showdown_copilot.proxy`. To bypass the proxy
    // entirely (e.g., when only the engine is running), point this at :7267.

    // ---- UI -------------------------------------------------------------
    const panel = document.createElement('div');
    panel.id = 'sc-panel';
    panel.classList.add('sc-panel-v2');
    // Force scroll + sizing to win over legacy #sc-panel CSS via setProperty('important').
    // Proper fix: dedupe legacy chrome rules into tcg.css. Tracked for follow-up.
    panel.style.setProperty('width', '400px', 'important');
    panel.style.setProperty('max-height', 'calc(100vh - 32px)', 'important');
    panel.style.setProperty('overflow-y', 'auto', 'important');
    panel.style.setProperty('overflow-x', 'hidden', 'important');
    panel.style.setProperty('position', 'fixed', 'important');
    panel.style.setProperty('top', '16px', 'important');
    panel.style.setProperty('right', '16px', 'important');
    panel.style.setProperty('bottom', 'auto', 'important');
    panel.style.setProperty('z-index', '999999', 'important');
    panel.innerHTML = `
      <div class="sc-pinned">
        <div class="sc-header">Copilot — idle</div>
        <div class="sc-conflict" style="display:none"></div>
        <div class="sc-best">—</div>
        <div class="sc-stats">—</div>
        <div class="sc-pv">PV: —</div>
        <div class="sc-alts">—</div>
        <div class="sc-pimc-pinned"></div>
        <div class="sc-notes-header" title="Battle note (press N for per-turn notes)">📝 Battle note <span class="sc-notes-toggle">[show]</span></div>
        <div class="sc-notes-body" style="display:none"><textarea class="sc-battle-note" placeholder="Free-form notes for this battle..." spellcheck="false"></textarea></div>
      </div>
    `;

    document.body.appendChild(panel);

    const hdrEl = panel.querySelector<HTMLDivElement>('.sc-header')!;
    const conflictEl = panel.querySelector<HTMLDivElement>('.sc-conflict')!;
    const bestEl = panel.querySelector<HTMLDivElement>('.sc-best')!;
    const statsEl = panel.querySelector<HTMLDivElement>('.sc-stats')!;
    const pvEl = panel.querySelector<HTMLDivElement>('.sc-pv')!;
    const altsEl = panel.querySelector<HTMLDivElement>('.sc-alts')!;
    const notesHeaderEl = panel.querySelector<HTMLDivElement>('.sc-notes-header')!;
    const notesBodyEl = panel.querySelector<HTMLDivElement>('.sc-notes-body')!;
    const notesToggleEl = panel.querySelector<HTMLSpanElement>('.sc-notes-toggle')!;
    const battleNoteTextarea = panel.querySelector<HTMLTextAreaElement>('.sc-battle-note')!;
    const pimcPinnedEl = panel.querySelector<HTMLDivElement>('.sc-pimc-pinned')!;

    // ---- Status overlay (TCG dashboard) --------------------------------
    // Banner above the TCG card for force-switch / engine-error / lead-pick
    // / bad-state messages. Replaces the legacy bestEl.innerHTML writes
    // that silently no-op'd after Task 7 swapped .sc-best for the TCG card.
    // Sits between the conflict banner and the TCG card; cleared on the
    // next successful renderUpdate so stale errors don't linger.
    function ensureStatusOverlay(): HTMLElement {
      let overlay = panel.querySelector<HTMLElement>('.sc-status-overlay');
      if (overlay) return overlay;
      overlay = document.createElement('div');
      overlay.className = 'sc-status-overlay hidden';
      overlay.style.cssText = `
        padding: 8px 14px;
        background: rgba(40,40,52,0.95);
        border-top: 1px solid #2a2a32;
        border-bottom: 1px solid #2a2a32;
        color: #ddd;
        font-family: 'Spectral', serif;
        font-style: italic;
        font-size: 12.5px;
        line-height: 1.4;
        display: none;
      `;
      // Mount above the TCG card if it exists, otherwise append to panel.
      const card = panel.querySelector('.sc-tcg-card');
      if (card && card.parentElement) {
        card.parentElement.insertBefore(overlay, card);
      } else {
        panel.appendChild(overlay);
      }
      return overlay;
    }

    function showStatusOverlay(message: string, severity: 'error' | 'info' = 'info'): void {
      const o = ensureStatusOverlay();
      o.classList.remove('hidden');
      o.style.display = 'block';
      o.style.color = severity === 'error' ? '#ff8888' : '#ddd';
      o.textContent = message;
    }

    function hideStatusOverlay(): void {
      const o = panel.querySelector<HTMLElement>('.sc-status-overlay');
      if (o) {
        o.classList.add('hidden');
        o.style.display = 'none';
      }
    }

    /**
     * Make a panel collapsible: clicking the header toggles `.collapsed` on
     * the root, which CSS uses to hide the body. State persists per user via
     * localStorage so the preference survives turn re-renders and reloads.
     */
    function attachPanelToggle(root: HTMLElement, headerSelector: string, storageKey: string): void {
      const header = root.querySelector<HTMLElement>(headerSelector);
      if (!header) return;
      const stored = (() => {
        try { return localStorage.getItem(storageKey) === '1'; } catch { return false; }
      })();
      if (stored) root.classList.add('collapsed');

      // Add a small visual chevron at the end of the header so the user
      // knows it's interactive (without burning a slot for a separate button).
      let chevron = header.querySelector<HTMLElement>('.sc-collapse-chevron');
      if (!chevron) {
        chevron = document.createElement('span');
        chevron.className = 'sc-collapse-chevron';
        header.appendChild(chevron);
      }
      const syncChevron = () => {
        chevron!.textContent = root.classList.contains('collapsed') ? '▸' : '▾';
      };
      syncChevron();

      header.style.cursor = 'pointer';
      header.addEventListener('click', () => {
        const nowCollapsed = !root.classList.contains('collapsed');
        root.classList.toggle('collapsed', nowCollapsed);
        try { localStorage.setItem(storageKey, nowCollapsed ? '1' : '0'); } catch { /* ignore */ }
        syncChevron();
      });
    }

    // Map legacy ConflictWarning.level → ConflictBanner severity. The new
    // banner only supports three severities; legacy `info` falls back to
    // POSSIBLE (it's mid-tier between STRONG and PIVOT and matches the
    // legacy `info` styling intent: advisory, non-blocking).
    function mapConflictSeverity(level: 'strong' | 'warn' | 'pivot' | 'info'): ConflictSeverity {
      if (level === 'strong') return 'STRONG';
      if (level === 'pivot') return 'PIVOT';
      return 'POSSIBLE'; // 'warn' and 'info' both map here
    }

    function toPanelThreatRow(t: any): PanelThreatRow {
      // Pick the worst victim from the legacy Threat.victims list — the
      // panel renders one row per (move, source, target) tuple so we
      // collapse the victim list to its top-damage entry.
      const worst = (t?.victims ?? []).slice().sort(
        (a: any, b: any) => (b?.dmgPct ?? 0) - (a?.dmgPct ?? 0),
      )[0];
      return {
        move: String(t?.oppMove ?? ''),
        source: String(t?.oppSpecies ?? ''),
        target: String(worst?.species ?? ''),
        dmgPct: Math.round(worst?.dmgPct ?? 0),
        isOhko: !!worst?.ohko,
        source_seen: t?.moveSource === 'revealed',
      };
    }

    // ---- Damage matrix (data only — UI superseded by TCG card) ---------
    // Computes opp→us damage % per (attacker, defender) pair, via
    // @smogon/calc with belief-driven move/item/spread choices. The legacy
    // expandable matrix card was removed in the TCG dashboard redesign;
    // `lastMatrix` is still populated because the new threats panel +
    // TCG card bottom strip + LLM explainer summary all read it.
    let lastMatrix: DamageMatrix | null = null;
    /** Mine-attacks-opp direction. Powers the safe-switch chips'
     *  "best move back" lookup in the conflict banner. Built alongside
     *  lastMatrix in refreshMatrix; null when teams aren't ready. */
    let lastMeAttacksMatrix: DamageMatrix | null = null;
    let lastBeliefSnapshot: BeliefSnapshot | null = null;

    async function refreshMatrix(b: any, br: any): Promise<void> {
      if (!b || !br?.id) return;
      const myTeam = (b.myPokemon || []).map((p: any) => buildMyPokemon(p, null, win));
      const oppTeam = (b.farSide?.pokemon || []).map((p: any) => buildOppPokemon(p, win));
      if (!myTeam.length || !oppTeam.length) {
        lastMatrix = null;
        lastMeAttacksMatrix = null;
        return;
      }
      const snap = await fetchBeliefSnapshot(PROXY_BASE_URL, br.id);
      if (snap) lastBeliefSnapshot = snap;
      const beliefByOpp: Record<string, any> = {};
      for (const [species, b2] of Object.entries(snap?.opponents || {})) {
        beliefByOpp[species] = b2;
      }
      const field = { weather: detectWeather(b) || '', terrain: detectTerrain(b) || '' };
      lastMatrix = buildDamageMatrix({
        attackers: oppTeam, defenders: myTeam,
        beliefByDefender: beliefByOpp,
        field,
        attackerSide: 'opp',
      });
      // Symmetric mine-attacks-opp direction: my mons' moves vs opp team.
      // Drives the safe-switch "best move back" data in the conflict banner.
      lastMeAttacksMatrix = buildDamageMatrix({
        attackers: myTeam, defenders: oppTeam,
        beliefByDefender: beliefByOpp,
        field,
        attackerSide: 'mine',
      });
    }

    // ---- Threats (data only — UI superseded by threats panel) ----------
    // Surfaces the highest-damage threats from opp's active mon (and bench)
    // against your team, derived from the DamageMatrix above. Reads
    // `lastMatrix` directly — refreshThreats requires refreshMatrix has
    // already populated it. The threats panel renderer reads `lastThreats`
    // on every engine update.
    let lastThreats: ThreatsReport | null = null;

    function refreshThreats(b: any): void {
      if (!b || !lastMatrix) {
        lastThreats = null;
        return;
      }
      const myActive = b.mySide?.active?.[0];
      const oppActive = b.farSide?.active?.[0];
      if (!myActive || !oppActive) {
        lastThreats = null;
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
    }

    // ---- PIMC vote breakdown (data only — UI superseded by energy orbs) -
    // Per-hypothesis vote distribution from the PIMC proxy mode (only
    // populated when POKE_PROXY_PIMC_K > 0 and the engine emits
    // `pimcBreakdown` on its `final` event). Surfaces consensus vs split
    // decisions via the pinned line + per-move energy orbs on the TCG card.
    let lastPimcBreakdown: any[] | null = null;
    let lastPimcBest: string | null = null;

    // ---- Engine-confidence trend tracker --------------------------------
    // Per-battle val history (most-recent last, capped at VAL_HISTORY_CAP).
    // Keyed by Showdown battleId so closing+reopening a battle doesn't reuse
    // a prior position's history. Cleared lazily — old entries cost ~80 bytes
    // each so we don't bother to GC.
    //
    // The displayed `confidence` from the engine `final` event is our val.
    // Append once per new (battleId, turn) pair so multi-update streams
    // don't double-count the same turn's value.
    const valHistoryByBattle = new Map<string, number[]>();
    const lastTrackedTurnByBattle = new Map<string, number>();

    // ---- Explainer (data only — UI superseded by TCG flavor strip) -----
    // LLM-rendered explanation of the engine's recommendation in plain
    // English. Fired once per engine `final` event, with the matrix
    // top-cells summary so the LLM can spot conflicts (engine recommends
    // stay-in but matrix says we get OHKO'd, etc.). Cached on
    // (battleId, turn, rqid) by lib/explainer.ts. Surfaced as a one-line
    // flavor strip on the TCG card.
    let lastExplanation: string | null = null;
    let explainerLoading = false;

    // Build a top-cells summary of `lastMatrix` for the LLM. Caps prompt
    // size at ~8 cells, OHKO-prioritized then by max damage. Returns
    // undefined if the matrix is empty so the proxy skips the field.
    function buildMatrixSummary(): any | undefined {
      if (!lastMatrix) return undefined;
      const cells = lastMatrix.cells.slice();
      cells.sort((a, b) => {
        if (a.ohko !== b.ohko) return a.ohko ? -1 : 1;
        return b.dmgPctMax - a.dmgPctMax;
      });
      const top = cells.slice(0, 8);
      if (lastMatrix.attackerSide === 'opp') {
        return {
          opp_attacks_me: top.map(c => ({
            opp: c.attacker, move: c.move, source: c.moveSource,
            target: c.defender, dmg_pct_max: c.dmgPctMax,
            ohko: c.ohko, two_hko: c.twoHko,
          })),
        };
      } else {
        return {
          me_attacks_opp: top.map(c => ({
            me: c.attacker, move: c.move, source: c.moveSource,
            target: c.defender, dmg_pct_max: c.dmgPctMax,
            ohko: c.ohko, two_hko: c.twoHko,
          })),
        };
      }
    }

    // ---- Annotation feature ---------------------------------------------
    // Per-turn notes (keyboard 'N') + per-battle freeform notes (textarea).
    // Stored under sc:turn-notes:<battleId> and sc:battle-note:<battleId>
    // during play; merged into the final post-mortem at persist time.
    // Also fire-and-forget POSTed to the proxy at /annotation so they
    // land on disk at analysis/play-notes/YYYY-MM-DD.jsonl independently
    // of localStorage capture.

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
    function writeTurnOverrideTag(battleId: string, turn: number, tag: string): void {
      localStorage.setItem(`sc:override-tag:${battleId}:${turn}`, tag);
    }
    function readTurnOverrideTag(battleId: string, turn: number): string | null {
      return localStorage.getItem(`sc:override-tag:${battleId}:${turn}`);
    }
    function writeTurnConflictWarning(battleId: string, turn: number, warning: any) {
      if (warning === null) return;  // don't pollute storage with nulls
      localStorage.setItem(
        `sc:conflict-warning:${battleId}:${turn}`,
        JSON.stringify(warning),
      );
    }
    function readTurnConflictWarning(battleId: string, turn: number): any | null {
      const raw = localStorage.getItem(`sc:conflict-warning:${battleId}:${turn}`);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch { return null; }
    }
    function writeTurnBeliefSnapshot(battleId: string, turn: number, snap: any) {
      if (!snap) return;
      try { localStorage.setItem(`sc:belief:${battleId}:${turn}`, JSON.stringify(snap)); }
      catch {}
    }
    function readTurnBeliefSnapshot(battleId: string, turn: number): any | null {
      const raw = localStorage.getItem(`sc:belief:${battleId}:${turn}`);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch { return null; }
    }
    function writeTurnMatrixSummary(battleId: string, turn: number, summary: any) {
      if (!summary) return;
      try { localStorage.setItem(`sc:matrix:${battleId}:${turn}`, JSON.stringify(summary)); }
      catch {}
    }
    function readTurnMatrixSummary(battleId: string, turn: number): any | null {
      const raw = localStorage.getItem(`sc:matrix:${battleId}:${turn}`);
      if (!raw) return null;
      try { return JSON.parse(raw); } catch { return null; }
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
      overrideTag?: string | null;
    }): void {
      // Fire-and-forget — do not block UX. localStorage is the fallback.
      fetch(PROXY_ANNOTATION_URL, {
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
      '  <select class="sc-override-tag">',
      '    <option value="">— no engine error this turn —</option>',
      '    <option value="item_assumption">Engine wrong: item assumption (CB? Scarf?)</option>',
      '    <option value="speed_assumption">Engine wrong: speed assumption</option>',
      '    <option value="ability_missed">Engine wrong: ability missed (HA Multiscale, etc.)</option>',
      '    <option value="set_unusual">Opp ran unusual / off-meta set</option>',
      '    <option value="long_term">Engine optimized too short-term</option>',
      '    <option value="engine_correct">I overrode but engine was right</option>',
      '    <option value="other">Other engine error</option>',
      '  </select>',
      '  <input type="text" class="sc-note-input" maxlength="500" placeholder="What did you notice?" />',
      '  <div class="sc-note-hint">Enter to save · Esc to cancel</div>',
      '</div>',
    ].join('');
    document.body.appendChild(noteModal);
    const noteModalTurnEl = noteModal.querySelector<HTMLSpanElement>('.sc-note-turn')!;
    const noteModalInput = noteModal.querySelector<HTMLInputElement>('.sc-note-input')!;
    const noteModalTag = noteModal.querySelector<HTMLSelectElement>('.sc-override-tag')!;

    function openNoteModal(): void {
      if (!annotationState.battleId) return;
      const turn = annotationState.turn;
      noteModalTurnEl.textContent = String(turn);
      const existing = readTurnNotes(annotationState.battleId)[String(turn)] || '';
      noteModalInput.value = existing;
      const existingTag = readTurnOverrideTag(annotationState.battleId, turn) || '';
      noteModalTag.value = existingTag;
      noteModal.classList.add('visible');
      noteModalInput.focus();
      noteModalInput.select();
    }
    function closeNoteModal(): void {
      noteModal.classList.remove('visible');
      noteModalInput.value = '';
      noteModalTag.value = '';
    }
    function saveNoteFromModal(): void {
      const battleId = annotationState.battleId;
      if (!battleId) { closeNoteModal(); return; }
      const turn = annotationState.turn;
      const text = noteModalInput.value.trim();
      const tag = noteModalTag.value || null;
      writeTurnNote(battleId, turn, text);
      if (tag) writeTurnOverrideTag(battleId, turn, tag);
      if (text || tag) {
        postAnnotation({ battleId, turn, kind: 'turn', text, overrideTag: tag });
      }
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

    function persistPostMortem(pm: BattlePostMortem, opts?: { final?: boolean }): void {
      // `final` defaults to true so the existing battle-end caller is unchanged.
      // Soft-persist callers (per-turn) pass `final: false` to write to
      // localStorage only and skip the disk POST — the proxy filename uses
      // `endedAtMs` which is 0/missing mid-battle, so a disk POST per turn
      // would pollute the archive with intermediate snapshots.
      const isFinal = opts?.final !== false;
      // Overlay any in-battle annotations from temp localStorage keys
      // onto the parsed post-mortem before persisting.
      const turnNotes = readTurnNotes(pm.battleId);
      const battleNote = readBattleNote(pm.battleId);
      if (battleNote) pm.battleNote = battleNote;
      for (const t of pm.turns) {
        const note = turnNotes[String(t.turn)];
        if (note) t.userNote = note;
        t.userOverrideTag = readTurnOverrideTag(pm.battleId, t.turn) as any;
        t.conflictWarning = readTurnConflictWarning(pm.battleId, t.turn);
        t.beliefSnapshot = readTurnBeliefSnapshot(pm.battleId, t.turn);
        t.matrixSummary = readTurnMatrixSummary(pm.battleId, t.turn);
      }
      // Derive replay URL from the battle ID. Showdown IDs look like
      // `battle-gen9nationaldex-2604189999-7lj3ryrg…`; the replay path
      // strips the `battle-` prefix. Fall back to null when the prefix
      // is absent (defensive against edge-case IDs from older builds).
      pm.replayUrl = pm.battleId.startsWith('battle-')
        ? `https://replay.pokemonshowdown.com/${pm.battleId.slice(7)}`
        : null;
      const key = `sc:postmortem:${pm.battleId}`;
      const json = JSON.stringify(pm);

      // Fire-and-forget mirror to disk via proxy. localStorage remains the
      // canonical client-side store; this is the engine-debug corpus path.
      // Placed BEFORE the localStorage try so the disk write happens even if
      // QuotaExceededError forces us into the prune-and-retry path below.
      //
      // Fix B (2026-05-08): fires for BOTH soft (per-turn) and hard (battle-
      // end) persists. The proxy /postmortem endpoint detects same-battleId
      // and OVERWRITES the existing file (battleId-keyed), so per-turn POSTs
      // converge to one file per battle. Eliminates the navigate-away data
      // loss case — the disk archive stays current even if the user closes
      // the tab before Showdown's battle-end signal fires.
      fetch(PROXY_POSTMORTEM_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: json,
        keepalive: true,
      }).catch(() => { /* proxy down — localStorage still has it */ });
      // `isFinal` is now informational only; both branches fire the same POST.
      void isFinal;

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

    // Debug helpers added 2026-05-08 after persist-pipeline issues. Each is
    // safe to call from the browser console; intended for ad-hoc diagnostics.

    (win as any).__scLocalStorageHealth = () => {
      const allKeys = Object.keys(localStorage);
      const scKeys = allKeys.filter(k => k.startsWith('sc:'));
      const grouped: Record<string, number> = {};
      for (const k of scKeys) {
        const prefix = k.split(':').slice(0, 2).join(':');
        grouped[prefix] = (grouped[prefix] || 0) + 1;
      }
      return {
        totalKeys: allKeys.length,
        scKeys: scKeys.length,
        byPrefix: grouped,
        postmortemKeys: scKeys.filter(k => k.startsWith('sc:postmortem:')),
      };
    };

    // Force a postmortem write for the CURRENT battle, bypassing all the
    // engine-final / cache-key gating. Reads scHistory + stepQueue + meta
    // directly, runs the parser, calls persistPostMortem (which writes both
    // localStorage and disk via the proxy /postmortem endpoint).
    // Returns a summary of what was written.
    (win as any).__scForcePersist = () => {
      const room = win.app?.curRoom;
      const b = room?.battle;
      if (!b || !room?.id) {
        return { ok: false, error: 'no active battle in curRoom' };
      }
      try {
        const battleRecords = scHistory.filter(r => r.battleId === room.id);
        const mySideId = (b.mySide?.sideid || b.mySide?.id || 'p1') as 'p1' | 'p2';
        const pm = parseBattlePostMortem(
          battleRecords as any,
          (b.stepQueue || []).slice(),
          {
            battleId: room.id,
            format: b.tier || 'unknown',
            myUsername: b.mySide?.name || 'unknown',
            mySideId,
            opponent: b.farSide?.name || 'unknown',
          },
        );
        persistPostMortem(pm, { final: true });
        return {
          ok: true,
          scHistoryRecords: battleRecords.length,
          stepQueueLen: (b.stepQueue || []).length,
          pmTurns: pm.turns.length,
          totalTurns: pm.totalTurns,
        };
      } catch (err) {
        return { ok: false, error: String(err) };
      }
    };

    // Dump everything about the current battle that the parser needs, as a
    // single JSON blob. Useful for offline parser debugging — paste into
    // analysis script + iterate without browser round-trips.
    (win as any).__scDumpForParse = () => {
      const room = win.app?.curRoom;
      const b = room?.battle;
      if (!b || !room?.id) return null;
      return {
        records: scHistory.filter(r => r.battleId === room.id),
        stepQueue: (b.stepQueue || []).slice(),
        meta: {
          battleId: room.id,
          format: b.tier || 'unknown',
          myUsername: b.mySide?.name || 'unknown',
          mySideId: (b.mySide?.sideid || b.mySide?.id || 'p1'),
          opponent: b.farSide?.name || 'unknown',
        },
      };
    };

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

    function renderConflict(c: ConflictWarning | null) {
      // Legacy .sc-conflict line stays hidden.
      conflictEl.style.display = 'none';

      // Banner mounts INSIDE the TCG card (as first child) so it's visually
      // attached to the recommendation it warns about, rather than being a
      // floating sibling. Strip any previous banner (either inside the card
      // now, or as the old top-level sibling) before inserting the new one.
      panel.querySelector('.sc-conflict-banner')?.remove();
      const card = panel.querySelector<HTMLElement>('.sc-tcg-card');
      if (!c || !card) return;

      const newBanner = renderConflictBanner({
        severity: mapConflictSeverity(c.level),
        reason: c.message,
        safeSwitches: c.safeSwitches?.map(s => ({
          species: s.species,
          worstDmgPct: s.worstDmgPct,
          bestMoveBack: s.bestMoveBack,
          fasterThanOpp: s.fasterThanOpp,
        })),
      });
      card.insertBefore(newBanner, card.firstChild);
    }

    // Detect whether the engine's bestMove string is a switch (species name) or
    // an actual move. The proxy may emit either "switch:dianciemega" (prefix
    // convention) or a bare uppercase species like "DIANCIEMEGA"; moves are
    // typically uppercase like "EARTHQUAKE", so prefix + bench-species lookup
    // is more robust than a leading-capital regex.
    function parseRecommendation(bestMove: string, myTeamSpecies: string[]): {
      move: string; isSwitch: boolean; switchTarget?: string;
    } {
      if (!bestMove) return { move: '', isSwitch: false };
      if (bestMove.startsWith('switch:')) {
        return { move: bestMove, isSwitch: true, switchTarget: bestMove.slice(7) };
      }
      const norm2 = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
      const target = myTeamSpecies.find(sp => norm2(sp) === norm2(bestMove));
      if (target) return { move: bestMove, isSwitch: true, switchTarget: target };
      return { move: bestMove, isSwitch: false };
    }

    /**
     * Map an engine update + battle state into TcgCardProps.
     * Pulls trend from the per-battle val-history, threats from lastThreats.
     *
     * `u` is the engine update event (no project-side TS type defined — fields
     * accessed: bestMove, alternatives, confidence, pimcBreakdown). `threats`
     * is a ThreatsReport | null. We use `any` on opaque inputs because the
     * engine update isn't a project type; tightening can land in Tasks 11+.
     */
    function buildTcgPropsFromUpdate(
      u: any,
      battle: any,
      valHistory: number[],
      llmExplanation: string,
      threats: ThreatsReport | null,
    ): TcgCardProps {
      const trend = computeTrendArrow(valHistory);
      const activeSpecies = battle?.mySide?.active?.[0]?.speciesForme ?? '';
      // Pick the worst on-field threat (already sorted desc by max dmg in
      // computeThreats). Fall back to the first incoming threat, else NONE.
      const topThreat = threats?.onField?.[0] ?? threats?.incoming?.[0] ?? null;
      const worstVictim = topThreat
        ? [...topThreat.victims].sort((a, b) => b.dmgPct - a.dmgPct)[0]
        : null;
      const worstThreat = {
        name: topThreat ? topThreat.oppSpecies : 'NONE',
        dmgPct: worstVictim ? Math.round(worstVictim.dmgPct) : 0,
        isOhko: !!(worstVictim && worstVictim.ohko),
      };
      const flairByType: Record<string, string> = {
        Ice: '❄', Fire: '🔥', Electric: '⚡', Water: '💧',
        Grass: '🌿', Psychic: '✨', Fighting: '👊',
        Dark: '🌑', Steel: '⚙', Fairy: '✦',
      };
      // The engine update does not surface move-type — default to Normal so
      // the energy palette falls back to colorless. Tasks 8+ can wire a move
      // → type lookup via Dex if needed.
      // Frame color follows the active Pokemon's primary type (Showdown dex
      // lookup, falls back to 'Normal' when the dex isn't loaded yet).
      const activeType = getPokemonPrimaryType(activeSpecies);

      // Tally per-move votes from pimcBreakdown (one record per hypothesis,
      // each with `top_move`). Old panels/pimc-vote-bar.ts did this; the new
      // TCG card needs the same data on each move to fill its orbs.
      // voteCap is breakdown.length so partial fanouts (3/4 HYPS etc.) render
      // correctly. Falls back to 0 votes / 4 cap when no breakdown is present.
      const breakdown = Array.isArray(u?.pimcBreakdown) ? u.pimcBreakdown : [];
      const voteCap = breakdown.length || 4;
      const voteTally = new Map<string, number>();
      for (const h of breakdown) {
        const m = typeof h?.top_move === 'string' ? h.top_move : '';
        if (m) voteTally.set(m, (voteTally.get(m) ?? 0) + 1);
      }
      const lookupVotes = (moveName: string): number => {
        if (!moveName) return 0;
        if (voteTally.has(moveName)) return voteTally.get(moveName)!;
        // Engine returns canonical move IDs (lowercase, no spaces) on some
        // paths and display names on others. Try a normalized match too.
        const norm = moveName.toLowerCase().replace(/[^a-z0-9]/g, '');
        for (const [k, v] of voteTally) {
          if (k.toLowerCase().replace(/[^a-z0-9]/g, '') === norm) return v;
        }
        return 0;
      };

      // Build the moves list: recommended first (highlighted .rec row),
      // then up to 4 alternatives. Per-move type comes from the Showdown
      // dex so orb colors reflect each move's actual element.
      const bestMoveName = String(u.bestMove ?? '');
      const recommendedMove: AlternativeMove | null = bestMoveName
        ? {
            name: bestMoveName,
            type: getMoveType(bestMoveName),
            votes: lookupVotes(bestMoveName),
            voteCap,
            winPct: Math.round((u.confidence ?? 0) * 100),
            category: 'P',
            isRecommended: true,
            desc: '',
          }
        : null;

      const altsFromUpdate: AlternativeMove[] = (u.alternatives ?? [])
        .filter((a: any) => (a.move ?? a.name) !== bestMoveName)
        .slice(0, 4)
        .map((a: any) => {
          const name = String(a.move ?? a.name ?? '');
          return {
            name,
            type: (a.type as string | undefined) ?? getMoveType(name),
            votes: typeof a.votes === 'number' ? a.votes : lookupVotes(name),
            voteCap,
            winPct: Math.round((a.confidence ?? 0) * 100),
            category: ((a.category as string | undefined) ?? 'P') as 'P' | 'S' | 'T',
            isRecommended: false,
            desc: String(a.desc ?? ''),
          };
        });

      const moves: AlternativeMove[] = recommendedMove
        ? [recommendedMove, ...altsFromUpdate]
        : altsFromUpdate;
      // Header shows the switch target when the engine recommends a switch,
      // otherwise mirrors the active Pokemon.
      const switchRec = isSwitchOption(bestMoveName);
      const headerSpecies = switchRec ? bestMoveName : activeSpecies;

      return {
        activeType,
        trend,
        activeSpecies,
        headerSpecies,
        isSwitchRec: switchRec,
        turn: battle?.turn ?? 0,
        trendTag: `${trend.arrow} LAST 3`,
        hypsTag: Array.isArray(u?.pimcBreakdown) && u.pimcBreakdown.length > 0
          ? `${u.pimcBreakdown.length} HYPS`
          : 'SINGLE',
        winPct: Math.round((u.confidence ?? 0) * 100),
        llmExplanation: llmExplanation || 'Analyzing turn…',
        moves,
        worstThreat,
        retreatCost: 2,
        sparklineHistory: valHistory,
        flairChar: flairByType[activeType] ?? '◆',
      };
    }

    function renderUpdate(u: any) {
      const arrow = u.event === 'final' ? '▲' : '•';
      const confRaw = u.confidence || 0;
      const conf = (confRaw * 100).toFixed(0);

      // Track per-battle val history on `final` events. Streamed
      // intermediate updates (event !== 'final') don't move into the
      // history — the engine's intermediate values can swing wildly while
      // the search is mid-flight, and we only want stable per-turn samples.
      let trend: Trend = null;
      let desperate = false;
      const battleId: string | undefined = win.app?.curRoom?.id;
      const turn: number | undefined = win.app?.curRoom?.battle?.turn;

      if (u.event === 'final' && battleId) {
        // Append exactly once per (battleId, turn). Showdown sometimes
        // re-fires final on retry / annotation flow — guard with the
        // tracked-turn map so a single decision doesn't pollute history.
        const lastTrackedTurn = lastTrackedTurnByBattle.get(battleId);
        if (typeof turn === 'number' && lastTrackedTurn !== turn) {
          const prev = valHistoryByBattle.get(battleId) ?? [];
          const next = appendVal(prev, confRaw);
          valHistoryByBattle.set(battleId, next);
          lastTrackedTurnByBattle.set(battleId, turn);
        }
        const hist = valHistoryByBattle.get(battleId) ?? [];
        trend = computeTrend(hist);
        // PIMC split suppresses the DESPERATE flag — the scalar val under a
        // hedged hypothesis distribution is misleading; the split tag
        // already communicates uncertainty. We compute the split locally
        // here (instead of reading lastPimcSplit) because updatePimcDisplay
        // runs AFTER this block on every render, so lastPimcSplit would
        // reflect the previous turn's state.
        const breakdown = Array.isArray(u?.pimcBreakdown) ? u.pimcBreakdown : null;
        let currentPimcSplit = false;
        if (breakdown && breakdown.length > 0) {
          const consensus = (typeof u?.bestMove === 'string' && u.bestMove)
            ? u.bestMove
            : (breakdown[0]?.top_move ?? '');
          const agree = breakdown.filter((h: any) => h?.top_move === consensus).length;
          currentPimcSplit = agree < breakdown.length;
        }
        desperate = isDesperate(confRaw, currentPimcSplit);
      }

      const trendGlyph = formatTrendArrow(trend);
      const trendTitle = formatTrendTitle(trend);
      const trendCls =
        trend === 'rising' ? 'sc-trend-rising' :
        trend === 'falling' ? 'sc-trend-falling' :
        trend === 'collapsing' ? 'sc-trend-collapsing' : '';
      const trendHtml = trendGlyph
        ? ` <span class="sc-trend-arrow ${trendCls}" title="${escapeHtml(trendTitle)}">${trendGlyph}</span>`
        : '';

      const desperateHtml = desperate
        ? ` <span class="sc-desperate" title="engine is recommending the least-bad move from a losing position. Consider sacrificing this mon or switching out.">DESPERATE</span>`
        : '';

      // Sparkline of recent val samples. Only meaningful on `final` events
      // (we don't pollute history with mid-search intermediate values, so
      // showing one for streamed updates would be stale/misleading). On
      // turn 1 the helper renders a neutral em-dash placeholder so the
      // confidence line doesn't reflow when the chart appears on turn 2.
      let sparklineHtml = '';
      if (u.event === 'final' && battleId) {
        const hist = valHistoryByBattle.get(battleId) ?? [];
        sparklineHtml = ' ' + renderSparkline(hist);
      }

      // New TCG card rendering — replaces the inline .sc-best HTML write.
      // On first call we swap the legacy .sc-best element with the card; on
      // subsequent calls we replace the existing card in place. The val-
      // history map populated above on `final` events feeds the sparkline.
      //
      // Task 11 redirected the 7 legacy bestEl.innerHTML write sites to
      // ensureStatusOverlay() so force-switch / engine-error / lead-pick /
      // bad-state messages still surface above the card. We clear that
      // overlay here so a stale error from a prior turn doesn't linger
      // once the engine returns a fresh valid update.
      hideStatusOverlay();
      {
        const battleForCard = win.app?.curRoom?.battle;
        const battleIdForCard: string | undefined = win.app?.curRoom?.id;
        const valHist = battleIdForCard
          ? (valHistoryByBattle.get(battleIdForCard) ?? [])
          : [];
        const tcgProps = buildTcgPropsFromUpdate(
          u,
          battleForCard,
          valHist,
          '',                       // LLM explanation — Tasks 8+ wire this in
          lastThreats ?? null,
        );
        const newCard = renderTcgCard(tcgProps);
        // .sc-best is the legacy initial placeholder in the panel HTML;
        // on first mount the card replaces it. After that .sc-tcg-card
        // exists and gets swapped in place.
        mountOrReplace(panel, {
          newEl: newCard,
          replaceTargets: ['.sc-tcg-card', '.sc-best'],
        });
      }

      // ---- Threats panel (Task 11) ---------------------------------------
      // Replaces the legacy expandable .sc-trainer-card / threats card body.
      // Reads from `lastThreats` (populated by refreshThreats elsewhere).
      // Mounted directly after the TCG card inside .sc-pinned so the user
      // sees the worst-case opp moves immediately under the recommendation.
      {
        const threatsProps = {
          onField: (lastThreats?.onField ?? []).map(toPanelThreatRow),
          incoming: (lastThreats?.incoming ?? []).map(toPanelThreatRow),
        };
        const newThreats = renderThreatsPanel(threatsProps);
        // Mount ABOVE the TCG card so the "what's about to kill you"
        // signal is the first thing the user sees — threats are the
        // emergency layer; the recommendation comes after.
        mountOrReplace(panel, {
          newEl: newThreats,
          replaceTargets: ['.sc-trainer-card'],
          anchors: [{ selector: '.sc-tcg-card', position: 'before' }],
        });
        attachPanelToggle(newThreats, '.trainer-header', 'sc-threats-collapsed');
      }

      // ---- PV chain (Task 11) --------------------------------------------
      // Inline engine principal-variation chain. Mounted below the threats
      // panel so the eye moves: recommended move → threats → engine line.
      // Steps alternate me/opp by index (engine PV interleaves sides).
      {
        const pvList: any[] = Array.isArray(u?.pv) ? u.pv : [];
        if (pvList.length > 0) {
          const steps: PvStep[] = pvList.slice(0, 4).map((move: any, i: number) => ({
            move: String(move ?? '').toUpperCase(),
            side: (i % 2 === 0 ? 'me' : 'opp') as 'me' | 'opp',
          }));
          const newPv = renderPvChain({
            steps,
            depth: typeof u.depth === 'number' ? u.depth : 0,
            sims: typeof u.sims === 'number' ? u.sims : 0,
          });
          // PV chain sits immediately after the threats panel, so the eye
          // moves: threats → engine line → recommendation.
          mountOrReplace(panel, {
            newEl: newPv,
            replaceTargets: ['.sc-pv-card'],
            anchors: [{ selector: '.sc-trainer-card', position: 'after' }],
          });
          attachPanelToggle(newPv, '.pv-header', 'sc-pv-collapsed');
        }
      }

      statsEl.textContent =
        `sims ${(u.sims || 0).toLocaleString()}  depth ${u.depth || 0}` +
        (u.error ? `  ERROR: ${u.error}` : '');
      pvEl.textContent = `PV: ${(u.pv || []).join(' → ') || '—'}`;
      altsEl.textContent = (u.alternatives || [])
        .slice(0, 3)
        .map((a: any) => `${labelMove(a.move)} ${((a.confidence || 0) * 100).toFixed(0)}%`)
        .join(' | ') || '—';
      // PIMC vote bar — only present when the proxy is in PIMC mode. We
      // intentionally render on every `final` update (cheap) so a stale
      // breakdown from a prior turn doesn't linger; if the field is absent
      // the pinned line stays hidden and the card body says "no PIMC data".
      if (u.event === 'final') {
        updatePimcDisplay(u);
      }
    }


    function updatePimcDisplay(u: any) {
      const breakdown = Array.isArray(u?.pimcBreakdown) ? u.pimcBreakdown : null;
      if (!breakdown || breakdown.length === 0) {
        // Single-modal response → clear pinned line and stored breakdown
        // so the TCG card's energy orbs stop showing stale vote counts.
        lastPimcBreakdown = null;
        lastPimcBest = null;
        pimcPinnedEl.classList.remove('visible', 'split');
        pimcPinnedEl.textContent = '';
        return;
      }
      lastPimcBreakdown = breakdown;
      lastPimcBest = typeof u?.bestMove === 'string' ? u.bestMove : null;
      const k = breakdown.length;
      const consensus = lastPimcBest ?? (breakdown[0]?.top_move ?? '(unknown)');
      const agree = breakdown.filter((h: any) => h?.top_move === consensus).length;
      const split = agree < k;
      pimcPinnedEl.classList.add('visible');
      pimcPinnedEl.classList.toggle('split', split);
      pimcPinnedEl.innerHTML =
        `${agree} of ${k} hypotheses agree on: <b>${escapeHtml(consensus)}</b>` +
        `<span class="sc-pimc-badge">PIMC: K=${k}</span>` +
        (split ? ' <span class="sc-pimc-split-tag">⚠ split</span>' : '');
    }

    // ---- engine call with native fetch streaming ------------------------
    let abortCtrl: AbortController | null = null;

    // Force-switch resolver. When Showdown demands a Pokemon (forceSwitch
    // rqid) the engine's bestMove may still be a move like "THUNDERPUNCH"
    // the user can't legally pick — scan bestMove + alternatives for the
    // first one that matches a team-species name and return it. Returns
    // null when the engine's top-K contains no switches; callers fall back
    // to the status-overlay "manual pick required" path.
    function findForceSwitchTarget(u: any): string | null {
      const b = win.app?.curRoom?.battle;
      if (!b) return null;
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
      const picks = [
        u.bestMove,
        ...((u.alternatives || []).map((a: any) => a.move)),
      ];
      for (const move of picks) {
        const species = findSpecies(move);
        if (species) return species;
      }
      return null;
    }

    // Rewrite a force-switch engine response so the TCG card's native
    // isSwitchRec path renders the recommendation: bestMove becomes the
    // species name, parseRecommendation downstream flags it as a switch,
    // and the card header flips to "SWITCH TO · T<n>". Returns null when
    // the engine surfaced no switches (caller falls back to status overlay).
    function rewriteForForceSwitch(u: any): any | null {
      const target = findForceSwitchTarget(u);
      if (!target) return null;
      return { ...u, bestMove: target };
    }

    // Test hook: synthetically trigger the force-switch flow from the console
    // / MCP without waiting for a live forceSwitch rqid.
    (win as any).__scTestForceSwitch = (u: any) => {
      const rewritten = rewriteForForceSwitch(u);
      if (rewritten) {
        renderUpdate(rewritten);
      } else {
        hdrEl.textContent = 'Copilot — force switch (engine returned no switch)';
        showStatusOverlay('FORCE SWITCH: — manual pick required', 'info');
      }
    };

    function handleEngineUpdate(u: any, record: DecisionRecord | null) {
      // Force-switch rewrite. When Showdown requires a Pokemon, replace the
      // engine's (possibly-illegal) bestMove with the top-ranked switch
      // target species *before* the TCG card consumes it — parseRecommendation
      // sees a team-species, flips isSwitchRec=true, and the card renders
      // "SWITCH TO · T<n>" natively. Falls back to status overlay only when
      // the engine's top-K had no switches at all.
      if (record?.forceSwitch && u.bestMove && !u.error) {
        const rewritten = rewriteForForceSwitch(u);
        if (rewritten) {
          u = rewritten;
        } else {
          hdrEl.textContent = 'Copilot — force switch (engine returned no switch)';
          showStatusOverlay('FORCE SWITCH: — manual pick required', 'info');
        }
      }
      renderUpdate(u);
      // Conflict warning: compare engine recommendation against threats report.
      if (u.event === 'final' && lastThreats) {
        const b = win.app?.curRoom?.battle;
        const myActive = b?.mySide?.active?.[0];
        const oppActive = b?.farSide?.active?.[0];
        if (myActive && oppActive) {
          const myTeamSnaps = (b.myPokemon || []).map((p: any) => buildMyPokemon(p, null, win));
          const myTeamSpecies = myTeamSnaps.map((p: any) => p.species);
          const rec = parseRecommendation(u.bestMove || '', myTeamSpecies);
          const conflict = detectConflict({
            engineRecommendation: rec,
            threats: lastThreats,
            myActive: buildMyPokemon(myActive, null, win),
            oppActive: buildOppPokemon(oppActive, win),
            myTeam: myTeamSnaps,
            meAttacksMatrix: lastMeAttacksMatrix ?? undefined,
          });
          renderConflict(conflict);
          // Persist for the post-battle overlay (debug-corpus). The helper
          // no-ops when `conflict` is null, so we only fill storage when
          // there's an actual warning to record.
          const battleId = win.app?.curRoom?.id;
          if (battleId) {
            const turn = b?.turn ?? 0;
            if (conflict) writeTurnConflictWarning(battleId, turn, conflict);
          }
        } else {
          renderConflict(null);
        }
      }
      // Tier 2 debug-corpus: freeze belief snapshot + matrix summary onto
      // localStorage at engine-final time so persistPostMortem can overlay
      // per-turn snapshots at battle-end (parser can't see them retroactively).
      if (u.event === 'final') {
        const b = win.app?.curRoom?.battle;
        const battleId = win.app?.curRoom?.id;
        if (battleId) {
          const turn = b?.turn ?? 0;
          if (lastBeliefSnapshot) writeTurnBeliefSnapshot(battleId, turn, lastBeliefSnapshot);
          const summary = buildMatrixSummary();
          if (summary !== undefined) writeTurnMatrixSummary(battleId, turn, summary);

          // Defense-in-depth: ensure scHistory has a record for THIS turn
          // before we parse. The poll-loop push at scHistory.push(record)
          // can miss turns where Showdown's rqid stays stuck (force-switch
          // chains, status-induced no-decision turns). Without this guard
          // the postmortem.turns array silently truncates at the last
          // poll-loop-pushed turn while engine.log keeps growing.
          // Observed in the dhtxdty 2026-05-08 battle: postmortem had 5
          // records, engine.log had 12 turns of instrument data.
          const req = (win.app?.curRoom?.request || b?.request);
          const rqid = req?.rqid ?? 0;
          const alreadyTracked = scHistory.some(
            r => r.battleId === battleId && r.turn === turn && r.rqid === rqid,
          );
          if (!alreadyTracked && record) {
            // record was passed in but didn't make it into scHistory — push
            // it now. Most often this means the poll loop's cache-skip path
            // fired and we never hit the post-push site at line ~1299.
            console.log(`[sc:postmortem] backfilling scHistory for T${turn} rqid=${rqid} (poll-loop missed)`);
            scHistory.push(record);
          } else if (!alreadyTracked && !record) {
            // No record handle to push; create a minimal stub so the parser
            // sees this turn even without payload/state context.
            console.log(`[sc:postmortem] stubbing scHistory for T${turn} rqid=${rqid} (no record + poll-loop missed)`);
            const stub: DecisionRecord = {
              battleId,
              turn,
              rqid,
              tStartMs: Date.now(),
              forceSwitch: !!req?.forceSwitch,
              state: snapshotState(b),
              payload: null as any,
              updates: [u],
            };
            stub.final = u;
            stub.tEndMs = Date.now();
            scHistory.push(stub);
          }
        }
        // Soft-persist the in-progress postmortem so navigating away mid-battle
        // doesn't lose data. Disk POST happens too (Fix B 2026-05-08:
        // proxy /postmortem detects same-battleId and overwrites — per-turn
        // POSTs converge to one file per battle).
        if (b && battleId) {
          try {
            const battleRecords = scHistory.filter(r => r.battleId === battleId);
            const mySideId = (b.mySide?.sideid || b.mySide?.id || 'p1') as 'p1' | 'p2';
            const pm = parseBattlePostMortem(
              battleRecords as any,
              (b.stepQueue || []).slice(),
              {
                battleId,
                format: b.tier || 'unknown',
                myUsername: b.mySide?.name || 'unknown',
                mySideId,
                opponent: b.farSide?.name || 'unknown',
              },
            );
            console.log(`[sc:postmortem] soft persist: ${battleRecords.length} records → ${pm.turns.length} turns in pm`);
            persistPostMortem(pm, { final: false });
          } catch (err) {
            console.warn('[sc:postmortem] soft persist failed', err);
          }
        }
      }
      if (!record) return;
      record.updates.push(u);
      if (u.event === 'final' || u.error) {
        record.final = u;
        record.tEndMs = Date.now();
        // Force-switch handling now runs at the top of handleEngineUpdate
        // (rewriteForForceSwitch) so the TCG card renders the switch target
        // natively. Nothing extra to do here on `final`.
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
      // Fire the LLM explainer once per turn-final event. The proxy caches
      // on (battle_id, turn, rqid) so re-fires are cheap. Pass the matrix
      // top-cells summary so the LLM can spot engine/matrix conflicts.
      if (u.event === 'final' && record) {
        const room = win.app?.curRoom;
        const b = room?.battle;
        const br = room;
        if (b && br?.id) {
          explainerLoading = true;
          fetchExplanation({
            proxyUrl: PROXY_BASE_URL,
            battleId: br.id,
            turn: b.turn,
            rqid: record.rqid ?? 0,
            snapshot: snapshotState(b),
            engineResult: u,
            lastSteps: (b.stepQueue || []).slice(-12),
            matrixSummary: buildMatrixSummary(),
          }).then(text => {
            lastExplanation = text;
            explainerLoading = false;
          });
        }
      }
    }

    async function requestAnalysis(payload: any, record: DecisionRecord | null) {
      if (abortCtrl) abortCtrl.abort();
      abortCtrl = new AbortController();
      const myCtrl = abortCtrl;
      hdrEl.textContent = 'Copilot — analyzing…';
      try {
        const resp = await fetch(ENGINE_STREAM_URL, {
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
        showStatusOverlay(`Error: ${e.message || 'fetch failed'}`, 'error');
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

          fetchBeliefSnapshot(PROXY_BASE_URL, br?.id || '').then((snap: any) => {
            if (snap) lastBeliefSnapshot = snap;
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
            showStatusOverlay('LEAD PICK: pick a lead — numbers below', 'info');
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
            showStatusOverlay('LEAD PICK: belief fetch failed', 'error');
            statsEl.textContent = `${myTeam.length} v ${oppTeam.length} (belief fetch failed)`;
          });

          // Refresh the damage matrix so it tracks the new battle, not the
          // prior one (fixes the stale-from-previous-battle bug). Threats
          // refreshes after matrix completes (fire-and-forget chain) so it
          // sees the new lastMatrix — the new threats panel reads
          // `lastThreats` on every engine update so we always refresh.
          refreshMatrix(b, br).then(() => refreshThreats(b));

          lastKey = key;
        } else {
          hdrEl.textContent = 'Copilot — team preview';
          showStatusOverlay('LEAD PICK: waiting for opponent preview…', 'info');
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
        // Refresh damage matrix + threats on every decision: the new
        // threats panel always reads `lastThreats` and the TCG card
        // bottom strip surfaces the worst-case threat.
        refreshMatrix(b, br).then(() => refreshThreats(b));
      } catch (e: any) {
        console.error('[sc] translate error', e);
        hdrEl.textContent = 'Copilot — translate error';
        showStatusOverlay(`Error: ${e.message || 'bad state'}`, 'error');
        lastKey = key;
      }
    }, POLL_MS);

    console.log('[Showdown Copilot] extension loaded — world=MAIN, fetch-based');
  },
});
