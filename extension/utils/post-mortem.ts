// Pure parser: joins scHistory DecisionRecords to Showdown battle.stepQueue
// events and produces a compact per-battle post-mortem.
// No DOM, no globals, no side effects — safe to import into Vitest.

export const POSTMORTEM_SCHEMA_VERSION = 11 as const;

export type ConflictWarningSnapshot = {
  level: 'strong' | 'warn' | 'pivot' | 'info';
  message: string;
};

export type OverrideTag =
  | 'item_assumption'      // engine wrong about opp item (e.g. assumed CB, was Scarf)
  | 'speed_assumption'     // wrong speed tier
  | 'ability_missed'       // wrong ability (e.g. missed HA Multiscale)
  | 'set_unusual'          // opp ran an off-meta set
  | 'long_term'            // engine optimized too short-term
  | 'engine_correct'       // I overrode but engine was actually right
  | 'other';

export type DecisionRecordInput = {
  battleId: string;
  turn: number;
  rqid: number;
  tStartMs: number;
  tEndMs?: number;
  forceSwitch: boolean;
  state?: unknown;
  updates?: any[];
  final?: {
    bestMove?: string;
    confidence?: number;
    sims?: number;
    depth?: number;
    pv?: string[];
    alternatives?: { move: string; confidence: number; note?: string }[];
    message?: string;
    pimcConsensus?: any;
    pimcBreakdown?: any[];
    event?: string;
    error?: string;
  } | null;
};

export type ParseMeta = {
  battleId: string;
  format: string;
  myUsername: string;
  mySideId: 'p1' | 'p2';
  opponent: string;
};

export type MoveOutcome = {
  move: string;
  targetSpecies: string;
  hpPctBefore: number | null;
  hpPctAfter: number | null;
  superEffective: boolean;
  resisted: boolean;
  crit: boolean;
  missed: boolean;
  immune: boolean;
  failed: boolean;
};

export type ActualMyAction = {
  kind: 'move' | 'switch' | 'prevented' | 'unknown';
  name: string | null;
  reason?: string | null;
};

export type ResidualCategory = 'hazard' | 'status' | 'contact' | 'item' | 'shield' | 'other';

export type ResidualEvent = {
  side: 'mine' | 'opp';
  source: string;           // raw from-tag text, e.g. "item: Rocky Helmet", "psn", "Leech Seed"
  category: ResidualCategory;
  hpPctLost: number;        // positive = damage, negative = heal
  targetSpecies: string;
};

export type HpTimelineEntry = {
  eventIndex: number;
  position: string;         // e.g. "p1a", "p2a"
  hpPct: number | null;
  event: 'switch' | 'damage' | 'heal' | 'faint';
};

export type PreBattleState = {
  hpTimeline: HpTimelineEntry[];
  teamPreview: { mine: string[]; opp: string[] } | null;
  startedAtMs: number | null;
};

export type PokemonFieldPressureStats = {
  totalHpLost: number;
  hazardHpLost: number;
  statusHpLost: number;
  contactHpLost: number;
  itemHpLost: number;
  shieldHpLost: number;
  otherHpLost: number;
  events: number;
};

export type PokemonKoCreditStats = {
  directKos: number;
  delayedMoveKos: number;
  hazardKos: number;
  statusKos: number;
  contactKos: number;
  pressureKos: number;
  unknownKos: number;
};

export type PokemonBattleStats = {
  species: string;
  led: boolean;
  switchIns: number;
  forcedSwitchIns: number;
  activeTurns: number;
  fainted: boolean;
  faintTurn: number | null;
  survived: boolean;
  actionPreventedCount: number;
  directDamageTakenPct: number;
  directDamageDealtPct: number;
  hpHealedPct: number;
  kos: number;
  koCredit: PokemonKoCreditStats;
  timesTargeted: number;
  fieldPressure: PokemonFieldPressureStats;
  decisionTurns: number;
  engineDisagreements: number;
  highConfidenceDisagreements: number;
  engineWantedSwitchIntoCount: number;
  engineWantedSwitchOutCount: number;
};

export type TeamPerformance = {
  mine: {
    lead: string | null;
    pokemon: Record<string, PokemonBattleStats>;
    caveats: string[];
  };
  opp: {
    lead: string | null;
  };
};

export type RegularTurnDiff = {
  turn: number;
  forceSwitch: false;
  rqid: number;
  myPick: {
    kind: 'move' | 'switch';
    name: string | null;
    confidence: number | null;
    sims: number | null;
    depth: number | null;
    pv: string[];
    message?: string | null;
    pimcConsensus?: any | null;
    pimcBreakdown?: any[];
  };
  actualMyAction: ActualMyAction;
  enginePredictedOpp: string | null;
  actualOppMove: string | null;
  pvMatchedReality: boolean | null;
  damageIDealt: MoveOutcome | null;
  damageOppDealt: MoveOutcome | null;
  hazardsAdded: { side: 'mine' | 'opp'; name: string }[];
  hazardsRemoved: { side: 'mine' | 'opp'; name: string }[];
  faints: { side: 'mine' | 'opp'; species: string }[];
  failureMessages: string[];
  residualEvents: ResidualEvent[];
  userNote: string | null;
  userOverrideTag: OverrideTag | null;
  conflictWarning: ConflictWarningSnapshot | null;
  beliefSnapshot: any | null;
  matrixSummary: any | null;
  // engineUpdates: convergence summary only, NOT raw streaming events.
  // Storing all updates blows past Chrome's 5MB localStorage cap (~30MB
  // for a 30-turn battle, 77k events). The same raw data lives in
  // /tmp/engine.log keyed by (battle_id, turn) for full-fidelity analysis.
  // Summary captures the MCTS convergence pattern: how many distinct
  // bestMove values appeared during the search (flipCount), the unique
  // sequence of bestMoves (capped to 6), and total event count.
  engineUpdates: { flipCount: number; sequence: string[]; eventCount: number };
};

export type ForceSwitchTurnDiff = {
  turn: number;
  forceSwitch: true;
  rqid: number;
  myPick: {
    kind: 'switch';
    name: string | null;
    confidence: number | null;
    sims: number | null;
    depth: number | null;
    pv: string[];
    message?: string | null;
    pimcConsensus?: any | null;
    pimcBreakdown?: any[];
  };
  actualMyAction: ActualMyAction;
  faintedBefore: { species: string; cause: string | null } | null;
  switchInTook: { hpPctLost: number; from: string } | null;
  residualEvents: ResidualEvent[];
  userNote: string | null;
  userOverrideTag: OverrideTag | null;
  conflictWarning: ConflictWarningSnapshot | null;
  beliefSnapshot: any | null;
  matrixSummary: any | null;
  // engineUpdates: convergence summary only, NOT raw streaming events.
  // Storing all updates blows past Chrome's 5MB localStorage cap (~30MB
  // for a 30-turn battle, 77k events). The same raw data lives in
  // /tmp/engine.log keyed by (battle_id, turn) for full-fidelity analysis.
  // Summary captures the MCTS convergence pattern: how many distinct
  // bestMove values appeared during the search (flipCount), the unique
  // sequence of bestMoves (capped to 6), and total event count.
  engineUpdates: { flipCount: number; sequence: string[]; eventCount: number };
};

export type TurnDiff = RegularTurnDiff | ForceSwitchTurnDiff;

export type BattlePostMortem = {
  schemaVersion: typeof POSTMORTEM_SCHEMA_VERSION;
  battleId: string;
  format: string;
  myUsername: string;
  mySideId: 'p1' | 'p2';
  opponent: string;
  winner: string | null;
  totalTurns: number;
  startedAtMs: number | null;
  endedAtMs: number;
  teamPreview: { mine: string[]; opp: string[] } | null;
  teamPerformance: TeamPerformance;
  turns: TurnDiff[];
  battleNote: string | null;
  replayUrl: string | null;
};

export function parseBattlePostMortem(
  records: DecisionRecordInput[],
  stepQueue: string[],
  meta: ParseMeta,
): BattlePostMortem {
  const turnBlocks = partitionByTurn(stepQueue);
  const winner = extractWinner(stepQueue);
  const totalTurns = [...turnBlocks.keys()].filter(k => k > 0).reduce((a, b) => Math.max(a, b), 0);
  records = dedupeRecords(records);
  const pre = buildPreBattleState(stepQueue, meta.mySideId);
  const turnEvents = new Map<number, TurnEvents>();
  for (const [turn, block] of turnBlocks) {
    if (turn <= 0) continue;
    turnEvents.set(turn, extractTurnEvents(turn, block, meta.mySideId, pre.hpTimeline));
  }
  const turns: TurnDiff[] = [];
  // Track in-turn force-switch consumption: walk faints on my side in order.
  const forceSwitchCursor = new Map<number, number>(); // turn -> next faint index to consume
  for (const r of records) {
    const te = turnEvents.get(r.turn);
    if (!te) continue;
    if (!r.forceSwitch) {
      turns.push(buildRegularTurnDiff(r, te, pre.teamPreview?.mine ?? []));
    } else {
      const cursor = forceSwitchCursor.get(r.turn) ?? 0;
      const myFaints = te.faints.filter(f => f.side === 'mine');
      const fainted = myFaints[cursor] ?? null;
      forceSwitchCursor.set(r.turn, cursor + 1);
      const blockLines = turnBlocks.get(r.turn)?.lines.map(e => e.line) || [];
      const cause = fainted ? findCauseOfMyFaint(cursor, blockLines, meta.mySideId) : null;
      const switchInTook = findHazardDamageOnSwitchIn(blockLines, meta.mySideId, cursor);
      const actualSwitchName =
        findMySwitchAfterFaint(cursor, blockLines, meta.mySideId) ??
        te.mySwitches.filter(s => s.afterMyFaint)[cursor]?.species ??
        null;
      turns.push(buildForceSwitchTurnDiff(
        r,
        fainted,
        cause,
        actualSwitchName,
        switchInTook,
        te.residualEvents,
      ));
    }
  }
  const teamPerformance = buildTeamPerformance(stepQueue, meta.mySideId, pre.teamPreview, turns);
  return {
    schemaVersion: POSTMORTEM_SCHEMA_VERSION,
    battleId: meta.battleId,
    format: meta.format,
    myUsername: meta.myUsername,
    mySideId: meta.mySideId,
    opponent: meta.opponent,
    winner,
    totalTurns,
    startedAtMs: pre.startedAtMs,
    endedAtMs: Date.now(),
    teamPreview: pre.teamPreview,
    teamPerformance,
    turns: sortTurnDiffsForExport(turns),
    battleNote: null,
    replayUrl: null,
  };
}

// ---- internal types + helpers ----

type TurnEvents = {
  turn: number;
  myMove: MoveInstance | null;
  oppMove: MoveInstance | null;
  mySwitches: SwitchInstance[];
  myPreventedAction: PreventedAction | null;
  faints: { side: 'mine' | 'opp'; species: string }[];
  hazardsAdded: { side: 'mine' | 'opp'; name: string }[];
  hazardsRemoved: { side: 'mine' | 'opp'; name: string }[];
  hints: string[];
  residualEvents: ResidualEvent[];
};

type MoveInstance = {
  move: string;
  attackerSide: 'mine' | 'opp';
  targetSpecies: string;
  hpPctBefore: number | null;
  hpPctAfter: number | null;
  superEffective: boolean;
  resisted: boolean;
  crit: boolean;
  missed: boolean;
  immune: boolean;
  failed: boolean;
};

type SwitchInstance = {
  species: string;
  afterMyFaint: boolean;
};

type PreventedAction = {
  species: string;
  reason: string;
};

type ActiveTimeline = {
  activeAtTurnStart: Map<number, Set<string>>;
};

type KoCause =
  | { kind: 'direct'; attackerSpecies: string | null; source: string | null }
  | { kind: 'delayed'; attackerSpecies: string | null; source: string | null }
  | { kind: 'hazard' | 'status' | 'contact' | 'other'; source: string | null; creditedSpecies: string | null }
  | { kind: 'unknown'; source: string | null };

type PressureCandidate = {
  species: string;
  turn: number;
  hpPctLost: number;
};

type TurnBlock = { startIdx: number; lines: { idx: number; line: string }[] };

function partitionByTurn(stepQueue: string[]): Map<number, TurnBlock> {
  const out = new Map<number, TurnBlock>();
  let current = 0;
  let block: TurnBlock = { startIdx: 0, lines: [] };
  out.set(0, block);
  for (let idx = 0; idx < stepQueue.length; idx++) {
    const line = stepQueue[idx];
    const m = line.match(/^\|turn\|(\d+)/);
    if (m) {
      current = Number(m[1]);
      if (!out.has(current)) {
        block = { startIdx: idx, lines: [] };
        out.set(current, block);
      } else {
        block = out.get(current)!;
      }
      continue;
    }
    out.get(current)!.lines.push({ idx, line });
  }
  return out;
}

function extractWinner(stepQueue: string[]): string | null {
  for (const line of stepQueue) {
    const mw = line.match(/^\|win\|(.+)$/);
    if (mw) return mw[1];
    if (line === '|tie' || line.startsWith('|tie|')) return null;
  }
  return null;
}

function classifySide(token: string, mySideId: 'p1' | 'p2'): 'mine' | 'opp' | null {
  const m = token.match(/^(p[12])[ab]?:/);
  if (!m) return null;
  return m[1] === mySideId ? 'mine' : 'opp';
}

function extractPosition(token: string): string | null {
  // "p1a: Species" -> "p1a"; "p1: Name" -> "p1"
  const m = token.match(/^(p[12][ab]?)/);
  return m ? m[1] : null;
}

function classifySideBase(token: string, mySideId: 'p1' | 'p2'): 'mine' | 'opp' | null {
  const m = token.match(/^(p[12])\b/);
  if (!m) return null;
  return m[1] === mySideId ? 'mine' : 'opp';
}

function parseHpPct(hpToken: string | undefined): number | null {
  if (!hpToken) return null;
  if (hpToken.includes('fnt')) return 0;
  const slash = hpToken.split(' ')[0];
  const nm = slash.split('/');
  if (nm.length === 2) {
    const n = Number(nm[0]);
    const d = Number(nm[1]);
    if (!isFinite(n) || !isFinite(d) || d === 0) return null;
    return Math.round((n / d) * 100);
  }
  const v = Number(slash);
  return isFinite(v) ? v : null;
}

function speciesFromToken(token: string): string {
  // "p1a: OppMon" -> "OppMon"
  const idx = token.indexOf(': ');
  return idx >= 0 ? token.slice(idx + 2) : token;
}

function speciesFromSwitchDetails(token: string): string | null {
  const species = speciesFromPokeLine(token || '');
  return species || null;
}

function extractTurnEvents(
  turn: number,
  block: TurnBlock,
  mySideId: 'p1' | 'p2',
  hpTimeline: HpTimelineEntry[],
): TurnEvents {
  const te: TurnEvents = {
    turn, myMove: null, oppMove: null, mySwitches: [], myPreventedAction: null,
    faints: [], hazardsAdded: [], hazardsRemoved: [], hints: [], residualEvents: [],
  };
  let lastMove: MoveInstance | null = null;
  let mineFaintsSeen = 0;
  for (const { idx: currentEventIndex, line } of block.lines) {
    const parts = line.split('|').slice(1); // drop leading '' from split
    const tag = parts[0];
    if (tag === 'move') {
      const attacker = parts[1] || '';
      const moveName = parts[2] || '';
      const target = parts[3] || '';
      const side = classifySide(attacker, mySideId);
      if (!side) continue;
      const mi: MoveInstance = {
        move: moveName,
        attackerSide: side,
        targetSpecies: speciesFromToken(target),
        hpPctBefore: null,
        hpPctAfter: null,
        superEffective: false,
        resisted: false,
        crit: false,
        missed: false,
        immune: false,
        failed: false,
      };
      if (side === 'mine') te.myMove = mi;
      else te.oppMove = mi;
      lastMove = mi;
    } else if (tag === 'switch') {
      const side = classifySide(parts[1] || '', mySideId);
      if (side === 'mine') {
        const species = speciesFromSwitchDetails(parts[2] || '');
        if (species) te.mySwitches.push({ species, afterMyFaint: mineFaintsSeen > 0 });
      }
    } else if (tag === '-damage' || tag === '-heal') {
      const victim = parts[1] || '';
      const hpToken = parts[2];
      const side = classifySide(victim, mySideId);
      if (!side) continue;
      const fromMatch = line.match(/\[from\]\s*([^|]+)/);

      if (fromMatch) {
        const rawSource = fromMatch[1].trim();
        const pos = extractPosition(victim);
        const prevHp = pos ? lookupHpBefore(hpTimeline, currentEventIndex, pos) : null;
        const newHp = parseHpPct(hpToken) ?? 0;
        const delta = (prevHp ?? 100) - newHp;
        te.residualEvents.push({
          side,
          source: rawSource,
          category: categorizeResidual(rawSource),
          hpPctLost: tag === '-damage' ? delta : -Math.abs(delta),
          targetSpecies: speciesFromToken(victim),
        });
      } else if (tag === '-damage' && lastMove && side !== lastMove.attackerSide) {
        if (lastMove.hpPctBefore == null) {
          const pos = extractPosition(victim);
          const prevHp = pos ? lookupHpBefore(hpTimeline, currentEventIndex, pos) : null;
          lastMove.hpPctBefore = prevHp ?? 100;
        }
        lastMove.hpPctAfter = parseHpPct(hpToken);
      }
    } else if (tag === 'faint') {
      const victim = parts[1] || '';
      const side = classifySide(victim, mySideId);
      if (side) {
        te.faints.push({ side, species: speciesFromToken(victim) });
        if (side === 'mine') mineFaintsSeen++;
      }
    } else if (tag === '-supereffective') {
      if (lastMove) lastMove.superEffective = true;
    } else if (tag === '-resisted') {
      if (lastMove) lastMove.resisted = true;
    } else if (tag === '-crit') {
      if (lastMove) lastMove.crit = true;
    } else if (tag === '-miss') {
      // |-miss|attacker|target — flag the attacker's last move as missed
      const attacker = parts[1] || '';
      const side = classifySide(attacker, mySideId);
      const target = side === 'mine' ? te.myMove : side === 'opp' ? te.oppMove : null;
      if (target) target.missed = true;
    } else if (tag === '-immune') {
      // |-immune|victim — flag the attacker's last move as immune
      const victim = parts[1] || '';
      const victimSide = classifySide(victim, mySideId);
      // The attacker is the OTHER side's last move.
      const attackerMove = victimSide === 'mine' ? te.oppMove : victimSide === 'opp' ? te.myMove : null;
      if (attackerMove) attackerMove.immune = true;
    } else if (tag === '-fail') {
      // |-fail|attacker — flag attacker's last move as failed
      const attacker = parts[1] || '';
      const side = classifySide(attacker, mySideId);
      const attackerMove = side === 'mine' ? te.myMove : side === 'opp' ? te.oppMove : null;
      if (attackerMove) attackerMove.failed = true;
      const reason = parts.slice(2).join('|');
      te.hints.push(`fail: ${attacker}${reason ? `: ${reason}` : ''}`);
    } else if (tag === 'cant') {
      const who = parts[1] || '';
      const side = classifySide(who, mySideId);
      const reason = formatCantReason(parts.slice(2));
      if (side === 'mine') {
        te.myPreventedAction = {
          species: speciesFromToken(who),
          reason,
        };
      }
      if (side) {
        te.hints.push(`cant: ${speciesFromToken(who)}${reason ? `: ${reason}` : ''}`);
      }
    } else if (tag === '-sidestart') {
      const sideToken = parts[1] || '';
      const rawName = (parts[2] || '').replace(/^move:\s*/, '');
      const side = classifySideBase(sideToken, mySideId);
      if (side && rawName) te.hazardsAdded.push({ side, name: rawName });
    } else if (tag === '-sideend') {
      const sideToken = parts[1] || '';
      const rawName = (parts[2] || '').replace(/^move:\s*/, '');
      const side = classifySideBase(sideToken, mySideId);
      if (side && rawName) te.hazardsRemoved.push({ side, name: rawName });
    } else if (tag === 'hint') {
      const text = parts.slice(1).join('|');
      if (text) te.hints.push(text);
    } else if (tag === '-activate') {
      const who = parts[1] || '';
      const effect = parts[2] || '';
      if (effect.startsWith('move: Protect') ||
          effect.startsWith('move: Detect') ||
          effect.startsWith('move: Spiky Shield') ||
          effect.startsWith('move: Baneful Bunker') ||
          effect.startsWith("move: King's Shield") ||
          effect.startsWith('ability: Disguise')) {
        te.hints.push(`${effect} by ${speciesFromToken(who)}`);
      }
    }
  }
  return te;
}

function observedMyAction(te: TurnEvents): ActualMyAction {
  if (te.myMove) return { kind: 'move', name: te.myMove.move };
  const voluntarySwitch = te.mySwitches.find(s => !s.afterMyFaint);
  if (voluntarySwitch) return { kind: 'switch', name: voluntarySwitch.species };
  if (te.myPreventedAction) {
    return {
      kind: 'prevented',
      name: null,
      reason: te.myPreventedAction.reason || null,
    };
  }
  const faintReason = inferMineFaintedBeforeActionReason(te);
  if (faintReason) {
    return { kind: 'prevented', name: null, reason: faintReason };
  }
  return { kind: 'unknown', name: null };
}

function formatCantReason(parts: string[]): string {
  const raw = parts.filter(Boolean).join('|').trim();
  if (!raw) return 'prevented';
  return raw.replace(/^move:\s*/, '').replace(/^ability:\s*/, '').replace(/^item:\s*/, '');
}

function inferMineFaintedBeforeActionReason(te: TurnEvents): string | null {
  const mineFaint = te.faints.find(f => f.side === 'mine');
  if (!mineFaint) return null;
  const lethalResidual = te.residualEvents.find(e =>
    e.side === 'mine' &&
    normalizeMoveName(e.targetSpecies) === normalizeMoveName(mineFaint.species) &&
    e.hpPctLost > 0);
  if (lethalResidual) return `fainted before action (${lethalResidual.source})`;
  if (te.oppMove?.move) return `fainted before action (${te.oppMove.move})`;
  return 'fainted before action';
}

function parseThemToken(pvEntry: string | undefined): string | null {
  if (!pvEntry) return null;
  const m = pvEntry.match(/\bthem=(\S+(?:\s\S+)*?)$/);
  return m ? m[1] : null;
}

function normalizeMoveName(s: string | null | undefined): string | null {
  if (!s) return null;
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}

function compareMoves(a: string | null, b: string | null): boolean | null {
  if (a == null || b == null) return null;
  const na = normalizeMoveName(a);
  const nb = normalizeMoveName(b);
  return !!na && !!nb && na === nb;
}

function inferRegularPickKind(
  bestMove: string | null | undefined,
  myTeamSpecies: string[],
): 'move' | 'switch' {
  const normalizedPick = normalizeMoveName(bestMove);
  if (!normalizedPick) return 'move';
  return myTeamSpecies.some(species => normalizeMoveName(species) === normalizedPick)
    ? 'switch'
    : 'move';
}

function buildRegularTurnDiff(
  r: DecisionRecordInput,
  te: TurnEvents,
  myTeamSpecies: string[],
): RegularTurnDiff {
  const pv = r.final?.pv ?? [];
  const enginePredictedOpp = parseThemToken(pv[0]);
  const actualOppMove = te.oppMove?.move ?? null;
  const bestMove = r.final?.bestMove ?? null;
  return {
    turn: r.turn,
    forceSwitch: false,
    rqid: r.rqid,
    myPick: {
      kind: inferRegularPickKind(bestMove, myTeamSpecies),
      name: bestMove,
      confidence: r.final?.confidence ?? null,
      sims: r.final?.sims ?? null,
      depth: r.final?.depth ?? null,
      pv,
      message: r.final?.message ?? null,
      pimcConsensus: r.final?.pimcConsensus ?? null,
      pimcBreakdown: Array.isArray(r.final?.pimcBreakdown) ? r.final?.pimcBreakdown : [],
    },
    actualMyAction: observedMyAction(te),
    enginePredictedOpp,
    actualOppMove,
    pvMatchedReality: compareMoves(enginePredictedOpp, actualOppMove),
    damageIDealt: te.myMove ? moveInstanceToOutcome(te.myMove) : null,
    damageOppDealt: te.oppMove ? moveInstanceToOutcome(te.oppMove) : null,
    hazardsAdded: [...te.hazardsAdded],
    hazardsRemoved: [...te.hazardsRemoved],
    faints: [...te.faints],
    failureMessages: [...te.hints],
    residualEvents: [...te.residualEvents],
    userNote: null,
    userOverrideTag: null,
    conflictWarning: null,
    beliefSnapshot: null,
    matrixSummary: null,
    engineUpdates: summarizeEngineUpdates(r.updates),
  };
}

function moveInstanceToOutcome(mi: MoveInstance): MoveOutcome {
  return {
    move: mi.move,
    targetSpecies: mi.targetSpecies,
    hpPctBefore: mi.hpPctBefore,
    hpPctAfter: mi.hpPctAfter,
    superEffective: mi.superEffective,
    resisted: mi.resisted,
    crit: mi.crit,
    missed: mi.missed,
    immune: mi.immune,
    failed: mi.failed,
  };
}

function findCauseOfMyFaint(cursor: number, block: string[], mySideId: 'p1' | 'p2'): string | null {
  // Walk the block tracking direct move damage and lethal residual ticks.
  // If the faint follows poison/burn/hazard/weather/item residual damage,
  // prefer that immediate source over the last opponent move.
  let lastOppMove: string | null = null;
  let lastMineResidualFaint: { position: string | null; cause: string } | null = null;
  let mineFaintsSeen = 0;
  for (const line of block) {
    if (line.startsWith('|move|')) {
      const parts = line.split('|').slice(1);
      const attacker = parts[1] || '';
      const side = classifySide(attacker, mySideId);
      if (side === 'opp') lastOppMove = parts[2] || null;
    } else if (line.startsWith('|-damage|')) {
      const parts = line.split('|').slice(1);
      const victim = parts[1] || '';
      if (classifySide(victim, mySideId) !== 'mine') continue;
      const source = extractFromSource(line);
      if (source && parseHpPct(parts[2]) === 0) {
        lastMineResidualFaint = {
          position: extractPosition(victim),
          cause: formatFaintCauseSource(source),
        };
      } else if (parseHpPct(parts[2]) === 0) {
        lastMineResidualFaint = null;
      }
    } else if (line.startsWith('|faint|')) {
      const parts = line.split('|').slice(1);
      const victim = parts[1] || '';
      if (classifySide(victim, mySideId) === 'mine') {
        if (mineFaintsSeen === cursor) {
          const victimPosition = extractPosition(victim);
          if (
            lastMineResidualFaint &&
            (!lastMineResidualFaint.position || lastMineResidualFaint.position === victimPosition)
          ) {
            return lastMineResidualFaint.cause;
          }
          return lastOppMove;
        }
        mineFaintsSeen++;
      }
    }
  }
  return null;
}

function extractFromSource(line: string): string | null {
  const match = line.match(/\[from\]\s*([^|]+)/);
  return match ? match[1].trim() : null;
}

function formatFaintCauseSource(source: string): string {
  return source.replace(/^move:\s*/, '');
}

function findMySwitchAfterFaint(cursor: number, block: string[], mySideId: 'p1' | 'p2'): string | null {
  let mineFaintsSeen = 0;
  let waitingForSwitch = false;
  for (const line of block) {
    if (waitingForSwitch && line.startsWith('|switch|')) {
      const parts = line.split('|').slice(1);
      if (classifySide(parts[1] || '', mySideId) === 'mine') {
        return speciesFromSwitchDetails(parts[2] || '');
      }
    } else if (line.startsWith('|faint|')) {
      const parts = line.split('|').slice(1);
      const victim = parts[1] || '';
      if (classifySide(victim, mySideId) === 'mine') {
        if (mineFaintsSeen === cursor) waitingForSwitch = true;
        mineFaintsSeen++;
      }
    }
  }
  return null;
}

function findHazardDamageOnSwitchIn(block: string[], mySideId: 'p1' | 'p2', cursor: number): { hpPctLost: number; from: string } | null {
  // Find my N-th switch-in (N = cursor, zero-indexed relative to faint cursor),
  // then look at the next |-damage| with a [from] hazard tag on my side.
  let mySwitchIns = 0;
  for (let i = 0; i < block.length; i++) {
    const line = block[i];
    if (line.startsWith('|switch|')) {
      const parts = line.split('|').slice(1);
      const pos = parts[1] || '';
      if (classifySide(pos, mySideId) === 'mine') {
        if (mySwitchIns === cursor) {
          // Peek at next damage line.
          for (let j = i + 1; j < block.length; j++) {
            const next = block[j];
            if (next.startsWith('|-damage|')) {
              const np = next.split('|').slice(1);
              const victim = np[1] || '';
              if (classifySide(victim, mySideId) === 'mine') {
                const hpAfter = parseHpPct(np[2]);
                const fromMatch = next.match(/\[from\]\s*([^|]+)/);
                if (fromMatch && hpAfter != null) {
                  const name = fromMatch[1].trim();
                  const before = parseSwitchInHpBefore(line);
                  if (before != null) {
                    return { hpPctLost: before - hpAfter, from: name };
                  }
                }
              }
              break;
            }
            if (next.startsWith('|move|') || next.startsWith('|turn|')) break;
          }
        }
        mySwitchIns++;
      }
    }
  }
  return null;
}

function parseSwitchInHpBefore(switchLine: string): number | null {
  // |switch|p2a: Name|Species|87/100 — last field is HP/Max
  const parts = switchLine.split('|').slice(1);
  return parseHpPct(parts[3]);
}

function buildForceSwitchTurnDiff(
  r: DecisionRecordInput,
  fainted: { side: 'mine' | 'opp'; species: string } | null,
  cause: string | null,
  actualSwitchName: string | null,
  switchInTook: { hpPctLost: number; from: string } | null,
  residualEvents: ResidualEvent[],
): ForceSwitchTurnDiff {
  const pv = r.final?.pv ?? [];
  return {
    turn: r.turn,
    forceSwitch: true,
    rqid: r.rqid,
    myPick: {
      kind: 'switch',
      name: r.final?.bestMove ?? null,
      confidence: r.final?.confidence ?? null,
      sims: r.final?.sims ?? null,
      depth: r.final?.depth ?? null,
      pv,
      message: r.final?.message ?? null,
      pimcConsensus: r.final?.pimcConsensus ?? null,
      pimcBreakdown: Array.isArray(r.final?.pimcBreakdown) ? r.final?.pimcBreakdown : [],
    },
    actualMyAction: { kind: actualSwitchName ? 'switch' : 'unknown', name: actualSwitchName },
    faintedBefore: fainted ? { species: fainted.species, cause } : null,
    switchInTook,
    residualEvents: [...residualEvents],
    userNote: null,
    userOverrideTag: null,
    conflictWarning: null,
    beliefSnapshot: null,
    matrixSummary: null,
    engineUpdates: summarizeEngineUpdates(r.updates),
  };
}

// Compress engine streaming events into a tiny convergence summary so the
// postmortem JSON fits in Chrome's 5MB localStorage cap. Raw events live in
// /tmp/engine.log; here we just track WHAT the bestMove sequence looked like.
function summarizeEngineUpdates(updates: any[] | undefined): { flipCount: number; sequence: string[]; eventCount: number } {
  if (!Array.isArray(updates) || updates.length === 0) {
    return { flipCount: 0, sequence: [], eventCount: 0 };
  }
  const seq: string[] = [];
  for (const u of updates) {
    const bm = u?.bestMove;
    if (typeof bm === 'string' && bm && (seq.length === 0 || seq[seq.length - 1] !== bm)) {
      seq.push(bm);
    }
  }
  return {
    flipCount: Math.max(0, seq.length - 1),
    sequence: seq.slice(0, 6),  // cap so a flailing 30-turn search doesn't bloat
    eventCount: updates.length,
  };
}

function sortTurnDiffsForExport(turns: TurnDiff[]): TurnDiff[] {
  return turns
    .map((turn, index) => ({ turn, index }))
    .sort((a, b) => {
      if (a.turn.turn !== b.turn.turn) return a.turn.turn - b.turn.turn;
      const aKind = a.turn.forceSwitch ? 1 : 0;
      const bKind = b.turn.forceSwitch ? 1 : 0;
      if (aKind !== bKind) return aKind - bKind;
      return a.index - b.index;
    })
    .map(({ turn }) => turn);
}

function dedupeRecordKey(r: DecisionRecordInput): string {
  if (r.turn <= 0) return `preview:${r.rqid}`;
  if (!r.forceSwitch) return `${r.battleId}:turn:${r.turn}:regular`;
  return `${r.battleId}:turn:${r.turn}:force:${r.rqid}`;
}

function recordCompletenessRank(r: DecisionRecordInput): number {
  if (r.final?.bestMove) return 2;
  if (r.final) return 1;
  return 0;
}

function preferRecord(current: DecisionRecordInput, candidate: DecisionRecordInput): DecisionRecordInput {
  const currentRank = recordCompletenessRank(current);
  const candidateRank = recordCompletenessRank(candidate);
  if (candidateRank !== currentRank) {
    return candidateRank > currentRank ? candidate : current;
  }
  const currentTime = current.tEndMs ?? current.tStartMs;
  const candidateTime = candidate.tEndMs ?? candidate.tStartMs;
  if (candidateTime !== currentTime) {
    return candidateTime > currentTime ? candidate : current;
  }
  const currentUpdates = Array.isArray(current.updates) ? current.updates.length : 0;
  const candidateUpdates = Array.isArray(candidate.updates) ? candidate.updates.length : 0;
  if (candidateUpdates !== currentUpdates) {
    return candidateUpdates > currentUpdates ? candidate : current;
  }
  return candidate;
}

function dedupeRecords(records: DecisionRecordInput[]): DecisionRecordInput[] {
  // Showdown can produce multiple client request IDs for the same actual
  // regular turn before one move resolves. For analytics, that is one
  // decision row. Force-switch records are kept rqid-specific because a
  // single turn can legitimately require multiple replacement choices.
  const bestByDecision = new Map<string, DecisionRecordInput>();
  for (const r of records) {
    const key = dedupeRecordKey(r);
    const existing = bestByDecision.get(key);
    bestByDecision.set(key, existing ? preferRecord(existing, r) : r);
  }
  const out = [...bestByDecision.values()];
  // Preserve original chronological order by tStartMs so the main loop's
  // cursor logic (force-switch pairing) still sees records in-order.
  out.sort((a, b) => a.tStartMs - b.tStartMs);
  return out;
}

function speciesFromPokeLine(pokeValue: string): string {
  // "Charizard, M" -> "Charizard"
  // "Galvantula, M, shiny" -> "Galvantula"
  // "Urshifu-*, M" -> "Urshifu-*"
  return pokeValue.split(',')[0].trim();
}

function emptyFieldPressureStats(): PokemonFieldPressureStats {
  return {
    totalHpLost: 0,
    hazardHpLost: 0,
    statusHpLost: 0,
    contactHpLost: 0,
    itemHpLost: 0,
    shieldHpLost: 0,
    otherHpLost: 0,
    events: 0,
  };
}

function emptyKoCreditStats(): PokemonKoCreditStats {
  return {
    directKos: 0,
    delayedMoveKos: 0,
    hazardKos: 0,
    statusKos: 0,
    contactKos: 0,
    pressureKos: 0,
    unknownKos: 0,
  };
}

function emptyPokemonBattleStats(species: string): PokemonBattleStats {
  return {
    species,
    led: false,
    switchIns: 0,
    forcedSwitchIns: 0,
    activeTurns: 0,
    fainted: false,
    faintTurn: null,
    survived: true,
    actionPreventedCount: 0,
    directDamageTakenPct: 0,
    directDamageDealtPct: 0,
    hpHealedPct: 0,
    kos: 0,
    koCredit: emptyKoCreditStats(),
    timesTargeted: 0,
    fieldPressure: emptyFieldPressureStats(),
    decisionTurns: 0,
    engineDisagreements: 0,
    highConfidenceDisagreements: 0,
    engineWantedSwitchIntoCount: 0,
    engineWantedSwitchOutCount: 0,
  };
}

function ensurePokemonStats(
  pokemon: Record<string, PokemonBattleStats>,
  species: string | null | undefined,
): PokemonBattleStats | null {
  const key = (species || '').trim();
  if (!key) return null;
  if (!pokemon[key]) pokemon[key] = emptyPokemonBattleStats(key);
  return pokemon[key];
}

function resolvePokemonKey(
  pokemon: Record<string, PokemonBattleStats>,
  species: string | null | undefined,
): string | null {
  const raw = (species || '').trim();
  if (!raw) return null;
  if (pokemon[raw]) return raw;
  const normalized = normalizeMoveName(raw);
  const existing = Object.keys(pokemon).find(key => normalizeMoveName(key) === normalized);
  return existing ?? raw;
}

function roundPct(value: number): number {
  return Number(value.toFixed(1));
}

function addFieldPressureLoss(
  stats: PokemonBattleStats,
  category: ResidualCategory,
  hpLost: number,
): void {
  const loss = Math.max(0, hpLost);
  if (loss <= 0) return;
  stats.fieldPressure.totalHpLost = roundPct(stats.fieldPressure.totalHpLost + loss);
  stats.fieldPressure.events++;
  if (category === 'hazard') stats.fieldPressure.hazardHpLost = roundPct(stats.fieldPressure.hazardHpLost + loss);
  else if (category === 'status') stats.fieldPressure.statusHpLost = roundPct(stats.fieldPressure.statusHpLost + loss);
  else if (category === 'contact') stats.fieldPressure.contactHpLost = roundPct(stats.fieldPressure.contactHpLost + loss);
  else if (category === 'item') stats.fieldPressure.itemHpLost = roundPct(stats.fieldPressure.itemHpLost + loss);
  else if (category === 'shield') stats.fieldPressure.shieldHpLost = roundPct(stats.fieldPressure.shieldHpLost + loss);
  else stats.fieldPressure.otherHpLost = roundPct(stats.fieldPressure.otherHpLost + loss);
}

function effectKey(source: string | null | undefined): string {
  return normalizeMoveName((source || '').replace(/^move:\s*/, '').replace(/^item:\s*/, '')) || '';
}

function extractBracketValue(line: string, name: string): string | null {
  const match = line.match(new RegExp(`\\[${name}\\]\\s*([^|]+)`));
  return match ? match[1].trim() : null;
}

function addKoCredit(
  pokemon: Record<string, PokemonBattleStats>,
  species: string | null | undefined,
  kind: keyof PokemonKoCreditStats,
): void {
  const stats = ensurePokemonStats(pokemon, species);
  if (!stats) return;
  stats.koCredit[kind]++;
  if (kind === 'directKos' || kind === 'delayedMoveKos') {
    stats.kos++;
  }
}

function speciesForPositionOrToken(
  activeByPosition: Map<string, string>,
  token: string,
): string | null {
  const pos = extractPosition(token);
  return (pos ? activeByPosition.get(pos) : null) ?? speciesFromToken(token);
}

function markActiveTurn(
  activeTurnSets: Map<string, Set<number>>,
  species: string | null | undefined,
  turn: number,
): void {
  if (!species || turn <= 0) return;
  if (!activeTurnSets.has(species)) activeTurnSets.set(species, new Set());
  activeTurnSets.get(species)!.add(turn);
}

function collectActiveMineSpecies(
  activeByPosition: Map<string, string>,
  mySideId: 'p1' | 'p2',
): Set<string> {
  const out = new Set<string>();
  for (const [position, species] of activeByPosition) {
    if (position.startsWith(mySideId)) out.add(species);
  }
  return out;
}

function buildTeamPerformance(
  stepQueue: string[],
  mySideId: 'p1' | 'p2',
  teamPreview: { mine: string[]; opp: string[] } | null,
  turns: TurnDiff[],
): TeamPerformance {
  const pokemon: Record<string, PokemonBattleStats> = {};
  for (const species of teamPreview?.mine ?? []) ensurePokemonStats(pokemon, species);

  const activeByPosition = new Map<string, string>();
  const hpByPosition = new Map<string, number>();
  const activeTurnSets = new Map<string, Set<number>>();
  const activeAtTurnStart = new Map<number, Set<string>>();
  const pendingMineFaintPositions = new Set<string>();
  let currentTurn = 0;
  let mineLead: string | null = null;
  let oppLead: string | null = null;
  let lastMove: {
    attackerSide: 'mine' | 'opp';
    attackerSpecies: string | null;
    targetSide: 'mine' | 'opp' | null;
    targetSpecies: string | null;
    move: string | null;
  } | null = null;
  const hazardSetters = new Map<string, string>();
  const statusInflicters = new Map<string, string>();
  const delayedMoveSources = new Map<string, { species: string; move: string }>();
  const pendingDelayedDamageByPosition = new Map<string, { species: string; move: string }>();
  const lethalOppDamageByPosition = new Map<string, KoCause>();
  const pressureCandidatesByOppSpecies = new Map<string, PressureCandidate>();

  for (const line of stepQueue) {
    const parts = line.split('|').slice(1);
    const tag = parts[0];

    if (tag === 'turn') {
      currentTurn = Number(parts[1]) || currentTurn;
      const active = collectActiveMineSpecies(activeByPosition, mySideId);
      activeAtTurnStart.set(currentTurn, active);
      for (const species of active) markActiveTurn(activeTurnSets, species, currentTurn);
      continue;
    }

    if (tag === 'switch' || tag === 'drag') {
      const positionToken = parts[1] || '';
      const side = classifySide(positionToken, mySideId);
      const position = extractPosition(positionToken);
      const species = speciesFromSwitchDetails(parts[2] || '') ?? speciesFromToken(positionToken);
      const hp = parseHpPct(parts[3]);
      if (position) {
        activeByPosition.set(position, species);
        if (hp != null) hpByPosition.set(position, hp);
      }
      if (side === 'mine') {
        const stats = ensurePokemonStats(pokemon, species);
        if (stats) {
          stats.switchIns++;
          if (mineLead == null) {
            mineLead = species;
            stats.led = true;
          }
          if (position && pendingMineFaintPositions.has(position)) {
            stats.forcedSwitchIns++;
            pendingMineFaintPositions.delete(position);
          }
          markActiveTurn(activeTurnSets, species, currentTurn);
        }
      } else if (side === 'opp' && oppLead == null) {
        oppLead = species;
      }
      continue;
    }

    if (tag === 'move') {
      const attackerToken = parts[1] || '';
      const targetToken = parts[3] || '';
      const attackerSide = classifySide(attackerToken, mySideId);
      if (!attackerSide) continue;
      const attackerSpecies = speciesForPositionOrToken(activeByPosition, attackerToken);
      const targetSide = classifySide(targetToken, mySideId);
      const targetSpecies = speciesForPositionOrToken(activeByPosition, targetToken);
      lastMove = { attackerSide, attackerSpecies, targetSide, targetSpecies, move: parts[2] || null };
      if (attackerSide === 'opp' && targetSide === 'mine') {
        const stats = ensurePokemonStats(pokemon, targetSpecies);
        if (stats) stats.timesTargeted++;
      }
      continue;
    }

    if (tag === '-sidestart') {
      const sideToken = parts[1] || '';
      const rawName = (parts[2] || '').replace(/^move:\s*/, '');
      const side = classifySideBase(sideToken, mySideId);
      if (side === 'opp' && rawName && lastMove?.attackerSide === 'mine' && lastMove.attackerSpecies) {
        hazardSetters.set(`opp:${effectKey(rawName)}`, lastMove.attackerSpecies);
      }
      continue;
    }

    if (tag === '-status') {
      const victimToken = parts[1] || '';
      const status = parts[2] || '';
      const species = speciesForPositionOrToken(activeByPosition, victimToken);
      if (species && classifySide(victimToken, mySideId) === 'opp') {
        const ofToken = extractBracketValue(line, 'of');
        const creditedSpecies =
          ofToken && classifySide(ofToken, mySideId) === 'mine'
            ? speciesForPositionOrToken(activeByPosition, ofToken)
            : lastMove?.attackerSide === 'mine'
              ? lastMove.attackerSpecies
              : null;
        if (creditedSpecies && status) {
          statusInflicters.set(`${species}:${effectKey(status)}`, creditedSpecies);
        }
      }
      continue;
    }

    if (tag === '-start') {
      const who = parts[1] || '';
      const effect = parts[2] || '';
      if (effectKey(effect) === 'futuresight' && classifySide(who, mySideId) === 'mine') {
        const species = speciesForPositionOrToken(activeByPosition, who);
        if (species) delayedMoveSources.set('futuresight', { species, move: 'Future Sight' });
      }
      continue;
    }

    if (tag === '-end') {
      const targetToken = parts[1] || '';
      const effect = parts[2] || '';
      const position = extractPosition(targetToken);
      const delayed = delayedMoveSources.get(effectKey(effect));
      if (position && delayed) pendingDelayedDamageByPosition.set(position, delayed);
      continue;
    }

    if (tag === '-damage' || tag === '-heal') {
      const victimToken = parts[1] || '';
      const side = classifySide(victimToken, mySideId);
      if (!side) continue;
      const position = extractPosition(victimToken);
      const species = speciesForPositionOrToken(activeByPosition, victimToken);
      const hpAfter = parseHpPct(parts[2]);
      const hpBefore = position ? hpByPosition.get(position) : null;
      if (position && hpAfter != null) hpByPosition.set(position, hpAfter);
      if (hpAfter == null) continue;

      if (tag === '-damage') {
        const delta = Math.max(0, (hpBefore ?? 100) - hpAfter);
        const fromSource = extractFromSource(line);
        if (side === 'mine') {
          const stats = ensurePokemonStats(pokemon, species);
          if (stats) {
            if (fromSource) addFieldPressureLoss(stats, categorizeResidual(fromSource), delta);
            else stats.directDamageTakenPct = roundPct(stats.directDamageTakenPct + delta);
          }
        } else {
          const delayed = position ? pendingDelayedDamageByPosition.get(position) : null;
          const directAttacker =
            delayed?.species ??
            (lastMove?.attackerSide === 'mine' ? lastMove.attackerSpecies : null);
          const stats = !fromSource ? ensurePokemonStats(pokemon, directAttacker) : null;
          if (stats) stats.directDamageDealtPct = roundPct(stats.directDamageDealtPct + delta);
          if (species && hpAfter > 0 && directAttacker && delta > 0) {
            pressureCandidatesByOppSpecies.set(species, {
              species: directAttacker,
              turn: currentTurn,
              hpPctLost: delta,
            });
          }
          if (position && hpAfter === 0) {
            if (fromSource) {
              const category = categorizeResidual(fromSource);
              const sourceKey = effectKey(fromSource);
              let cause: KoCause;
              if (category === 'hazard') {
                cause = {
                  kind: 'hazard',
                  source: fromSource,
                  creditedSpecies: hazardSetters.get(`opp:${sourceKey}`) ?? null,
                };
              } else if (category === 'status') {
                cause = {
                  kind: 'status',
                  source: fromSource,
                  creditedSpecies: statusInflicters.get(`${species}:${sourceKey}`) ?? null,
                };
              } else if (category === 'contact') {
                const ofToken = extractBracketValue(line, 'of');
                cause = {
                  kind: 'contact',
                  source: fromSource,
                  creditedSpecies: ofToken && classifySide(ofToken, mySideId) === 'mine'
                    ? speciesForPositionOrToken(activeByPosition, ofToken)
                    : null,
                };
              } else {
                cause = { kind: 'other', source: fromSource, creditedSpecies: null };
              }
              lethalOppDamageByPosition.set(position, cause);
            } else if (delayed) {
              lethalOppDamageByPosition.set(position, {
                kind: 'delayed',
                attackerSpecies: delayed.species,
                source: delayed.move,
              });
            } else {
              lethalOppDamageByPosition.set(position, {
                kind: directAttacker ? 'direct' : 'unknown',
                attackerSpecies: directAttacker,
                source: lastMove?.move ?? null,
              } as KoCause);
            }
          }
          if (position && delayed) pendingDelayedDamageByPosition.delete(position);
        }
      } else if (tag === '-heal' && side === 'mine') {
        const healed = Math.max(0, hpAfter - (hpBefore ?? hpAfter));
        const stats = ensurePokemonStats(pokemon, species);
        if (stats) stats.hpHealedPct = roundPct(stats.hpHealedPct + healed);
      }
      continue;
    }

    if (tag === 'cant') {
      // Decision-level prevented-action counts are applied from TurnDiffs
      // below so a |cant| line and its parsed actualMyAction do not double count.
      continue;
    }

    if (tag === 'faint') {
      const victimToken = parts[1] || '';
      const side = classifySide(victimToken, mySideId);
      const position = extractPosition(victimToken);
      const species = speciesForPositionOrToken(activeByPosition, victimToken);
      if (side === 'mine') {
        const stats = ensurePokemonStats(pokemon, species);
        if (stats) {
          stats.fainted = true;
          stats.survived = false;
          stats.faintTurn = currentTurn || null;
        }
        if (position) pendingMineFaintPositions.add(position);
      } else if (side === 'opp') {
        const cause = position ? lethalOppDamageByPosition.get(position) : null;
        if (!cause) {
          addKoCredit(
            pokemon,
            lastMove?.attackerSide === 'mine' ? lastMove.attackerSpecies : null,
            'directKos',
          );
        } else if (cause.kind === 'direct') {
          addKoCredit(pokemon, cause.attackerSpecies, 'directKos');
        } else if (cause.kind === 'delayed') {
          addKoCredit(pokemon, cause.attackerSpecies, 'delayedMoveKos');
        } else if (cause.kind === 'hazard') {
          addKoCredit(pokemon, cause.creditedSpecies, 'hazardKos');
        } else if (cause.kind === 'status') {
          addKoCredit(pokemon, cause.creditedSpecies, 'statusKos');
        } else if (cause.kind === 'contact') {
          addKoCredit(pokemon, cause.creditedSpecies, 'contactKos');
        } else {
          addKoCredit(pokemon, null, 'unknownKos');
        }
        if (cause && cause.kind !== 'direct' && cause.kind !== 'delayed' && position) {
          const pressure = species ? pressureCandidatesByOppSpecies.get(species) : null;
          const creditedSpecies = 'creditedSpecies' in cause ? cause.creditedSpecies : null;
          if (pressure && pressure.hpPctLost >= 20 && pressure.species !== creditedSpecies) {
            addKoCredit(pokemon, pressure.species, 'pressureKos');
          }
        }
      }
      if (position) {
        activeByPosition.delete(position);
        hpByPosition.set(position, 0);
      }
    }
  }

  applyDecisionStatsToTeamPerformance(pokemon, activeAtTurnStart, turns);

  for (const [species, stats] of Object.entries(pokemon)) {
    stats.activeTurns = activeTurnSets.get(species)?.size ?? 0;
    stats.survived = !stats.fainted;
  }

  return {
    mine: {
      lead: mineLead,
      pokemon,
      caveats: [
        'Damage totals use visible HP percentages from Showdown logs, not exact damage rolls.',
        'Active-turn and forced-switch stats are inferred from turn, switch, drag, and faint event order.',
        'Per-Pokemon aggregation assumes one copy of each species on the team.',
      ],
    },
    opp: {
      lead: oppLead,
    },
  };
}

function applyDecisionStatsToTeamPerformance(
  pokemon: Record<string, PokemonBattleStats>,
  activeAtTurnStart: ActiveTimeline['activeAtTurnStart'],
  turns: TurnDiff[],
): void {
  for (const turn of turns) {
    if (turn.forceSwitch) {
      const key = resolvePokemonKey(pokemon, turn.myPick.name);
      const stats = ensurePokemonStats(pokemon, key);
      if (stats) stats.engineWantedSwitchIntoCount++;
      continue;
    }

    const activeSpecies = activeAtTurnStart.get(turn.turn) ?? new Set<string>();
    const actionMatch = regularTurnActionMatch(turn);
    const confidence = turn.myPick.confidence ?? 0;

    if (turn.myPick.kind === 'switch') {
      const switchKey = resolvePokemonKey(pokemon, turn.myPick.name);
      const switchStats = ensurePokemonStats(pokemon, switchKey);
      if (switchStats) switchStats.engineWantedSwitchIntoCount++;
      for (const species of activeSpecies) {
        const stats = ensurePokemonStats(pokemon, species);
        if (stats) stats.engineWantedSwitchOutCount++;
      }
    }

    for (const species of activeSpecies) {
      const stats = ensurePokemonStats(pokemon, species);
      if (!stats) continue;
      stats.decisionTurns++;
      if (actionMatch === false) {
        stats.engineDisagreements++;
        if (confidence >= 0.65) stats.highConfidenceDisagreements++;
      }
      if (turn.actualMyAction.kind === 'prevented') {
        stats.actionPreventedCount++;
      }
    }
  }
}

function regularTurnActionMatch(turn: RegularTurnDiff): boolean | null {
  if (!turn.myPick.name || turn.actualMyAction.kind === 'unknown') return null;
  if (turn.actualMyAction.kind === 'prevented') return false;
  if (turn.myPick.kind !== turn.actualMyAction.kind) return false;
  if (!turn.actualMyAction.name) return null;
  return compareMoves(turn.myPick.name, turn.actualMyAction.name);
}

export function buildPreBattleState(stepQueue: string[], mySideId: 'p1' | 'p2'): PreBattleState {
  const mine: string[] = [];
  const opp: string[] = [];
  let startedAtMs: number | null = null;
  const hpTimeline: HpTimelineEntry[] = [];

  for (let idx = 0; idx < stepQueue.length; idx++) {
    const line = stepQueue[idx];
    const parts = line.split('|').slice(1);
    const tag = parts[0];

    if (tag === 'poke') {
      const side = parts[1] || '';
      const raw = parts[2] || '';
      const species = speciesFromPokeLine(raw);
      if (!species) continue;
      if (side === mySideId) mine.push(species);
      else opp.push(species);
    } else if (tag === 't:' && startedAtMs == null) {
      const unix = Number(parts[1]);
      if (isFinite(unix)) startedAtMs = unix * 1000;
    } else if (tag === 'switch' || tag === 'drag') {
      const pos = extractPosition(parts[1] || '');
      const hp = parseHpPct(parts[3]);
      if (pos) hpTimeline.push({ eventIndex: idx, position: pos, hpPct: hp, event: 'switch' });
    } else if (tag === '-damage') {
      const pos = extractPosition(parts[1] || '');
      const hp = parseHpPct(parts[2]);
      if (pos) hpTimeline.push({ eventIndex: idx, position: pos, hpPct: hp, event: 'damage' });
    } else if (tag === '-heal') {
      const pos = extractPosition(parts[1] || '');
      const hp = parseHpPct(parts[2]);
      if (pos) hpTimeline.push({ eventIndex: idx, position: pos, hpPct: hp, event: 'heal' });
    } else if (tag === 'faint') {
      const pos = extractPosition(parts[1] || '');
      if (pos) hpTimeline.push({ eventIndex: idx, position: pos, hpPct: 0, event: 'faint' });
    }
  }

  const teamPreview = (mine.length || opp.length) ? { mine, opp } : null;
  return { hpTimeline, teamPreview, startedAtMs };
}

export function lookupHpBefore(
  timeline: HpTimelineEntry[],
  beforeIndex: number,
  position: string
): number | null {
  for (let i = timeline.length - 1; i >= 0; i--) {
    const e = timeline[i];
    if (e.eventIndex >= beforeIndex) continue;
    if (e.position === position) return e.hpPct;
  }
  return null;
}

function categorizeResidual(source: string): ResidualCategory {
  const s = source.toLowerCase().trim();
  if (/^stealth rock$|^spikes$|^sticky web$|^toxic spikes$|g-max steelsurge/.test(s)) return 'hazard';
  if (/^(psn|tox|brn|confusion|recoil|curse|leech seed|perish|future sight|doom desire|nightmare|sandstorm|hail|saltcure)$/.test(s)) return 'status';
  if (/^(item: )?rocky helmet$|^ability: (rough skin|iron barbs|aftermath)$/.test(s)) return 'contact';
  if (/^item: (leftovers|black sludge|shell bell)$/.test(s)) return 'item';
  if (/^(move: )?(spiky shield|baneful bunker|jungle healing|g-max volt crash)$/.test(s)) return 'shield';
  return 'other';
}
