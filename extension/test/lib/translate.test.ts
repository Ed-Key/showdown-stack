import { describe, it, expect } from 'vitest';
import { buildMyPokemon, buildOppPokemon, computeFutureSightState } from '../../lib/translate';

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

  it('uses the active object HP when myPokemon is stale for the active slot', () => {
    const fakeMon = {
      speciesForme: 'Heatran',
      level: 100,
      hp: 386, maxhp: 386,
      ability: 'flashfire',
      item: 'leftovers',
      stats: { atk: 195, def: 252, spa: 328, spd: 295, spe: 222 },
      status: '',
      moves: ['magmastorm'],
    };
    const active = {
      species: { name: 'Heatran' },
      hp: 96,
      maxhp: 386,
      status: 'brn',
    };
    const fakeWin = { Dex: { species: { get: () => ({ types: ['Fire', 'Steel'], baseStats: {}, abilities: { 0: 'flashfire' } }) } } };
    const result = buildMyPokemon(fakeMon, null, fakeWin as any, active);
    expect(result.hp).toBe(96);
    expect(result.maxhp).toBe(386);
    expect(result.status).toBe('Burn');
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

  // ---- Edge cases (translate.ts:168-202 — buildTransformPayload) ----------
  // Source shape variance is the main risk area: Showdown's client stores the
  // transform volatile in at least three documented shapes (array, object
  // with .pokemon, object with .target) and target fields vary by replay
  // type (live battle vs. spectator vs. log replay).

  it('accepts the newer xform.target shape (alternate to xform.pokemon)', () => {
    const target = {
      speciesForme: 'Volcarona',
      stats: { atk: 112, def: 167, spa: 369, spd: 246, spe: 328 },
      moveSlots: [{ id: 'quiverdance' }],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { target } }, // .target instead of .pokemon
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto).toBeDefined();
    expect(result.transformedInto.species).toBe('volcarona');
    expect(result.transformedInto.specialAttack).toBe(369);
  });

  it('degrades to 0 stats and [] moves when target carries minimal data', () => {
    // Imposter-on-switch-in edge case: Showdown may emit the transform
    // volatile before the target's stats/moves are populated on the client.
    // The function must not crash — it returns a payload with safe defaults.
    const target = { speciesForme: 'Volcarona' };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.species).toBe('volcarona');
    expect(result.transformedInto.attack).toBe(0);
    expect(result.transformedInto.speed).toBe(0);
    expect(result.transformedInto.moves).toEqual([]);
    // Types still resolved via dex even without target.types.
    expect(result.transformedInto.types).toEqual(['Bug', 'Fire']);
  });

  it('accepts string-form move entries and filters out "none" + empty', () => {
    const target = {
      speciesForme: 'Volcarona',
      moveSlots: ['quiverdance', { id: 'none' }, '', 'flamethrower'],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.moves).toEqual(['quiverdance', 'flamethrower']);
  });

  it('falls back to target.moves when target.moveSlots is absent', () => {
    const target = {
      speciesForme: 'Volcarona',
      moves: [{ id: 'fierydance' }, 'psychic'],
    };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.moves).toEqual(['fierydance', 'psychic']);
  });

  it('uses target types (not Tera) when terastallized flag is falsy', () => {
    // Guards the Tera-or-base branch at translate.ts:179-181: teraTypes only
    // wins when both terastallized=true AND teraType is set.
    const target = { speciesForme: 'Volcarona', types: ['Bug', 'Fire'] };
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      terastallized: false, teraType: 'Ghost',
      volatiles: { transform: { pokemon: target } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto.types).toEqual(['Bug', 'Fire']);
  });

  it('returns null payload when volatiles.transform exists but has no target', () => {
    // Defensive degrade — Showdown has shipped malformed transform volatiles
    // in the past (e.g. {pokemon: null} on certain disconnect paths).
    const oppMon = {
      speciesForme: 'Ditto', level: 100, hp: 50,
      volatiles: { transform: { pokemon: null } },
    };
    const result: any = buildOppPokemon(oppMon, _xformDexWin);
    expect(result.transformedInto).toBeUndefined();
  });
});

// ---- computeFutureSightState ---------------------------------------------
// Reconstructs pending Future Sight / Doom Desire state from Showdown's
// stepQueue protocol log. Engine consumes via Side.future_sight to mask
// re-casts (choice_effects.rs:913). Tests cover the 5 gap scenarios from
// the post-merge polish roadmap plus key edge cases.

const fsBattle = (overrides: any = {}) => ({
  turn: 1,
  mySide: { sideid: 'p1' },
  myPokemon: [
    { speciesForme: 'Jirachi' },
    { speciesForme: 'Heatran' },
    { speciesForme: 'Garchomp' },
    { speciesForme: 'Tapu Lele' },
    { speciesForme: 'Landorus-Therian' },
    { speciesForme: 'Kartana' },
  ],
  farSide: {
    pokemon: [
      { speciesForme: 'Slowking-Galar' },
      { speciesForme: 'Volcarona' },
      { speciesForme: 'Dragapult' },
      { speciesForme: 'Garchomp' },
      { speciesForme: 'Tapu Koko' },
      { speciesForme: 'Toxapex' },
    ],
  },
  stepQueue: [],
  ...overrides,
});

describe('computeFutureSightState', () => {
  it('returns nulls for both sides when stepQueue is empty', () => {
    const result = computeFutureSightState(fsBattle());
    expect(result).toEqual({ p1: null, p2: null });
  });

  it('returns turns=3 immediately after Future Sight is cast', () => {
    const b = fsBattle({
      turn: 5,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Future Sight',
      ],
    });
    const result = computeFutureSightState(b);
    expect(result.p1).toEqual({ turns: 3, pokemonIndex: 0 });
    expect(result.p2).toBeNull();
  });

  it('reports turns=2 mid-resolution one turn after cast', () => {
    const b = fsBattle({
      turn: 6,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|turn|6',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 2, pokemonIndex: 0 });
  });

  it('reports turns=1 on the turn FS will resolve (2 turns elapsed)', () => {
    const b = fsBattle({
      turn: 7,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|turn|6',
        '|turn|7',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 1, pokemonIndex: 0 });
  });

  it('tracks pending Future Sight on both sides simultaneously', () => {
    const b = fsBattle({
      turn: 3,
      stepQueue: [
        '|turn|3',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|switch|p2a: Slowking-Galar|Slowking-Galar, L100|394/394',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|-start|p2a: Slowking-Galar|move: Future Sight',
      ],
    });
    const result = computeFutureSightState(b);
    expect(result.p1).toEqual({ turns: 3, pokemonIndex: 0 });
    expect(result.p2).toEqual({ turns: 3, pokemonIndex: 0 });
  });

  it('preserves pokemonIndex from cast-time when the source switches out', () => {
    // Jirachi casts FS from slot 0, then switches to Garchomp (slot 2).
    // The pending payload must keep pokemonIndex=0 (Jirachi), not 2.
    const b = fsBattle({
      turn: 5,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|switch|p1a: Garchomp|Garchomp, L100, F|350/350',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 3, pokemonIndex: 0 });
  });

  it('captures the active slot at cast-time when source switched in first', () => {
    // Active starts at slot 0. Switch to Garchomp (slot 2), then cast FS.
    // pokemonIndex should be 2, not 0.
    const b = fsBattle({
      turn: 5,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Garchomp|Garchomp, L100, F|350/350',
        '|-start|p1a: Garchomp|move: Future Sight',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 3, pokemonIndex: 2 });
  });

  it('treats Doom Desire identically to Future Sight', () => {
    const b = fsBattle({
      turn: 5,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Doom Desire',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 3, pokemonIndex: 0 });
  });

  it('clears the source-side pending when -end fires on the target', () => {
    // -end fires on the TARGET side (p2) when FS resolves; the function
    // must clear pending on the SOURCE side (p1 = otherSide of target).
    const b = fsBattle({
      turn: 7,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|switch|p2a: Volcarona|Volcarona, L100|331/331',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|turn|6',
        '|turn|7',
        '|-end|p2a: Volcarona|move: Future Sight',
      ],
    });
    expect(computeFutureSightState(b).p1).toBeNull();
  });

  it('returns null once 3 turns have elapsed even without an -end marker', () => {
    // Cast turn 5, currentTurn 8 → elapsed=3 → turns=0 → null
    const b = fsBattle({
      turn: 8,
      stepQueue: [
        '|turn|5',
        '|switch|p1a: Jirachi|Jirachi, L100|332/332',
        '|-start|p1a: Jirachi|move: Future Sight',
        '|turn|6',
        '|turn|7',
        '|turn|8',
      ],
    });
    expect(computeFutureSightState(b).p1).toBeNull();
  });

  it('uses myPokemon for the side identified by mySide.sideid (p2 routing)', () => {
    // When mySide.sideid='p2', myPokemon belongs to p2 and farSide to p1.
    // FS cast by p2 should index into myPokemon's order.
    const b = fsBattle({
      turn: 3,
      mySide: { sideid: 'p2' },
      myPokemon: [
        { speciesForme: 'Slowking-Galar' },
        { speciesForme: 'Volcarona' },
      ],
      farSide: {
        pokemon: [
          { speciesForme: 'Jirachi' },
          { speciesForme: 'Heatran' },
        ],
      },
      stepQueue: [
        '|turn|3',
        '|switch|p2a: Volcarona|Volcarona, L100|331/331',
        '|-start|p2a: Volcarona|move: Future Sight',
      ],
    });
    const result = computeFutureSightState(b);
    expect(result.p2).toEqual({ turns: 3, pokemonIndex: 1 });
    expect(result.p1).toBeNull();
  });

  it('updates active slot on drag and replace lines (Roar / Illusion)', () => {
    // Drag (Roar/Whirlwind) and replace (Illusion break) should both update
    // the tracked active slot the same way switch does.
    const b = fsBattle({
      turn: 5,
      stepQueue: [
        '|turn|5',
        '|drag|p1a: Heatran|Heatran, L100|352/352',
        '|-start|p1a: Heatran|move: Future Sight',
      ],
    });
    expect(computeFutureSightState(b).p1).toEqual({ turns: 3, pokemonIndex: 1 });
  });
});
