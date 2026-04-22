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

describe('parseBattlePostMortem — modifier tags', () => {
  it('attaches super-effective / resisted / crit / miss / immune / fail to the right move', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Togekiss|100/100',
      '|switch|p2a: MyMon|Conkeldurr|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Ice Punch|p1a: OppMon',
      '|-supereffective|p1a: OppMon',
      '|-crit|p1a: OppMon',
      '|-damage|p1a: OppMon|10/100',
      '|move|p1a: OppMon|Flamethrower|p2a: MyMon|[miss]',
      '|-miss|p1a: OppMon|p2a: MyMon',
      '|turn|2',
      '|move|p2a: MyMon|Drain Punch|p1a: OppMon',
      '|-resisted|p1a: OppMon',
      '|-damage|p1a: OppMon|5/100',
      '|move|p1a: OppMon|Thunder Wave|p2a: MyMon',
      '|-immune|p2a: MyMon',
      '|turn|3',
      '|move|p2a: MyMon|Stealth Rock|p1a: MyMon',
      '|-fail|p2a: MyMon',
      '|win|Me',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Ice Punch', pv: ['you=ICEPUNCH them=FLAMETHROWER'] } }),
      rec({ turn: 2, rqid: 2, final: { bestMove: 'Drain Punch', pv: ['you=DRAINPUNCH them=THUNDERWAVE'] } }),
      rec({ turn: 3, rqid: 3, final: { bestMove: 'Stealth Rock', pv: ['you=STEALTHROCK them=NOMOVE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);

    const t1 = pm.turns[0] as RegularTurnDiff;
    expect(t1.damageIDealt?.superEffective).toBe(true);
    expect(t1.damageIDealt?.crit).toBe(true);
    expect(t1.damageOppDealt?.missed).toBe(true);

    const t2 = pm.turns[1] as RegularTurnDiff;
    expect(t2.damageIDealt?.resisted).toBe(true);
    expect(t2.damageOppDealt?.immune).toBe(true);

    const t3 = pm.turns[2] as RegularTurnDiff;
    expect(t3.damageIDealt?.failed).toBe(true);
  });
});

describe('parseBattlePostMortem — PV normalization', () => {
  const stepQueue = [
    '|gametype|singles',
    '|player|p1|Opp|1|',
    '|player|p2|Me|2|',
    '|start',
    '|switch|p1a: OppMon|X|100/100',
    '|switch|p2a: MyMon|Y|100/100',
    '|turn|1',
    '|move|p2a: MyMon|Thunder Punch|p1a: OppMon',
    '|-damage|p1a: OppMon|50/100',
    '|move|p1a: OppMon|Thunder Punch|p2a: MyMon',
    '|-damage|p2a: MyMon|50/100',
    '|win|Me',
  ];

  it('matches engine "THUNDERPUNCH" to Showdown "Thunder Punch"', () => {
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Thunder Punch', pv: ['you=THUNDERPUNCH them=THUNDERPUNCH'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect((pm.turns[0] as RegularTurnDiff).pvMatchedReality).toBe(true);
  });

  it('matches when names differ by case only', () => {
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Thunder Punch', pv: ['you=thunderpunch them=thunder Punch'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect((pm.turns[0] as RegularTurnDiff).pvMatchedReality).toBe(true);
  });

  it('mismatches different moves', () => {
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Thunder Punch', pv: ['you=THUNDERPUNCH them=DRAINPUNCH'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect((pm.turns[0] as RegularTurnDiff).pvMatchedReality).toBe(false);
  });

  it('returns null when PV missing', () => {
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Thunder Punch', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.enginePredictedOpp).toBe(null);
    expect(t.pvMatchedReality).toBe(null);
  });
});

describe('parseBattlePostMortem — multi-hit moves', () => {
  it('accumulates total damage across three |-damage| events', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Breloom|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Bullet Seed|p1a: OppMon',
      '|-damage|p1a: OppMon|85/100',
      '|-damage|p1a: OppMon|70/100',
      '|-damage|p1a: OppMon|55/100',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Bullet Seed', pv: ['you=BULLETSEED them=NOMOVE'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.damageIDealt?.hpPctBefore).toBe(100);
    expect(t.damageIDealt?.hpPctAfter).toBe(55);
  });
});
