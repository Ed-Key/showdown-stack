import { describe, it, expect } from 'vitest';
import { detectConflict } from '../../lib/conflict';

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
});
