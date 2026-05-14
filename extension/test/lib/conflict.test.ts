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
