import { describe, it, expect } from 'vitest';
import { buildMyPokemon, buildOppPokemon } from '../../lib/translate';

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

const _xformDexWin = {
  Dex: {
    species: {
      get: (name: string) => {
        const n = String(name).toLowerCase();
        if (n === 'ditto')
          return { types: ['Normal'], baseStats: { hp: 48, atk: 48, def: 48, spa: 48, spd: 48, spe: 48 }, abilities: { 0: 'limber' } };
        if (n === 'volcarona')
          return { types: ['Bug', 'Fire'], baseStats: { hp: 85, atk: 60, def: 65, spa: 135, spd: 105, spe: 100 }, abilities: { 0: 'flamebody' } };
        return { types: ['Normal'], baseStats: { hp: 100, atk: 100, def: 100, spa: 100, spd: 100, spe: 100 }, abilities: { 0: 'pressure' } };
      },
    },
  },
} as any;

describe('buildOppPokemon — Transform / Imposter', () => {
  it('emits no transformedInto field when not transformed', () => {
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 100,
      ability: 'imposter', item: 'choicescarf', moveTrack: [],
    };
    const result = buildOppPokemon(oppMon, _xformDexWin);
    expect((result as any).transformedInto).toBeUndefined();
  });

  it('emits transformedInto when Showdown volatile is present (object shape)', () => {
    const target = {
      speciesForme: 'Volcarona',
      ability: 'flamebody',
      stats: { atk: 112, def: 167, spa: 369, spd: 246, spe: 328 },
      moveSlots: [
        { id: 'quiverdance' }, { id: 'flamethrower' },
        { id: 'bugbuzz' }, { id: 'gigadrain' },
      ],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 33,
      ability: 'imposter', item: 'choicescarf',
      moveTrack: [['transform', 0]],
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto).toBeDefined();
    expect(result.transformedInto.species).toBe('volcarona');
    expect(result.transformedInto.types).toEqual(['Bug', 'Fire']);
    expect(result.transformedInto.ability).toBe('flamebody');
    expect(result.transformedInto.attack).toBe(112);
    expect(result.transformedInto.specialAttack).toBe(369);
    expect(result.transformedInto.moves).toEqual([
      'quiverdance', 'flamethrower', 'bugbuzz', 'gigadrain',
    ]);
  });

  it('handles older array-shape transform volatile [name, target]', () => {
    const target = {
      speciesForme: 'Volcarona',
      stats: { atk: 112, def: 167, spa: 369, spd: 246, spe: 328 },
      moveSlots: [{ id: 'quiverdance' }, { id: 'flamethrower' }],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 33,
      moveTrack: [['transform', 0]],
      volatiles: { transform: ['transform', target] },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto).toBeDefined();
    expect(result.transformedInto.species).toBe('volcarona');
  });

  it('overrides types to Tera type when the transforming mon has Tera-d', () => {
    // Ditto transforms into Volcarona, then opp Teras Ditto to Ghost.
    // Ditto uses its OWN preview Tera type, not the target's. Result: pure Ghost.
    const target = {
      speciesForme: 'Volcarona',
      stats: { atk: 112, def: 167, spa: 369, spd: 246, spe: 328 },
      moveSlots: [{ id: 'flamethrower' }],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 33,
      terastallized: true, teraType: 'Ghost',
      moveTrack: [['transform', 0]],
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.types).toEqual(['Ghost']);
  });

  it('caps copied moves at 4 and filters struggle', () => {
    const target = {
      speciesForme: 'Volcarona',
      moveSlots: [
        { id: 'quiverdance' }, { id: 'flamethrower' },
        { id: 'bugbuzz' }, { id: 'gigadrain' },
        { id: 'struggle' },
      ],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.moves).toHaveLength(4);
    expect(result.transformedInto.moves).not.toContain('struggle');
  });
});
