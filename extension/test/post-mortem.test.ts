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
import phase2Fixture from './fixtures/stepqueue-phase2.json';
import koCreditFixture from './fixtures/stepqueue-ko-credit.json';

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
      final: {
        bestMove: 'Secret Sword',
        confidence: 0.9,
        sims: 100,
        depth: 5,
        pv: ['you=SECRETSWORD them=BODYSLAM'],
        alternatives: [],
        message: 'Hidden-info split.',
        pimcConsensus: {
          hypothesisCount: 4,
          topMove: 'Secret Sword',
          topMoveVotes: 2,
          topMoveShare: 0.5,
          distinctTopMoves: 3,
          tier: 'split',
          uncertain: true,
        },
        pimcBreakdown: [
          { top_move: 'Secret Sword', value: 0.7, visit_share: 0.6 },
        ],
      },
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
  it('turn 1: myPick preserves engine uncertainty metadata', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.myPick.message).toBe('Hidden-info split.');
    expect(t.myPick.pimcConsensus?.tier).toBe('split');
    expect(t.myPick.pimcConsensus?.topMoveShare).toBe(0.5);
    expect(t.myPick.pimcBreakdown).toHaveLength(1);
  });
  it('turn 1: actualMyAction records the observed move from stepQueue', () => {
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.actualMyAction).toEqual({ kind: 'move', name: 'Secret Sword' });
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
    expect(fs.actualMyAction).toEqual({ kind: 'switch', name: 'Corviknight' });
    expect(fs.faintedBefore?.species).toBe('MyMon');
    expect(fs.faintedBefore?.cause).toBe('Ice Beam');
    expect(fs.switchInTook?.from).toBe('Stealth Rock');
    expect(fs.switchInTook?.hpPctLost).toBe(13);
  });

  it('prefers lethal residual damage over the last opponent move for faint cause', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Shuckle|100/100',
      '|switch|p2a: MyMon|Garchomp|24/100 psn',
      '|turn|1',
      '|move|p2a: MyMon|Earthquake|p1a: OppMon',
      '|move|p1a: OppMon|Protect|p1a: OppMon',
      '|-activate|p1a: OppMon|move: Protect',
      '|-damage|p2a: MyMon|0 fnt|[from] psn',
      '|faint|p2a: MyMon',
      '|switch|p2a: MyMon2|Ogerpon-Wellspring|100/100',
      '|win|Opp',
    ];
    const records: DecisionRecordInput[] = [
      rec({ turn: 1, rqid: 1, forceSwitch: false, final: { bestMove: 'Earthquake', pv: ['you=EARTHQUAKE them=PROTECT'] } }),
      rec({ turn: 1, rqid: 2, forceSwitch: true, tStartMs: 1200, final: { bestMove: 'Ogerpon-Wellspring', pv: ['you=OGERPONWELLSPRING them=NOMOVE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const fs = pm.turns[1] as ForceSwitchTurnDiff;
    expect(fs.faintedBefore).toEqual({ species: 'MyMon', cause: 'psn' });
    expect(fs.actualMyAction).toEqual({ kind: 'switch', name: 'Ogerpon-Wellspring' });
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
    expect(fs1.actualMyAction).toEqual({ kind: 'switch', name: 'Blissey' });
    expect(fs2.actualMyAction).toEqual({ kind: 'switch', name: 'Corviknight' });
    // Switch-in took Stealth Rock damage for fs1's replacement (Blissey at cursor 0)
    expect(fs1.switchInTook?.from).toBe('Stealth Rock');
    expect(fs1.switchInTook?.hpPctLost).toBe(13);
    // And for fs2's replacement (Corviknight at cursor 1)
    expect(fs2.switchInTook?.from).toBe('Stealth Rock');
    expect(fs2.switchInTook?.hpPctLost).toBe(13);
  });
});

describe('parseBattlePostMortem — actualMyAction', () => {
  it('records a voluntary switch as the observed action when no move executes', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|100/100',
      '|turn|1',
      '|switch|p2a: Wall|Corviknight|100/100',
      '|move|p1a: OppMon|Body Slam|p2a: Wall',
      '|-damage|p2a: Wall|82/100',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=BODYSLAM'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.actualMyAction).toEqual({ kind: 'switch', name: 'Corviknight' });
    expect(t.damageIDealt).toBeNull();
  });

  it('classifies a species-name regular recommendation as a switch pick', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|poke|p1|Snorlax|',
      '|poke|p2|Keldeo|',
      '|poke|p2|Corviknight|',
      '|start',
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|100/100',
      '|turn|1',
      '|switch|p2a: Wall|Corviknight|100/100',
      '|move|p1a: OppMon|Body Slam|p2a: Wall',
      '|-damage|p2a: Wall|82/100',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=BODYSLAM'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.myPick).toMatchObject({ kind: 'switch', name: 'Corviknight' });
    expect(t.actualMyAction).toEqual({ kind: 'switch', name: 'Corviknight' });
  });

  it('records prevented when the chosen action never executes before a faint', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Body Slam|p2a: MyMon',
      '|-damage|p2a: MyMon|0 fnt',
      '|faint|p2a: MyMon',
      '|win|Opp',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Secret Sword', pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.actualMyAction).toEqual({ kind: 'prevented', name: null, reason: 'fainted before action (Body Slam)' });
    expect(t.myPick.name).toBe('Secret Sword');
  });

  it('records a prevented action when Showdown emits |cant|', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Greninja|100/100',
      '|switch|p2a: MyMon|Gholdengo|85/100',
      '|turn|1',
      '|move|p1a: OppMon|Dark Pulse|p2a: MyMon',
      '|-supereffective|p2a: MyMon',
      '|-damage|p2a: MyMon|40/100',
      '|cant|p2a: MyMon|flinch',
      '|win|Opp',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Recover', pv: ['you=RECOVER them=DARKPULSE'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.actualMyAction).toEqual({ kind: 'prevented', name: null, reason: 'flinch' });
    expect(t.failureMessages).toContain('cant: MyMon: flinch');
    expect(t.damageOppDealt?.move).toBe('Dark Pulse');
  });

  it('does not treat a forced replacement switch as the regular turn action', () => {
    const stepQueue = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Body Slam|p2a: MyMon',
      '|-damage|p2a: MyMon|0 fnt',
      '|faint|p2a: MyMon',
      '|switch|p2a: Wall|Corviknight|100/100',
      '|win|Opp',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Secret Sword', pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
      rec({
        turn: 1,
        rqid: 2,
        forceSwitch: true,
        tStartMs: 900,
        final: { bestMove: 'Corviknight', pv: ['you=CORVIKNIGHT them=NOMOVE'] },
      }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.turns).toHaveLength(2);
    expect(pm.turns[0].forceSwitch).toBe(false);
    expect(pm.turns[1].forceSwitch).toBe(true);
    expect((pm.turns[0] as RegularTurnDiff).actualMyAction).toEqual({
      kind: 'prevented',
      name: null,
      reason: 'fainted before action (Body Slam)',
    });
    expect((pm.turns[1] as ForceSwitchTurnDiff).actualMyAction).toEqual({ kind: 'switch', name: 'Corviknight' });
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
  it('credits real fixture Future Sight KO to Slowking-Galar', () => {
    const slowking = pm.teamPerformance.mine.pokemon['Slowking-Galar'];
    expect(slowking.koCredit.delayedMoveKos).toBe(1);
    expect(slowking.kos).toBeGreaterThanOrEqual(1);
  });
});

describe('parseBattlePostMortem — dedupe duplicate decisions', () => {
  it('collapses duplicate regular decisions on the same turn keeping the latest complete final', () => {
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
    // Multiple regular records can represent the same actual Showdown turn.
    // Keep one row for analytics, preferring the latest complete final.
    const records: DecisionRecordInput[] = [
      rec({ turn: 0, rqid: 5, final: null }),                       // team-preview scaffold
      rec({ turn: 1, rqid: 5, final: { bestMove: 'Secret Sword', confidence: 0.5, pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
      rec({ turn: 1, rqid: 7, final: null }),                       // incomplete duplicate
      rec({ turn: 1, rqid: 7, final: { bestMove: 'Secret Sword', confidence: 0.9, pv: ['you=SECRETSWORD them=BODYSLAM'] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    expect(pm.turns).toHaveLength(1);
    expect((pm.turns[0] as RegularTurnDiff).rqid).toBe(7);
    expect((pm.turns[0] as RegularTurnDiff).myPick.name).toBe('Secret Sword');
    expect((pm.turns[0] as RegularTurnDiff).myPick.confidence).toBe(0.9);
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

describe('parseBattlePostMortem — schema current', () => {
  it('emits current schemaVersion', () => {
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
    expect(pm.schemaVersion).toBe(POSTMORTEM_SCHEMA_VERSION);
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

describe('parseBattlePostMortem — team performance stats', () => {
  const stepQueue = [
    '|gametype|singles',
    '|player|p1|Opp|1|',
    '|player|p2|Me|2|',
    '|clearpoke',
    '|poke|p1|Landorus-Therian, M|',
    '|poke|p1|Corviknight, M|',
    '|poke|p2|Volcarona, F|',
    '|poke|p2|Garchomp, M|',
    '|poke|p2|Iron Valiant|',
    '|teampreview',
    '|start',
    '|switch|p1a: OppLead|Landorus-Therian|100/100',
    '|switch|p2a: Volcarona|Volcarona|100/100',
    '|turn|1',
    '|move|p2a: Volcarona|Fiery Dance|p1a: OppLead',
    '|-damage|p1a: OppLead|60/100',
    '|move|p1a: OppLead|Stone Edge|p2a: Volcarona',
    '|-damage|p2a: Volcarona|70/100',
    '|turn|2',
    '|switch|p2a: Garchomp|Garchomp|100/100',
    '|-damage|p2a: Garchomp|88/100|[from] Stealth Rock',
    '|move|p1a: OppLead|Earthquake|p2a: Garchomp',
    '|-damage|p2a: Garchomp|40/100',
    '|turn|3',
    '|move|p1a: OppLead|Ice Beam|p2a: Garchomp',
    '|-damage|p2a: Garchomp|0 fnt',
    '|faint|p2a: Garchomp',
    '|switch|p2a: Iron Valiant|Iron Valiant|100/100',
    '|turn|4',
    '|cant|p2a: Iron Valiant|par',
    '|move|p1a: OppLead|U-turn|p2a: Iron Valiant',
    '|-damage|p2a: Iron Valiant|75/100',
    '|turn|5',
    '|move|p2a: Iron Valiant|Close Combat|p1a: OppLead',
    '|-damage|p1a: OppLead|0 fnt',
    '|faint|p1a: OppLead',
    '|win|Me',
  ];
  const records: DecisionRecordInput[] = [
    rec({
      turn: 1,
      rqid: 1,
      final: { bestMove: 'Fiery Dance', confidence: 0.9, sims: 100, depth: 5, pv: ['you=FIERYDANCE them=STONEEDGE'] },
    }),
    rec({
      turn: 2,
      rqid: 2,
      final: { bestMove: 'Garchomp', confidence: 0.8, sims: 100, depth: 5, pv: ['you=GARCHOMP them=EARTHQUAKE'] },
    }),
    rec({
      turn: 3,
      rqid: 3,
      final: { bestMove: 'Earthquake', confidence: 0.72, sims: 100, depth: 5, pv: ['you=EARTHQUAKE them=STONEEDGE'] },
    }),
    rec({
      turn: 4,
      rqid: 4,
      final: { bestMove: 'Close Combat', confidence: 0.7, sims: 100, depth: 5, pv: ['you=CLOSECOMBAT them=UTURN'] },
    }),
    rec({
      turn: 5,
      rqid: 5,
      final: { bestMove: 'Close Combat', confidence: 0.93, sims: 100, depth: 5, pv: ['you=CLOSECOMBAT them=UTURN'] },
    }),
  ];

  const pm = parseBattlePostMortem(records, stepQueue, META);
  const stats = pm.teamPerformance.mine.pokemon;

  it('records exact team leads from switch events', () => {
    expect(pm.teamPerformance.mine.lead).toBe('Volcarona');
    expect(pm.teamPerformance.opp.lead).toBe('Landorus-Therian');
    expect(stats.Volcarona.led).toBe(true);
  });

  it('records survival and faint timing per Pokemon', () => {
    expect(stats.Volcarona.survived).toBe(true);
    expect(stats.Garchomp.survived).toBe(false);
    expect(stats.Garchomp.fainted).toBe(true);
    expect(stats.Garchomp.faintTurn).toBe(3);
  });

  it('records switch-in and forced replacement counts', () => {
    expect(stats.Volcarona.switchIns).toBe(1);
    expect(stats.Garchomp.switchIns).toBe(1);
    expect(stats['Iron Valiant'].switchIns).toBe(1);
    expect(stats['Iron Valiant'].forcedSwitchIns).toBe(1);
  });

  it('records visible HP pressure by source category', () => {
    expect(stats.Garchomp.fieldPressure.hazardHpLost).toBe(12);
    expect(stats.Garchomp.fieldPressure.totalHpLost).toBe(12);
    expect(stats.Garchomp.fieldPressure.events).toBe(1);
  });

  it('records visible direct damage, healing-independent targeting, and KOs', () => {
    expect(stats.Volcarona.directDamageDealtPct).toBe(40);
    expect(stats.Volcarona.directDamageTakenPct).toBe(30);
    expect(stats.Garchomp.directDamageTakenPct).toBe(88);
    expect(stats['Iron Valiant'].directDamageTakenPct).toBe(25);
    expect(stats['Iron Valiant'].directDamageDealtPct).toBe(60);
    expect(stats['Iron Valiant'].timesTargeted).toBe(1);
    expect(stats['Iron Valiant'].kos).toBe(1);
    expect(stats['Iron Valiant'].koCredit.directKos).toBe(1);
  });

  it('records engine/player calibration stats by active Pokemon', () => {
    expect(stats.Volcarona.decisionTurns).toBe(2);
    expect(stats.Volcarona.engineWantedSwitchOutCount).toBe(1);
    expect(stats.Garchomp.engineWantedSwitchIntoCount).toBe(1);
    expect(stats.Garchomp.actionPreventedCount).toBe(1);
    expect(stats.Garchomp.highConfidenceDisagreements).toBe(1);
    expect(stats['Iron Valiant'].actionPreventedCount).toBe(1);
    expect(stats['Iron Valiant'].highConfidenceDisagreements).toBe(1);
  });

  it('documents metric caveats for dashboard display', () => {
    expect(pm.teamPerformance.mine.caveats.length).toBeGreaterThanOrEqual(2);
  });
});

describe('parseBattlePostMortem — KO credit attribution', () => {
  const pm = parseBattlePostMortem(
    koCreditFixture.scHistoryForBattle as DecisionRecordInput[],
    koCreditFixture.stepQueue,
    {
      battleId: koCreditFixture.meta.battleId,
      format: koCreditFixture.meta.format,
      myUsername: koCreditFixture.meta.myUsername,
      mySideId: koCreditFixture.meta.mySideId as 'p1' | 'p2',
      opponent: koCreditFixture.meta.opponent,
    },
  );

  it('credits direct, hazard, status, delayed-move, and pressure KOs from fixture logs', () => {
    const stats = pm.teamPerformance.mine.pokemon;

    expect(stats['Landorus-Therian'].koCredit.hazardKos).toBe(1);
    expect(stats.Volcarona.koCredit.pressureKos).toBe(1);
    expect(stats.Toxapex.koCredit.statusKos).toBe(1);
    expect(stats['Slowking-Galar'].koCredit.delayedMoveKos).toBe(1);
    expect(stats['Iron Valiant'].koCredit.directKos).toBe(1);
    expect(stats['Iron Valiant'].kos).toBe(1);
    expect(stats['Slowking-Galar'].kos).toBe(1);
  });

  it('fixture contains source-to-zero lines for hazard and status KO validation', () => {
    const sourceKos = koCreditFixture.stepQueue.filter(line =>
      line.startsWith('|-damage|') && line.includes('0 fnt') && line.includes('[from]')
    );
    expect(sourceKos).toEqual([
      '|-damage|p1a: Charizard|0 fnt|[from] Stealth Rock',
      '|-damage|p1a: Blissey|0 fnt|[from] psn',
    ]);
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

describe('parseBattlePostMortem — residual damage (hazard)', () => {
  it('captures Stealth Rock damage on switch-in', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Landorus|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Stealth Rock|p1a: OppMon',
      '|-sidestart|p1: Opp|move: Stealth Rock',
      '|turn|2',
      '|switch|p1a: OppMon2|Medicham|88/100',
      '|-damage|p1a: OppMon2|88/100|[from] Stealth Rock',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Stealth Rock', pv: [] } }),
      rec({ turn: 2, rqid: 2, final: { bestMove: 'Tackle', pv: [] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t2 = pm.turns[1] as RegularTurnDiff;
    expect(t2.residualEvents.length).toBeGreaterThanOrEqual(1);
    const sr = t2.residualEvents.find(e => e.source === 'Stealth Rock');
    expect(sr).toBeDefined();
    expect(sr?.category).toBe('hazard');
    expect(sr?.side).toBe('opp');
    expect(sr?.targetSpecies).toBe('OppMon2');
  });
});

describe('parseBattlePostMortem — residual damage (contact)', () => {
  it('captures Rocky Helmet recoil', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|Scizor|100/100',
      '|switch|p2a: MyMon|Landorus|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Bullet Punch|p2a: MyMon',
      '|-damage|p2a: MyMon|80/100',
      '|-damage|p1a: OppMon|85/100|[from] item: Rocky Helmet|[of] p2a: MyMon',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Protect', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    const rh = t.residualEvents.find(e => e.source === 'item: Rocky Helmet');
    expect(rh).toBeDefined();
    expect(rh?.category).toBe('contact');
    expect(rh?.side).toBe('opp');
    expect(rh?.targetSpecies).toBe('OppMon');
    expect(rh?.hpPctLost).toBe(15);   // 100 (prior HP) - 85 (new HP)
  });
});

describe('parseBattlePostMortem — residual damage (status)', () => {
  it('captures poison tick', () => {
    const stepQueue = [
      '|switch|p2a: MyMon|X|100/100 psn',
      '|turn|1',
      '|-damage|p2a: MyMon|87/100 psn|[from] psn',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Rest', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    const psn = t.residualEvents.find(e => e.source === 'psn');
    expect(psn?.category).toBe('status');
    expect(psn?.side).toBe('mine');
    expect(psn?.hpPctLost).toBe(13);
  });
});

describe('parseBattlePostMortem — residual heal (Leftovers)', () => {
  it('captures Leftovers as negative hpPctLost', () => {
    const stepQueue = [
      '|switch|p2a: MyMon|X|80/100',
      '|turn|1',
      '|-heal|p2a: MyMon|92/100|[from] item: Leftovers',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Recover', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    const lefts = t.residualEvents.find(e => e.source === 'item: Leftovers');
    expect(lefts?.category).toBe('item');
    expect(lefts?.hpPctLost).toBeLessThan(0);   // heal -> negative
  });
});

describe('parseBattlePostMortem — residual damage (shield)', () => {
  it('captures Spiky Shield recoil', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|Ogerpon|100/100',
      '|switch|p2a: MyMon|Ogerpon|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Horn Leech|p1a: OppMon',
      '|-activate|p1a: OppMon|move: Spiky Shield',
      '|-damage|p2a: MyMon|88/100|[from] Spiky Shield|[of] p1a: OppMon',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Horn Leech', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    const sp = t.residualEvents.find(e => e.source === 'Spiky Shield');
    expect(sp?.category).toBe('shield');
    expect(sp?.side).toBe('mine');
  });
});

describe('parseBattlePostMortem — residual damage (unknown/other)', () => {
  it('unknown source falls back to category "other" with raw source preserved', () => {
    const stepQueue = [
      '|switch|p2a: MyMon|X|100/100',
      '|turn|1',
      '|-damage|p2a: MyMon|50/100|[from] ability: Madeupability',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Rest', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    const x = t.residualEvents.find(e => e.source === 'ability: Madeupability');
    expect(x?.category).toBe('other');
  });
});

describe('parseBattlePostMortem — hpPctBefore from timeline', () => {
  it('uses real pre-move HP, not 100 default', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Tackle|p1a: OppMon',
      '|-damage|p1a: OppMon|80/100',
      '|turn|2',
      // Opp switches in a chipped mon (via voluntary switch to an existing mon at 60%)
      '|switch|p1a: OppMon2|Z|60/100',
      '|move|p2a: MyMon|Tackle|p1a: OppMon2',
      '|-damage|p1a: OppMon2|30/100',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: [] } }),
      rec({ turn: 2, rqid: 2, final: { bestMove: 'Tackle', pv: [] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t2 = pm.turns[1] as RegularTurnDiff;
    expect(t2.damageIDealt?.hpPctBefore).toBe(60);
    expect(t2.damageIDealt?.hpPctAfter).toBe(30);
  });

  it('captures correct hpPctBefore across post-chip turns', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Tackle|p2a: MyMon',
      '|-damage|p2a: MyMon|70/100',
      '|turn|2',
      '|move|p1a: OppMon|Tackle|p2a: MyMon',
      '|-damage|p2a: MyMon|40/100',
      '|win|Me',
    ];
    const records = [
      rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: [] } }),
      rec({ turn: 2, rqid: 2, final: { bestMove: 'Tackle', pv: [] } }),
    ];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t2 = pm.turns[1] as RegularTurnDiff;
    expect(t2.damageOppDealt?.hpPctBefore).toBe(70);
    expect(t2.damageOppDealt?.hpPctAfter).toBe(40);
  });
});

describe('parseBattlePostMortem — failureMessages', () => {
  it('captures Protect activation', () => {
    const stepQueue = [
      '|switch|p1a: OppMon|X|100/100',
      '|switch|p2a: MyMon|Y|100/100',
      '|turn|1',
      '|move|p1a: OppMon|Protect|p1a: OppMon',
      '|-singleturn|p1a: OppMon|move: Protect',
      '|move|p2a: MyMon|Tackle|p1a: OppMon',
      '|-activate|p1a: OppMon|move: Protect',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Tackle', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.failureMessages.length).toBeGreaterThanOrEqual(1);
    expect(t.failureMessages.some(m => m.includes('Protect'))).toBe(true);
  });

  it('captures |hint| messages', () => {
    const stepQueue = [
      '|turn|1',
      '|move|p2a: MyMon|Sleep Talk|p2a: MyMon',
      '|hint|Sleep Talk failed because the user is not asleep.',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Sleep Talk', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.failureMessages.some(m => m.includes('Sleep Talk'))).toBe(true);
  });

  it('captures |-fail| events', () => {
    const stepQueue = [
      '|turn|1',
      '|move|p2a: MyMon|Stealth Rock|p1a: OppMon',
      '|-fail|p2a: MyMon',
      '|win|Me',
    ];
    const records = [rec({ turn: 1, rqid: 1, final: { bestMove: 'Stealth Rock', pv: [] } })];
    const pm = parseBattlePostMortem(records, stepQueue, META);
    const t = pm.turns[0] as RegularTurnDiff;
    expect(t.failureMessages.some(m => m.toLowerCase().includes('fail'))).toBe(true);
  });
});

describe('parseBattlePostMortem — Phase 2 integration', () => {
  const pm = parseBattlePostMortem(
    phase2Fixture.scHistoryForBattle as DecisionRecordInput[],
    phase2Fixture.stepQueue,
    {
      battleId: phase2Fixture.meta.battleId,
      format: phase2Fixture.meta.format,
      myUsername: phase2Fixture.meta.myUsername,
      mySideId: phase2Fixture.meta.mySideId as 'p1' | 'p2',
      opponent: phase2Fixture.meta.opponent,
    },
  );

  it('parses without throwing', () => {
    expect(pm).toBeTruthy();
  });
  it('emits current schemaVersion', () => {
    expect(pm.schemaVersion).toBe(POSTMORTEM_SCHEMA_VERSION);
  });
  it('populates teamPreview with 6 mons per side', () => {
    expect(pm.teamPreview).toBeTruthy();
    expect(pm.teamPreview?.mine.length).toBe(6);
    expect(pm.teamPreview?.opp.length).toBe(6);
  });
  it('populates startedAtMs before endedAtMs', () => {
    expect(pm.startedAtMs).not.toBe(null);
    expect(pm.startedAtMs!).toBeLessThan(pm.endedAtMs);
  });
  it('captures at least one residual event across the battle', () => {
    const total = pm.turns.reduce((acc, t) => acc + (t.residualEvents?.length ?? 0), 0);
    expect(total).toBeGreaterThanOrEqual(1);
  });
  it('at least one post-turn-1 move has hpPctBefore below 100', () => {
    const chipHits = pm.turns.filter(t =>
      !t.forceSwitch &&
      t.turn > 1 &&
      (t.damageIDealt?.hpPctBefore != null && t.damageIDealt.hpPctBefore < 100)
    );
    expect(chipHits.length).toBeGreaterThanOrEqual(1);
  });
  it('winner matches fixture meta', () => {
    expect(pm.winner).toBe(phase2Fixture.meta.winner);
  });
});

describe('parseBattlePostMortem — annotation fields (schema v7)', () => {
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
    '|win|Me',
  ];
  const records: DecisionRecordInput[] = [
    rec({
      turn: 1, rqid: 1,
      final: { bestMove: 'Secret Sword', confidence: 0.9, sims: 100, depth: 5, pv: [], alternatives: [] },
    }),
  ];

  const pm = parseBattlePostMortem(records, stepQueue, META);

  it('schemaVersion is current', () => {
    expect(pm.schemaVersion).toBe(POSTMORTEM_SCHEMA_VERSION);
  });
  it('battleNote defaults to null on a fresh postmortem', () => {
    expect(pm.battleNote).toBeNull();
  });
  it('userNote defaults to null on each turn', () => {
    for (const t of pm.turns) {
      expect(t.userNote).toBeNull();
    }
  });
  it('userOverrideTag defaults to null on each turn diff', () => {
    for (const t of pm.turns) {
      expect(t.userOverrideTag).toBeNull();
    }
  });
  it('conflictWarning defaults to null on each turn diff', () => {
    for (const t of pm.turns) {
      expect(t.conflictWarning).toBeNull();
    }
  });
  it('beliefSnapshot defaults to null on each turn diff', () => {
    for (const t of pm.turns) {
      expect(t.beliefSnapshot).toBeNull();
    }
  });
  it('matrixSummary defaults to null on each turn diff', () => {
    for (const t of pm.turns) {
      expect(t.matrixSummary).toBeNull();
    }
  });
  it('engineUpdates is a summary object on each turn diff (zero when record.updates absent)', () => {
    for (const t of pm.turns) {
      expect(t.engineUpdates).toEqual({ flipCount: 0, sequence: [], eventCount: 0 });
    }
  });
  it('actualMyAction is present on each turn diff', () => {
    for (const t of pm.turns) {
      expect(t.actualMyAction).toEqual({ kind: 'move', name: 'Secret Sword' });
    }
  });
  it('engineUpdates summarizes record.updates when provided (full events live in engine.log)', () => {
    const stepQueueLocal = [
      '|gametype|singles',
      '|player|p1|Opp|1|',
      '|player|p2|Me|2|',
      '|start',
      '|switch|p1a: OppMon|Snorlax|100/100',
      '|switch|p2a: MyMon|Keldeo|100/100',
      '|turn|1',
      '|move|p2a: MyMon|Secret Sword|p1a: OppMon',
      '|-damage|p1a: OppMon|50/100',
      '|win|Me',
    ];
    const updateA = { event: 'update', bestMove: 'X', confidence: 0.4 };
    const updateB = { event: 'update', bestMove: 'Y', confidence: 0.6 };
    const updateF = { event: 'final', bestMove: 'Secret Sword', confidence: 0.9 };
    const recs: DecisionRecordInput[] = [
      rec({
        turn: 1, rqid: 1,
        updates: [updateA, updateB, updateF],
        final: { bestMove: 'Secret Sword', confidence: 0.9, sims: 100, depth: 5, pv: [], alternatives: [] },
      }),
    ];
    const pm2 = parseBattlePostMortem(recs, stepQueueLocal, META);
    expect(pm2.turns.length).toBe(1);
    expect(pm2.turns[0].engineUpdates.eventCount).toBe(3);
    expect(pm2.turns[0].engineUpdates.flipCount).toBe(2);
    expect(pm2.turns[0].engineUpdates.sequence).toEqual(['X', 'Y', 'Secret Sword']);
  });
  it('replayUrl defaults to null on a fresh postmortem (parser default)', () => {
    expect(pm.replayUrl).toBeNull();
  });
});
