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
  _records: DecisionRecordInput[],
  _stepQueue: string[],
  _meta: ParseMeta,
): BattlePostMortem {
  throw new Error('parseBattlePostMortem: not implemented');
}
