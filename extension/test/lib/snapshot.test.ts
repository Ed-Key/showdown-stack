import { describe, it, expect } from 'vitest';
import { snapshotState } from '../../lib/snapshot';

describe('snapshotState', () => {
  it('captures both sides + turn + weather', () => {
    const fakeBattle = {
      turn: 5,
      weather: 'sandstorm',
      mySide: { active: [{ species: { name: 'Heatran' }, hp: 80, maxhp: 100 }], pokemon: [] },
      farSide: { active: [{ species: { name: 'Garchomp' }, hp: 100, maxhp: 100 }], pokemon: [] },
    };
    const snap = snapshotState(fakeBattle);
    expect(snap.turn).toBe(5);
    expect(snap.weather).toBe('sandstorm');
    expect(snap.my.activeSpecies).toBe('Heatran');
    expect(snap.opp.activeSpecies).toBe('Garchomp');
  });
});
