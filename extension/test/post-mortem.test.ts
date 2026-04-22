import { describe, it, expect } from 'vitest';
import {
  parseBattlePostMortem,
  POSTMORTEM_SCHEMA_VERSION,
  type DecisionRecordInput,
  type ParseMeta,
  type RegularTurnDiff,
} from '../utils/post-mortem';

const META: ParseMeta = {
  battleId: 'synthetic-1',
  format: '[Gen 9] Synthetic',
  myUsername: 'Me',
  mySideId: 'p2',
  opponent: 'Opp',
};

function rec(partial: Partial<DecisionRecordInput> & { turn: number; rqid: number }): DecisionRecordInput {
  return {
    battleId: 'synthetic-1',
    tStartMs: 1000 + partial.turn,
    tEndMs: 1500 + partial.turn,
    forceSwitch: false,
    ...partial,
  };
}

describe('parseBattlePostMortem — happy path', () => {
  const stepQueue = [
    '|gametype|singles',
    '|player|p1|Opp|1|',
    '|player|p2|Me|2|',
    '|start',
    '|switch|p1a: OppMon|Snorlax|100/100',
    '|switch|p2a: MyMon|Keldeo|100/100',
    '|turn|1',
    '|move|p2a: MyMon|Secret Sword|p1a: OppMon',
    '|-damage|p1a: OppMon|50/100',
    '|move|p1a: OppMon|Body Slam|p2a: MyMon',
    '|-damage|p2a: MyMon|70/100',
    '|turn|2',
    '|move|p2a: MyMon|Secret Sword|p1a: OppMon',
    '|-damage|p1a: OppMon|0 fnt',
    '|faint|p1a: OppMon',
    '|win|Me',
  ];
  const records: DecisionRecordInput[] = [
    rec({
      turn: 1,
      rqid: 1,
      final: { bestMove: 'Secret Sword', confidence: 0.9, sims: 100, depth: 5, pv: ['you=SECRETSWORD them=BODYSLAM'], alternatives: [] },
    }),
    rec({
      turn: 2,
      rqid: 2,
      final: { bestMove: 'Secret Sword', confidence: 0.95, sims: 100, depth: 5, pv: ['you=SECRETSWORD them=BODYSLAM'], alternatives: [] },
    }),
  ];

  const pm = parseBattlePostMortem(records, stepQueue, META);

  it('emits schemaVersion 1', () => {
    expect(pm.schemaVersion).toBe(POSTMORTEM_SCHEMA_VERSION);
  });
  it('carries meta fields through', () => {
    expect(pm.battleId).toBe(META.battleId);
    expect(pm.format).toBe(META.format);
    expect(pm.mySideId).toBe('p2');
    expect(pm.opponent).toBe(META.opponent);
  });
  it('extracts winner', () => {
    expect(pm.winner).toBe('Me');
  });
  it('computes totalTurns from |turn| markers', () => {
    expect(pm.totalTurns).toBe(2);
  });
  it('emits one TurnDiff per record', () => {
    expect(pm.turns).toHaveLength(2);
  });
  it('turn 1: myPick.name matches engine bestMove', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.turn).toBe(1);
    expect(t.forceSwitch).toBe(false);
    expect(t.myPick.name).toBe('Secret Sword');
  });
  it('turn 1: actualOppMove matches stepQueue', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.actualOppMove).toBe('Body Slam');
  });
  it('turn 1: enginePredictedOpp extracted from pv them= token', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.enginePredictedOpp).toBe('BODYSLAM');
  });
  it('turn 1: pvMatchedReality true for "BODYSLAM" vs "Body Slam"', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.pvMatchedReality).toBe(true);
  });
});
