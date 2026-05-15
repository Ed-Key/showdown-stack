import { describe, it, expect } from 'vitest';
import { detectConflict, computeSafeSwitches } from '../../lib/conflict';

describe('detectConflict', () => {
  const heatran = { species: 'Heatran', speed: 222 } as any;
  const garchomp = { species: 'Garchomp', speed: 311 } as any;
  const myTeam = [heatran, { species: 'Diancie', speed: 200 } as any];

  it('STRONG CONFLICT: opp faster + OHKO + engine recommends stay', () => {
    const threats = {
      onField: [{
        oppSpecies: 'Garchomp', oppMove: 'earthquake', moveSource: 'revealed' as const,
        victims: [{ species: 'Heatran', dmgPct: 105, ohko: true, twoHko: false }],
        speedAdvantage: 'opp_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    const result = detectConflict({
      engineRecommendation: { move: 'magmastorm', isSwitch: false },
      threats, myActive: heatran, oppActive: garchomp, myTeam,
    });
    expect(result?.level).toBe('strong');
    expect(result?.message).toContain('OHKO');
  });

  it('PIVOT WARNING: engine recommends switch to a mon that also dies', () => {
    const threats = {
      onField: [{
        oppSpecies: 'Garchomp', oppMove: 'outrage', moveSource: 'revealed' as const,
        victims: [{ species: 'Diancie', dmgPct: 110, ohko: true, twoHko: false }],
        speedAdvantage: 'opp_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    const result = detectConflict({
      engineRecommendation: { move: 'switch', isSwitch: true, switchTarget: 'Diancie' },
      threats, myActive: heatran, oppActive: garchomp, myTeam,
    });
    expect(result?.level).toBe('pivot');
  });

  it('returns null when engine and threats agree', () => {
    const threats = {
      onField: [{
        oppSpecies: 'Garchomp', oppMove: 'outrage', moveSource: 'revealed' as const,
        victims: [{ species: 'Heatran', dmgPct: 30, ohko: false, twoHko: false }],
        speedAdvantage: 'opp_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    expect(detectConflict({
      engineRecommendation: { move: 'magmastorm', isSwitch: false },
      threats, myActive: heatran, oppActive: garchomp, myTeam,
    })).toBeNull();
  });

  it('STRONG CONFLICT surfaces safe switches ranked by lowest damage', () => {
    // Heatran is active (OHKO'd by EQ). Bench: Latios (40%), Diancie (immune to EQ),
    // Toxapex (OHKO'd by Hydro). Expected order: Diancie (0%) → Latios (40%),
    // Toxapex excluded for OHKO.
    const team = [
      { species: 'Heatran', speed: 222, hp: 1 } as any,
      { species: 'Latios', speed: 320, hp: 1 } as any,
      { species: 'Diancie', speed: 200, hp: 1 } as any,
      { species: 'Toxapex', speed: 100, hp: 1 } as any,
    ];
    const threats = {
      onField: [
        {
          oppSpecies: 'Garchomp', oppMove: 'earthquake', moveSource: 'revealed' as const,
          victims: [
            { species: 'Heatran', dmgPct: 105, ohko: true, twoHko: false },
            { species: 'Latios', dmgPct: 40, ohko: false, twoHko: false },
            { species: 'Toxapex', dmgPct: 50, ohko: false, twoHko: false },
          ],
          speedAdvantage: 'opp_faster' as const,
        },
        {
          oppSpecies: 'Garchomp', oppMove: 'hydropump', moveSource: 'modal' as const,
          victims: [
            { species: 'Toxapex', dmgPct: 120, ohko: true, twoHko: false },
          ],
          speedAdvantage: 'opp_faster' as const,
        },
      ],
      incoming: [], speedNote: '',
    };
    const result = detectConflict({
      engineRecommendation: { move: 'magmastorm', isSwitch: false },
      threats, myActive: team[0], oppActive: garchomp, myTeam: team,
    });
    expect(result?.level).toBe('strong');
    expect(result?.safeSwitches?.map(s => s.species)).toEqual(['Diancie', 'Latios']);
    expect(result?.safeSwitches?.find(s => s.species === 'Toxapex')).toBeUndefined();
  });

  it('computeSafeSwitches excludes the active mon, fainted mons, and OHKO victims', () => {
    const team = [
      { species: 'Heatran', speed: 222, hp: 100 } as any,    // active
      { species: 'Latios', speed: 320, hp: 100 } as any,     // 40% dmg → safe
      { species: 'Diancie', speed: 200, hp: 0 } as any,      // fainted → excluded
      { species: 'Iron Valiant', speed: 360, hp: 100 } as any, // OHKO → excluded
    ];
    const threats = {
      onField: [{
        oppSpecies: 'Garchomp', oppMove: 'earthquake', moveSource: 'revealed' as const,
        victims: [
          { species: 'Latios', dmgPct: 40, ohko: false, twoHko: false },
          { species: 'Diancie', dmgPct: 30, ohko: false, twoHko: false },
          { species: 'Iron Valiant', dmgPct: 110, ohko: true, twoHko: false },
        ],
        speedAdvantage: 'opp_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    const safe = computeSafeSwitches(threats, team[0], team);
    expect(safe.map(s => s.species)).toEqual(['Latios']);
    expect(safe[0].worstDmgPct).toBe(40);
  });

  it('computeSafeSwitches reports zero damage when the threat does not list the mon', () => {
    // Diancie is Fairy/Rock — Earthquake doesn't target it in this victims list,
    // so worstDmgPct should be 0 (treated as "no damage data → safe").
    const team = [
      { species: 'Heatran', speed: 222, hp: 100 } as any,
      { species: 'Diancie', speed: 200, hp: 100 } as any,
    ];
    const threats = {
      onField: [{
        oppSpecies: 'Garchomp', oppMove: 'earthquake', moveSource: 'revealed' as const,
        victims: [{ species: 'Heatran', dmgPct: 105, ohko: true, twoHko: false }],
        speedAdvantage: 'opp_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    const safe = computeSafeSwitches(threats, team[0], team);
    expect(safe).toEqual([{ species: 'Diancie', worstDmgPct: 0 }]);
  });
});

describe('computeSafeSwitches with matrix + speed data', () => {
  const heatran = { species: 'Heatran', speed: 222, hp: 100 } as any;
  const oppGarchomp = { species: 'Garchomp', speed: 311, hp: 100 } as any;
  const oppSlower = { species: 'Slowking', speed: 250, hp: 100 } as any;
  const baseThreats = {
    onField: [{
      oppSpecies: 'Garchomp', oppMove: 'earthquake', moveSource: 'revealed' as const,
      victims: [
        { species: 'Heatran', dmgPct: 105, ohko: true, twoHko: false },
        { species: 'Latios', dmgPct: 40, ohko: false, twoHko: false },
      ],
      speedAdvantage: 'opp_faster' as const,
    }],
    incoming: [], speedNote: '',
  };

  const matrixCell = (over: Record<string, any>): any => ({
    moveSource: 'revealed', dmgPctMin: 0, dmgPctMax: 0,
    ohko: false, twoHko: false, immune: false,
    ...over,
  });

  it('best move back picked correctly (highest dmgPctMax wins)', () => {
    const team = [
      heatran,
      { species: 'Latios', speed: 320, hp: 100 } as any,
    ];
    const matrix = {
      cells: [
        matrixCell({ attacker: 'Latios', defender: 'Garchomp', move: 'dracometeor', dmgPctMax: 75 }),
        matrixCell({ attacker: 'Latios', defender: 'Garchomp', move: 'psychic', dmgPctMax: 50 }),
        matrixCell({ attacker: 'Latios', defender: 'Garchomp', move: 'hiddenpowerice', dmgPctMax: 110, ohko: true }),
      ],
      attackerSide: 'mine' as const, computedAt: 0,
    };
    const safe = computeSafeSwitches(baseThreats, heatran, team, oppGarchomp, matrix);
    expect(safe.length).toBe(1);
    expect(safe[0].bestMoveBack).toEqual({
      move: 'hiddenpowerice', dmgPctMax: 110, ohko: true, twoHko: false,
    });
  });

  it('immune cells skipped when picking best move back', () => {
    // Lopunny's Close Combat OHKOs but its Quick Attack hits a Ghost — engine
    // should ignore the immune cell entirely, even though it nominally
    // "exists" in the matrix.
    const team = [heatran, { species: 'Lopunny', speed: 240, hp: 100 } as any];
    const matrix = {
      cells: [
        matrixCell({ attacker: 'Lopunny', defender: 'Garchomp', move: 'quickattack', dmgPctMax: 0, immune: true }),
        matrixCell({ attacker: 'Lopunny', defender: 'Garchomp', move: 'closecombat', dmgPctMax: 95, twoHko: true }),
      ],
      attackerSide: 'mine' as const, computedAt: 0,
    };
    const safe = computeSafeSwitches(baseThreats, heatran, team, oppGarchomp, matrix);
    expect(safe[0].bestMoveBack?.move).toBe('closecombat');
    expect(safe[0].bestMoveBack?.twoHko).toBe(true);
  });

  it('all-immune candidate has no bestMoveBack', () => {
    // Normal-type mon attacking pure Ghost — every move immune. Should still
    // appear as a safe switch (it survives) but with no bestMoveBack data.
    const team = [heatran, { species: 'Snorlax', speed: 60, hp: 100 } as any];
    const matrix = {
      cells: [
        matrixCell({ attacker: 'Snorlax', defender: 'Garchomp', move: 'bodyslam', dmgPctMax: 0, immune: true }),
        matrixCell({ attacker: 'Snorlax', defender: 'Garchomp', move: 'doubleedge', dmgPctMax: 0, immune: true }),
      ],
      attackerSide: 'mine' as const, computedAt: 0,
    };
    const safe = computeSafeSwitches(baseThreats, heatran, team, oppGarchomp, matrix);
    expect(safe.length).toBe(1);
    expect(safe[0].bestMoveBack).toBeUndefined();
  });

  it('fasterThanOpp accurate at the speed-tie boundary', () => {
    // Three candidates at speeds 300, 250, 250 vs an opp at 250 — strict
    // greater-than only; tie resolves false.
    const team = [
      heatran,
      { species: 'Fast', speed: 300, hp: 100 } as any,
      { species: 'TiedA', speed: 250, hp: 100 } as any,
      { species: 'TiedB', speed: 250, hp: 100 } as any,
    ];
    // Use a "no damage" threat so all three are equally safe.
    const benignThreats = {
      onField: [{
        oppSpecies: 'Slowking', oppMove: 'scald', moveSource: 'revealed' as const,
        victims: [], speedAdvantage: 'me_faster' as const,
      }],
      incoming: [], speedNote: '',
    };
    const safe = computeSafeSwitches(benignThreats, heatran, team, oppSlower);
    const bySpec = Object.fromEntries(safe.map(s => [s.species, s.fasterThanOpp]));
    expect(bySpec).toEqual({ Fast: true, TiedA: false, TiedB: false });
  });

  it('no matrix or oppActive → legacy shape preserved (no new fields)', () => {
    const team = [
      heatran,
      { species: 'Latios', speed: 320, hp: 100 } as any,
    ];
    const safe = computeSafeSwitches(baseThreats, heatran, team);
    expect(safe).toEqual([{ species: 'Latios', worstDmgPct: 40 }]);
    // No bestMoveBack, no fasterThanOpp keys.
    expect('bestMoveBack' in safe[0]).toBe(false);
    expect('fasterThanOpp' in safe[0]).toBe(false);
  });
});
