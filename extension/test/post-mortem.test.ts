import { describe, it, expect } from 'vitest';
import {
  parseBattlePostMortem,
  POSTMORTEM_SCHEMA_VERSION,
  type BattlePostMortem,
  type DecisionRecordInput,
  type ParseMeta,
  type RegularTurnDiff,
  type ForceSwitchTurnDiff,
} from '../utils/post-mortem';
import {
  buildPreBattleState,
  type HpTimelineEntry,
} from '../utils/post-mortem';
import fixture from './fixtures/stepqueue-triplej-loss.json';

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

describe('parseBattlePostMortem — single force-switch', () => {
  it('stamps faintedBefore with species + cause, and switchInTook for hazards', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|-sidestart|p2: Me|move: Stealth Rock',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Talonflame|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Ice Beam|p2a: MyMon',
      '|-damage|p2a: MyMon|0 fnt',
      '|faint|p2a: MyMon',
      '|switch|p2a: MyMon2|Corviknight|100/100',
      '|-damage|p2a: MyMon2|87/100|[from] Stealth Rock',
      '|win|Opp',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 1, forceSwitch: false, final: { bestMove: 'Brave Bird', pv: ['you=BRAVEBIRD them=ICEBEAM'] } }),
      rec({ turn: 1, rqid: 2, forceSwitch: true, tStartMs: 1200, final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=NOMOVE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);

    // Two records on turn 1: regular + force-switch
    expect(pm.turns).toHaveLength(2);
    const fs = pm.turns[1] as ForceSwitchTurnDiff;
    expect(fs.forceSwitch).toBe(true);
    expect(fs.myPick.name).toBe('Corviknight');
    expect(fs.myPick.kind).toBe('switch');
    expect(fs.faintedBefore?.species).toBe('MyMon');
    expect(fs.faintedBefore?.cause).toBe('Ice Beam');
    expect(fs.switchInTook?.from).toBe('Stealth Rock');
    expect(fs.switchInTook?.hpPctLost).toBe(13);
  });
});

describe('parseBattlePostMortem — double force-switch', () => {
  it('pairs two same-turn force-switch records with their respective faints and causes', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|-sidestart|p2: Me|move: Stealth Rock',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: A|Aegislash|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Explosion|p2a: A',
      '|-damage|p2a: A|0 fnt',
      '|-damage|p1a: OppMon|0 fnt',
      '|faint|p1a: OppMon',
      '|faint|p2a: A',
      '|switch|p2a: B|Blissey|100/100',
      '|-damage|p2a: B|87/100|[from] Stealth Rock',
      '|move|p2a: B|Tackle|p1a: OppMon',
      '|-damage|p1a: OppMon|0 fnt',
      '|faint|p1a: OppMon',
      '|move|p1a: OppMon2|Earthquake|p2a: B',
      '|-damage|p2a: B|0 fnt',
      '|faint|p2a: B',
      '|switch|p2a: C|Corviknight|100/100',
      '|-damage|p2a: C|87/100|[from] Stealth Rock',
      '|win|Opp',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 1, forceSwitch: false, final: { bestMove: 'Swords Dance', pv: ['you=SWORDSDANCE them=EXPLOSION'] } }),
      rec({ turn: 1, rqid: 2, forceSwitch: true, tStartMs: 1100, final: { bestMove: 'Blissey', pv: ['you=BLISSEY them=NOMOVE'] } }),
      rec({ turn: 1, rqid: 3, forceSwitch: true, tStartMs: 1200, final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=NOMOVE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);

    expect(pm.turns).toHaveLength(3);
    const fs1 = pm.turns[1] as ForceSwitchTurnDiff;
    const fs2 = pm.turns[2] as ForceSwitchTurnDiff;
    // Species pairing
    expect(fs1.faintedBefore?.species).toBe('A');
    expect(fs2.faintedBefore?.species).toBe('B');
    // Cause pairing (the cursor-aware bit): fs1 died to Explosion, fs2 died to Earthquake
    expect(fs1.faintedBefore?.cause).toBe('Explosion');
    expect(fs2.faintedBefore?.cause).toBe('Earthquake');
    // Pick pairing
    expect(fs1.myPick.name).toBe('Blissey');
    expect(fs2.myPick.name).toBe('Corviknight');
    // Switch-in took Stealth Rock damage for fs1's replacement (Blissey at cursor 0)
    expect(fs1.switchInTook?.from).toBe('Stealth Rock');
    expect(fs1.switchInTook?.hpPctLost).toBe(13);
    // And for fs2's replacement (Corviknight at cursor 1)
    expect(fs2.switchInTook?.from).toBe('Stealth Rock');
    expect(fs2.switchInTook?.hpPctLost).toBe(13);
  });
});

describe('parseBattlePostMortem — hazards', () => {
  it('tracks |-sidestart| and |-sideend| with correct side', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Landorus|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Stealth Rock|p2a: MyMon',
      '|-sidestart|p2: Me|move: Stealth Rock',
      '|move|p2a: MyMon|Defog|p1a: OppMon',
      '|-sideend|p2: Me|move: Stealth Rock',
      '|turn|2',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Defog', pv: ['you=DEFOG them=STEALTHROCK'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.hazardsAdded).toEqual([{ side: 'mine', name: 'Stealth Rock' }]);
    expect(t.hazardsRemoved).toEqual([{ side: 'mine', name: 'Stealth Rock' }]);
  });
});

describe('parseBattlePostMortem — team-preview-only', () => {
  it('returns turns: [] when no |turn|N markers exist', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|clearpoke',
      '|teampreview',
    ];
    const records = [rec({ turn: 0, rqid: 1, final: { bestMove: 'Keldeo', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.turns).toEqual([]);
    expect(pm.totalTurns).toBe(0);
    expect(pm.winner).toBe(null);
  });
});

describe('parseBattlePostMortem — tie', () => {
  it('returns winner: null for |tie|', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Explosion|p1a: OppMon',
      '|-damage|p1a: OppMon|0 fnt',
      '|-damage|p2a: MyMon|0 fnt',
      '|faint|p1a: OppMon',
      '|faint|p2a: MyMon',
      '|tie',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Explosion', pv: ['you=EXPLOSION them=NOMOVE'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.winner).toBe(null);
    expect(pm.turns).toHaveLength(1);
  });
});

describe('parseBattlePostMortem — win mid-turn', () => {
  it('extracts winner even when |win| appears inside a turn block without a closing |turn|', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Secret Sword|p1a: OppMon',
      '|-damage|p1a: OppMon|0 fnt',
      '|faint|p1a: OppMon',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Secret Sword', pv: ['you=SECRETSWORD them=NOMOVE'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.winner).toBe('Me');
    expect(pm.totalTurns).toBe(1);
    const t = pm.turns[0] as RegularTurnDiff;
    // The |win| event must not pollute any field (no 'win' string in failures, hazards, faints).
    expect(t.failureMessages).toEqual([]);
  });
});

describe('parseBattlePostMortem — TripleJ integration', () => {
  const pm: BattlePostMortem = parseBattlePostMortem(
    fixture.scHistoryForBattle as DecisionRecordInput[],
    fixture.stepQueue,
    {
      battleId: fixture.meta.battleId,
      format: fixture.meta.format,
      myUsername: fixture.meta.myUsername,
      mySideId: fixture.meta.mySideId as 'p1' | 'p2',
      opponent: fixture.meta.opponent,
    },
  );

  it('parses without throwing', () => {
    expect(pm).toBeTruthy();
  });
  it('identifies the winner', () => {
    expect(pm.winner).toBe('TripleJ1118');
  });
  it('counts 20 total turns', () => {
    expect(pm.totalTurns).toBe(20);
  });
  it('emits exactly one TurnDiff (extension initialized mid-battle)', () => {
    expect(pm.turns).toHaveLength(1);
    expect(pm.turns[0].turn).toBe(20);
  });
  it('turn 20: engine recommended TAUNT, opp actually used Ice Spinner', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.myPick.name).toBe('TAUNT');
    expect(t.actualOppMove).toBe('Ice Spinner');
  });
  it('turn 20: pvMatchedReality true (ICESPINNER normalizes to Ice Spinner)', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.pvMatchedReality).toBe(true);
  });
  it('turn 20: opp move flagged super-effective', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.damageOppDealt?.superEffective).toBe(true);
  });
  it('turn 20: my side fainted (Landorus)', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.faints.some(f => f.side === 'mine' && f.species === 'Landorus')).toBe(true);
  });
});

describe('parseBattlePostMortem — dedupe duplicate rqids', () => {
  it('collapses duplicate rqids keeping the latest complete final', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Secret Sword|p1a: OppMon',
      '|-damage|p1a: OppMon|50/100',
      '|move|p1a: OppMon|Body Slam|p2a: MyMon',
      '|-damage|p2a: MyMon|70/100',
      '|win|Me',
    ];
    // Three records, two share rqid=7 (one incomplete, one complete). Different rqid=5 is separate.
    const records: DecisionRecordInput[] = [
      rec({ turn: 0, rqid: 5, final: null }),                       // team-preview scaffold
      rec({ turn: 1, rqid: 5, final: { bestMove: 'Secret Sword', confidence: 0.5, pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
      rec({ turn: 1, rqid: 7, final: null }),                       // incomplete duplicate
      rec({ turn: 1, rqid: 7, final: { bestMove: 'Secret Sword', confidence: 0.9, pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    // After dedup: rqid=5 (the complete one, turn=1) + rqid=7 (the complete one) = 2 records.
    // Parser skips turn=0 (no turn block exists); rqid=5 turn=1 and rqid=7 turn=1 both emit TurnDiffs,
    // but both live in turn 1's block, so we get 2 TurnDiffs for turn 1.
    expect(pm.turns).toHaveLength(2);
    // Both picks should be Secret Sword (complete records won over incomplete ones).
    for (const t of pm.turns) {
      expect((t as RegularTurnDiff).myPick.name).toBe('Secret Sword');
    }
    // The higher-confidence (rqid=7) record's confidence should be 0.9; the lower (rqid=5) should be 0.5.
    const confs = pm.turns.map(t => (t as RegularTurnDiff).myPick.confidence).sort();
    expect(confs).toEqual([0.5, 0.9]);
  });

  it('keeps the only record when no complete final exists', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|turn|1',
      '|move|p1a: OppMon|Body Slam|p2a: MyMon',
      '|-damage|p2a: MyMon|70/100',
      '|win|Opp',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 7, final: null }),
      rec({ turn: 1, rqid: 7, final: null }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    // Even with no completes, one TurnDiff emitted for rqid=7 (the latest record kept).
    expect(pm.turns).toHaveLength(1);
    expect((pm.turns[0] as RegularTurnDiff).myPick.name).toBe(null);
  });

  it('preserves distinct rqids on same turn (legit double force-switch)', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: A|Aegislash|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Explosion|p2a: A',
      '|-damage|p2a: A|0 fnt',
      '|-damage|p1a: OppMon|0 fnt',
      '|faint|p1a: OppMon',
      '|faint|p2a: A',
      '|switch|p2a: B|Blissey|100/100',
      '|move|p2a: B|Tackle|p1a: OppMon2',
      '|-damage|p1a: OppMon2|0 fnt',
      '|faint|p1a: OppMon2',
      '|move|p1a: OppMon3|Earthquake|p2a: B',
      '|-damage|p2a: B|0 fnt',
      '|faint|p2a: B',
      '|switch|p2a: C|Corviknight|100/100',
      '|win|Opp',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 5, forceSwitch: false, final: { bestMove: 'Swords Dance', pv: ['you=SWORDSDANCE them=EXPLOSION'] } }),
      rec({ turn: 1, rqid: 7, forceSwitch: true, tStartMs: 1100, final: { bestMove: 'Blissey', pv: ['you=BLISSEY them=NOMOVE'] } }),
      rec({ turn: 1, rqid: 9, forceSwitch: true, tStartMs: 1200, final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=NOMOVE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    // Three distinct rqids → three TurnDiffs, not merged.
    expect(pm.turns).toHaveLength(3);
    const fs1 = pm.turns[1] as ForceSwitchTurnDiff;
    const fs2 = pm.turns[2] as ForceSwitchTurnDiff;
    expect(fs1.faintedBefore?.species).toBe('A');
    expect(fs2.faintedBefore?.species).toBe('B');
  });
});

describe('parseBattlePostMortem — team preview (Pass 0)', () => {
  it('extracts team preview rosters for both sides', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|clearpoke',
      '|poke|p1|Gliscor, F|',
      '|poke|p1|Iron Hands|',
      '|poke|p1|Medicham, F|',
      '|poke|p2|Landorus-Therian, M|',
      '|poke|p2|Ferrothorn, M|',
      '|poke|p2|Gholdengo|',
      '|teampreview',
      '|start',
      '|switch|p1a: Gliscor|Gliscor, F|100/100',
      '|switch|p2a: Landorus|Landorus-Therian, M|381/381',
      '|turn|1',
      '|move|p2a: Landorus|Earthquake|p1a: Gliscor',
      '|-damage|p1a: Gliscor|0 fnt',
      '|faint|p1a: Gliscor',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Earthquake', pv: ['you=EARTHQUAKE them=EARTHQUAKE'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    // META.mySideId is 'p2', so p2 pokes go to 'mine'.
    expect(pm.teamPreview).toEqual({
      mine: ['Landorus-Therian', 'Ferrothorn', 'Gholdengo'],
      opp:  ['Gliscor', 'Iron Hands', 'Medicham'],
    });
  });

  it('returns null teamPreview when no |poke| lines exist', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|turn|1',
      '|move|p2a: X|Tackle|p1a: Y',
      '|-damage|p1a: Y|50/100',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.teamPreview).toBe(null);
  });

  it('strips gender / form / shiny suffixes from poke names', () => {
    const stepQueue = [
      '|clearpoke',
      '|poke|p1|Charizard, M|',
      '|poke|p1|Galvantula, M, shiny|',
      '|poke|p1|Urshifu-*, M|',
      '|poke|p2|Ogerpon-Wellspring, F|',
      '|teampreview',
      '|turn|1',
      '|win|Me',
    ];
    const records: DecisionRecordInput[] = [];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.teamPreview).toEqual({
      mine: ['Ogerpon-Wellspring'],
      opp:  ['Charizard', 'Galvantula', 'Urshifu-*'],
    });
  });
});

describe('parseBattlePostMortem — schema v2', () => {
  it('emits schemaVersion 2', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|turn|1',
      '|move|p2a: MyMon|Tackle|p1a: OppMon',
      '|-damage|p1a: OppMon|50/100',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: ['you=TACKLE them=TACKLE'] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.schemaVersion).toBe(2);
  });

  it('RegularTurnDiff has residualEvents field', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|turn|1',
      '|move|p2a: MyMon|Tackle|p1a: OppMon',
      '|-damage|p1a: OppMon|50/100',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(Array.isArray(t.residualEvents)).toBe(true);
    expect(t.residualEvents).toEqual([]);
  });
});

describe('buildPreBattleState — HP timeline', () => {
  it('records switch-in HP', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|80/100',
      '|turn|1',
    ];
    const pre = buildPreBattleState(stepQueue, 'p2');
    const switches = pre.hpTimeline.filter(e => e.event === 'switch');
    expect(switches).toHaveLength(2);
    expect(switches[0]).toMatchObject({ position: 'p1a', hpPct: 100, event: 'switch' });
    expect(switches[1]).toMatchObject({ position: 'p2a', hpPct: 80, event: 'switch' });
  });

  it('records -damage with post-damage HP', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Tackle|p2a: MyMon',
      '|-damage|p2a: MyMon|75/100',
    ];
    const pre = buildPreBattleState(stepQueue, 'p2');
    const dmg = pre.hpTimeline.filter(e => e.event === 'damage');
    expect(dmg).toHaveLength(1);
    expect(dmg[0]).toMatchObject({ position: 'p2a', hpPct: 75, event: 'damage' });
  });

  it('records -heal as event heal', () => {
    const stepQueue = [
      '|switch|p2a: MyMon|X|50/100',
      '|turn|1',
      '|-heal|p2a: MyMon|75/100|[from] item: Leftovers',
    ];
    const pre = buildPreBattleState(stepQueue, 'p2');
    const heals = pre.hpTimeline.filter(e => e.event === 'heal');
    expect(heals).toHaveLength(1);
    expect(heals[0]).toMatchObject({ position: 'p2a', hpPct: 75, event: 'heal' });
  });

  it('records faint with hpPct 0', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|-damage|p2a: MyMon|0 fnt',
      '|faint|p2a: MyMon',
    ];
    const pre = buildPreBattleState(stepQueue, 'p2');
    const faints = pre.hpTimeline.filter(e => e.event === 'faint');
    expect(faints).toHaveLength(1);
    expect(faints[0]).toMatchObject({ position: 'p2a', hpPct: 0, event: 'faint' });
  });

  it('preserves chronological event indices', () => {
    const stepQueue = [
      '|switch|p1a: X|X|100/100',   // index 0
      '|turn|1',                     // index 1
      '|-damage|p1a: X|80/100',     // index 2
    ];
    const pre = buildPreBattleState(stepQueue, 'p2');
    expect(pre.hpTimeline.map(e => e.eventIndex)).toEqual([0, 2]);
  });
});

import { lookupHpBefore } from '../utils/post-mortem';

describe('lookupHpBefore', () => {
  const tl: HpTimelineEntry[] = [
    { eventIndex: 0, position: 'p1a', hpPct: 100, event: 'switch' },
    { eventIndex: 1, position: 'p2a', hpPct: 100, event: 'switch' },
    { eventIndex: 3, position: 'p2a', hpPct: 75, event: 'damage' },
    { eventIndex: 5, position: 'p1a', hpPct: 50, event: 'damage' },
    { eventIndex: 7, position: 'p2a', hpPct: 90, event: 'heal' },
  ];

  it('returns most recent HP before target index', () => {
    expect(lookupHpBefore(tl, 4, 'p2a')).toBe(75);
  });

  it('returns switch-in HP when only switch precedes target', () => {
    expect(lookupHpBefore(tl, 2, 'p2a')).toBe(100);
  });

  it('returns null when no prior entry for position', () => {
    expect(lookupHpBefore(tl, 0, 'p1a')).toBe(null);
    expect(lookupHpBefore(tl, 1, 'p2a')).toBe(null);
  });

  it('excludes entries at or after beforeIndex', () => {
    expect(lookupHpBefore(tl, 3, 'p2a')).toBe(100);
    expect(lookupHpBefore(tl, 5, 'p1a')).toBe(100);
  });
});
