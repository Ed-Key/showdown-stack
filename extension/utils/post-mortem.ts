// Pure parser: joins scHistory DecisionRecords to Showdown battle.stepQueue
// events and produces a compact per-battle post-mortem.
// No DOM, no globals, no side effects — safe to import into Vitest.

export const POSTMORTEM_SCHEMA_VERSION = 1 as const;

export type DecisionRecordInput = {
  battleId: string;
  turn: number;
  rqid: number;
  tStartMs: number;
  tEndMs?: number;
  forceSwitch: boolean;
  state?: unknown;
  final?: {
    bestMove?: string;
    confidence?: number;
    sims?: number;
    depth?: number;
    pv?: string[];
    alternatives?: { move: string; confidence: number; note?: string }[];
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
  };
  enginePredictedOpp: string | null;
  actualOppMove: string | null;
  pvMatchedReality: boolean | null;
  damageIDealt: MoveOutcome | null;
  damageOppDealt: MoveOutcome | null;
  hazardsAdded: { side: 'mine' | 'opp'; name: string }[];
  hazardsRemoved: { side: 'mine' | 'opp'; name: string }[];
  faints: { side: 'mine' | 'opp'; species: string }[];
  failureMessages: string[];
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
  };
  faintedBefore: { species: string; cause: string | null } | null;
  switchInTook: { hpPctLost: number; from: string } | null;
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
  turns: TurnDiff[];
};

export function parseBattlePostMortem(
  records: DecisionRecordInput[],
  stepQueue: string[],
  meta: ParseMeta,
): BattlePostMortem {
  const turnBlocks = partitionByTurn(stepQueue);
  const winner = extractWinner(stepQueue);
  const totalTurns = [...turnBlocks.keys()].filter(k => k > 0).reduce((a, b) => Math.max(a, b), 0);
  const turnEvents = new Map<number, TurnEvents>();
  for (const [turn, block] of turnBlocks) {
    if (turn <= 0) continue;
    turnEvents.set(turn, extractTurnEvents(turn, block, meta.mySideId));
  }
  const turns: TurnDiff[] = [];
  // Track in-turn force-switch consumption: walk faints on my side in order.
  const forceSwitchCursor = new Map<number, number>(); // turn -> next faint index to consume
  for (const r of records) {
    const te = turnEvents.get(r.turn);
    if (!te) continue;
    if (!r.forceSwitch) {
      turns.push(buildRegularTurnDiff(r, te));
    } else {
      const cursor = forceSwitchCursor.get(r.turn) ?? 0;
      const myFaints = te.faints.filter(f => f.side === 'mine');
      const fainted = myFaints[cursor] ?? null;
      forceSwitchCursor.set(r.turn, cursor + 1);
      const cause = fainted ? findCauseOfMyFaint(cursor, turnBlocks.get(r.turn) || [], meta.mySideId) : null;
      const switchInTook = findHazardDamageOnSwitchIn(turnBlocks.get(r.turn) || [], meta.mySideId, cursor);
      turns.push(buildForceSwitchTurnDiff(r, fainted, cause, switchInTook));
    }
  }
  return {
    schemaVersion: POSTMORTEM_SCHEMA_VERSION,
    battleId: meta.battleId,
    format: meta.format,
    myUsername: meta.myUsername,
    mySideId: meta.mySideId,
    opponent: meta.opponent,
    winner,
    totalTurns,
    startedAtMs: null,
    endedAtMs: Date.now(),
    teamPreview: null,
    turns,
  };
}

// ---- internal types + helpers ----

type TurnEvents = {
  turn: number;
  myMove: MoveInstance | null;
  oppMove: MoveInstance | null;
  faints: { side: 'mine' | 'opp'; species: string }[];
  hazardsAdded: { side: 'mine' | 'opp'; name: string }[];
  hazardsRemoved: { side: 'mine' | 'opp'; name: string }[];
  hints: string[];
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

function partitionByTurn(stepQueue: string[]): Map<number, string[]> {
  const out = new Map<number, string[]>();
  let current = 0;
  out.set(0, []);
  for (const line of stepQueue) {
    const m = line.match(/^\|turn\|(\d+)/);
    if (m) {
      current = Number(m[1]);
      if (!out.has(current)) out.set(current, []);
      continue;
    }
    out.get(current)!.push(line);
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

function extractTurnEvents(turn: number, block: string[], mySideId: 'p1' | 'p2'): TurnEvents {
  const te: TurnEvents = {
    turn, myMove: null, oppMove: null,
    faints: [], hazardsAdded: [], hazardsRemoved: [], hints: [],
  };
  let lastMove: MoveInstance | null = null;
  for (const line of block) {
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
    } else if (tag === '-damage') {
      const victim = parts[1] || '';
      const hpAfter = parseHpPct(parts[2]);
      const side = classifySide(victim, mySideId);
      if (lastMove && side && side !== lastMove.attackerSide) {
        if (lastMove.hpPctBefore == null) lastMove.hpPctBefore = 100;
        lastMove.hpPctAfter = hpAfter;
      }
    } else if (tag === 'faint') {
      const victim = parts[1] || '';
      const side = classifySide(victim, mySideId);
      if (side) te.faints.push({ side, species: speciesFromToken(victim) });
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
    }
  }
  return te;
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

function buildRegularTurnDiff(r: DecisionRecordInput, te: TurnEvents): RegularTurnDiff {
  const pv = r.final?.pv ?? [];
  const enginePredictedOpp = parseThemToken(pv[0]);
  const actualOppMove = te.oppMove?.move ?? null;
  return {
    turn: r.turn,
    forceSwitch: false,
    rqid: r.rqid,
    myPick: {
      kind: 'move',
      name: r.final?.bestMove ?? null,
      confidence: r.final?.confidence ?? null,
      sims: r.final?.sims ?? null,
      depth: r.final?.depth ?? null,
      pv,
    },
    enginePredictedOpp,
    actualOppMove,
    pvMatchedReality: compareMoves(enginePredictedOpp, actualOppMove),
    damageIDealt: te.myMove ? moveInstanceToOutcome(te.myMove) : null,
    damageOppDealt: te.oppMove ? moveInstanceToOutcome(te.oppMove) : null,
    hazardsAdded: [...te.hazardsAdded],
    hazardsRemoved: [...te.hazardsRemoved],
    faints: [...te.faints],
    failureMessages: [...te.hints],
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
  // Walk the block tracking the most recent opp |move| at each position.
  // When we hit the cursor-th mine-side |faint|, return that last opp move.
  let lastOppMove: string | null = null;
  let mineFaintsSeen = 0;
  for (const line of block) {
    if (line.startsWith('|move|')) {
      const parts = line.split('|').slice(1);
      const attacker = parts[1] || '';
      const side = classifySide(attacker, mySideId);
      if (side === 'opp') lastOppMove = parts[2] || null;
    } else if (line.startsWith('|faint|')) {
      const parts = line.split('|').slice(1);
      const victim = parts[1] || '';
      if (classifySide(victim, mySideId) === 'mine') {
        if (mineFaintsSeen === cursor) return lastOppMove;
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
  switchInTook: { hpPctLost: number; from: string } | null,
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
    },
    faintedBefore: fainted ? { species: fainted.species, cause } : null,
    switchInTook,
  };
}
