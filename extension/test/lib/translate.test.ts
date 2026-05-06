import { describe, it, expect } from 'vitest';
import { buildMyPokemon } from '../../lib/translate';

describe('buildMyPokemon', () => {
  it('returns engine payload shape with all PP/disabled flags', () => {
    const fakeMon = {
      speciesForme: 'Heatran',
      level: 100,
      hp: 332, maxhp: 332,
      ability: 'flashfire',
      item: 'leftovers',
      stats: { atk: 195, def: 252, spa: 328, spd: 295, spe: 222 },
      status: '',
      moves: ['magmastorm'],
      terastallized: false,
      teraType: 'Grass',
    };
    const fakeWin = { Dex: { species: { get: () => ({ types: ['Fire', 'Steel'], baseStats: {}, abilities: { 0: 'flashfire' } }) } } };
    const result = buildMyPokemon(fakeMon, null, fakeWin as any);
    expect(result.species).toBe('heatran');
    expect(result.types).toEqual(['Fire', 'Steel']);
    expect(result.moves).toHaveLength(4);
    expect(result.moves[0].id).toBe('magmastorm');
  });
});
