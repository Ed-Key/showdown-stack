import { describe, it, expect } from 'vitest';
import { computeThreats } from '../../lib/threats';

describe('computeThreats', () => {
  it('surfaces OHKO threats from on-field opp', () => {
    const matrix = {
      cells: [
        { attacker: 'Garchomp', defender: 'Heatran', move: 'earthquake', moveSource: 'revealed' as const,
          dmgPctMin: 100, dmgPctMax: 120, ohko: true, twoHko: false, immune: false },
        { attacker: 'Garchomp', defender: 'Diancie', move: 'earthquake', moveSource: 'revealed' as const,
          dmgPctMin: 60, dmgPctMax: 70, ohko: false, twoHko: true, immune: false },
        { attacker: 'Slowbro', defender: 'Heatran', move: 'scald', moveSource: 'modal' as const, modalPct: 80,
          dmgPctMin: 30, dmgPctMax: 40, ohko: false, twoHko: false, immune: false },
      ],
      attackerSide: 'opp' as const,
      computedAt: 0,
    };
    const myActive = { species: 'Heatran', speed: 222 } as any;
    const oppActive = { species: 'Garchomp', speed: 311 } as any;
    const myTeam = [myActive, { species: 'Diancie', speed: 200 } as any];
    const oppTeam = [oppActive, { species: 'Slowbro', speed: 110 } as any];
    const r = computeThreats({
      matrix, myActive, oppActive, myTeam, oppTeam,
      threshold: { warn: 50, danger: 80 },
    });
    expect(r.onField).toHaveLength(1);
    expect(r.onField[0].oppMove).toBe('earthquake');
    expect(r.onField[0].victims.find(v => v.species === 'Heatran')?.ohko).toBe(true);
    expect(r.incoming.find(t => t.oppSpecies === 'Slowbro')).toBeUndefined();  // <50% threshold
    expect(r.speedNote).toContain('faster than 2/2');
  });
});
