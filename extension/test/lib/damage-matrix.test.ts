import { describe, it, expect } from 'vitest';
import { buildDamageMatrix } from '../../lib/damage-matrix';

describe('buildDamageMatrix', () => {
  it('CB Garchomp EQ OHKOs Heatran', () => {
    const matrix = buildDamageMatrix({
      attackers: [{
        species: 'Garchomp', level: 100, types: ['Dragon', 'Ground'],
        hp: 357, maxhp: 357, ability: 'roughskin', item: 'choiceband',
        attack: 359, defense: 200, specialAttack: 176, specialDefense: 206, speed: 311,
        status: 'None',
        moves: [{ id: 'earthquake', pp: 16 }, { id: 'outrage', pp: 16 }],
        terastallized: false, teraType: '',
      }],
      defenders: [{
        species: 'Heatran', level: 100, types: ['Fire', 'Steel'],
        hp: 332, maxhp: 332, ability: 'flashfire', item: 'leftovers',
        attack: 195, defense: 252, specialAttack: 328, specialDefense: 295, speed: 222,
        status: 'None',
        moves: [],
        terastallized: false, teraType: '',
      }],
      field: { weather: 'Sand', terrain: '' },
      attackerSide: 'opp',
    });
    const eq = matrix.cells.find(c => c.move === 'earthquake');
    expect(eq).toBeDefined();
    expect(eq!.ohko).toBe(true);
    expect(eq!.dmgPctMax).toBeGreaterThanOrEqual(100);
  });

  it('marks modal-source moves with confidence pct', () => {
    const matrix = buildDamageMatrix({
      attackers: [{
        species: 'Tyranitar', level: 100, types: ['Rock', 'Dark'],
        hp: 408, maxhp: 408, ability: 'sandstream', item: 'none',
        attack: 367, defense: 256, specialAttack: 167, specialDefense: 217, speed: 263,
        status: 'None',
        moves: [],
        terastallized: false, teraType: '',
      }],
      defenders: [{ species: 'Heatran', level: 100, types: ['Fire','Steel'],
        hp: 332, maxhp: 332, ability: 'flashfire', item: 'leftovers',
        attack: 195, defense: 252, specialAttack: 328, specialDefense: 295, speed: 222,
        status: 'None', moves: [], terastallized: false, teraType: '' }],
      beliefByDefender: {
        tyranitar: {
          revealed: { moves: ['stoneedge'], item: null, ability: null, tera_type: null },
          modal: {
            moves: [
              { name: 'stoneedge', pct: 95 },
              { name: 'crunch', pct: 78 },
              { name: 'fireblast', pct: 30 },
              { name: 'earthquake', pct: 25 },
            ],
            items: [], abilities: [], spreads: [], tera_types: [],
          },
          speed_range: null, item_inferred_choicescarf: false,
        },
      },
      field: { weather: 'Sand', terrain: '' },
      attackerSide: 'opp',
    });
    const stoneEdge = matrix.cells.find(c => c.move === 'stoneedge');
    const crunch = matrix.cells.find(c => c.move === 'crunch');
    expect(stoneEdge?.moveSource).toBe('revealed');
    expect(crunch?.moveSource).toBe('modal');
    expect(crunch?.modalPct).toBe(78);
  });
});
