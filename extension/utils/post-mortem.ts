// Pure parser: joins scHistory DecisionRecords to Showdown battle.stepQueue
// events and produces a compact per-battle post-mortem.
// No DOM, no globals, no side effects — safe to import into Vitest.

export const POSTMORTEM_SCHEMA_VERSION = 2 as const;

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
  residualEvents: ResidualEvent[];
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
  residualEvents: ResidualEvent[];
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
      turns.push(buildRegularTurnDiff(r, te));
    } else {
      const cursor = forceSwitchCursor.get(r.turn) ?? 0;
      const myFaints = te.faints.filter(f => f.side === 'mine');
      const fainted = myFaints[cursor] ?? null;
      forceSwitchCursor.set(r.turn, cursor + 1);
      const blockLines = turnBlocks.get(r.turn)?.lines.map(e => e.line) || [];
      const cause = fainted ? findCauseOfMyFaint(cursor, blockLines, meta.mySideId) : null;
      const switchInTook = findHazardDamageOnSwitchIn(blockLines, meta.mySideId, cursor);
      turns.push(buildForceSwitchTurnDiff(r, fainted, cause, switchInTook, te.residualEvents));
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
    startedAtMs: pre.startedAtMs,
    endedAtMs: Date.now(),
    teamPreview: pre.teamPreview,
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

function extractTurnEvents(
  turn: number,
  block: TurnBlock,
  mySideId: 'p1' | 'p2',
  hpTimeline: HpTimelineEntry[],
): TurnEvents {
  const te: TurnEvents = {
    turn, myMove: null, oppMove: null,
    faints: [], hazardsAdded: [], hazardsRemoved: [], hints: [], residualEvents: [],
  };
  let lastMove: MoveInstance | null = null;
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
        if (lastMove.hpPctBefore == null) lastMove.hpPctBefore = 100;
        lastMove.hpPctAfter = parseHpPct(hpToken);
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
    residualEvents: [...te.residualEvents],
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
    },
    faintedBefore: fainted ? { species: fainted.species, cause } : null,
    switchInTook,
    residualEvents: [...residualEvents],
  };
}

function dedupeRecords(records: DecisionRecordInput[]): DecisionRecordInput[] {
  // Showdown's request lifecycle sometimes produces two scHistory entries per
  // rqid (poll fires before engine response, then fires again after b.turn
  // advances but rqid is unchanged). Collapse by rqid, preferring records
  // with a complete final.bestMove. For each rqid group, keep the LAST record
  // with a complete final, or the last record if none are complete.
  const groups = new Map<number, DecisionRecordInput[]>();
  for (const r of records) {
    const arr = groups.get(r.rqid);
    if (arr) arr.push(r);
    else groups.set(r.rqid, [r]);
  }
  const out: DecisionRecordInput[] = [];
  for (const [, group] of groups) {
    const complete = group.filter(r => r.final?.bestMove);
    out.push(complete.length > 0 ? complete[complete.length - 1] : group[group.length - 1]);
  }
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
