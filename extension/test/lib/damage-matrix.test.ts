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

  // --- Fix #1: own-team exact stats ---
  // For attackerSide:'opp', the DEFENDER is the user's mon. Snapshot stats
  // should now be honored exactly via overrides.baseStats instead of being
  // overwritten by the heuristic.

  it("Fix #1: opp Garchomp EQ — defensive Heatran's incoming damage drops vs no-investment Heatran", () => {
    // Defensive Heatran: 252 HP / 252+ SpD Calm / 4 Def
    //   Smogon: hp=386, atk=194, def=249, spa=296, spd=342, spe=190
    // No-investment Heatran:
    //   Smogon: hp=323, atk=216, def=248, spa=296, spd=248, spe=190
    // CB Adamant 252 Atk Garchomp EQ raw damage (verified manually via
    // @smogon/calc):
    //   vs Defensive Heatran: 1020-1204 → 264-312% (hp denom 386)
    //   vs No-invest Heatran: 1024-1212 → 317-375% (hp denom 323)
    // The defensive mon should show LOWER % even though raw damage barely
    // moves — the bigger HP denominator does the work.

    const chompAttacker = {
      species: 'Garchomp', level: 100, types: ['Dragon', 'Ground'],
      hp: 357, maxhp: 357, ability: 'roughskin', item: 'choiceband',
      attack: 359, defense: 200, specialAttack: 176, specialDefense: 206, speed: 311,
      status: 'None', moves: [{ id: 'earthquake', pp: 16 }],
      terastallized: false, teraType: '',
    } as const;

    const matrixDef = buildDamageMatrix({
      attackers: [chompAttacker as any],
      defenders: [{
        species: 'Heatran', level: 100, types: ['Fire', 'Steel'],
        hp: 386, maxhp: 386, ability: 'flashfire', item: 'leftovers',
        attack: 194, defense: 249, specialAttack: 296, specialDefense: 342, speed: 190,
        status: 'None', moves: [], terastallized: false, teraType: '',
      }],
      field: { weather: '', terrain: '' },
      attackerSide: 'opp', // defender is "mine" — exact stats path
    });

    const matrixNoInvest = buildDamageMatrix({
      attackers: [chompAttacker as any],
      defenders: [{
        species: 'Heatran', level: 100, types: ['Fire', 'Steel'],
        hp: 323, maxhp: 323, ability: 'flashfire', item: 'leftovers',
        attack: 216, defense: 248, specialAttack: 296, specialDefense: 248, speed: 190,
        status: 'None', moves: [], terastallized: false, teraType: '',
      }],
      field: { weather: '', terrain: '' },
      attackerSide: 'opp',
    });

    const defCell = matrixDef.cells[0];
    const noCell = matrixNoInvest.cells[0];

    // Defensive % must be strictly lower than no-investment %.
    expect(defCell.dmgPctMax).toBeLessThan(noCell.dmgPctMax);

    // Bracket the calc-true values (264-312% vs 317-375%).
    expect(defCell.dmgPctMin).toBeGreaterThanOrEqual(255);
    expect(defCell.dmgPctMax).toBeLessThanOrEqual(320);
    expect(noCell.dmgPctMin).toBeGreaterThanOrEqual(310);
    expect(noCell.dmgPctMax).toBeLessThanOrEqual(385);

    // Sanity: both still OHKO (hits >100%).
    expect(defCell.ohko).toBe(true);
    expect(noCell.ohko).toBe(true);
  });

  it("Fix #1: my defensive Heatran's outgoing Magma Storm respects reduced SpA", () => {
    // Careful (+SpD / -SpA) 252 HP / 252+ SpD Heatran:
    //   stats: hp=386, atk=216, def=249, spa=266, spd=342, spe=190
    // Modest 252 SpA Heatran (heuristic-friendly offensive snapshot):
    //   stats: hp=323, atk=194, def=248, spa=394, spd=248, spe=191
    // Target: 248 HP / 252+ SpD Calm Toxapex (hp=303, spd=421)
    // Magma Storm raw damage (verified via @smogon/calc):
    //   defensive Heatran:  34-41   → 11-14% of 303
    //   offensive Heatran:  51-60   → 17-20% of 303
    // attackerSide:'mine' means attacker is the "mine" path.

    const tox = {
      species: 'Toxapex', level: 100, types: ['Poison', 'Water'],
      hp: 303, maxhp: 303, ability: 'regenerator', item: 'blacksludge',
      attack: 145, defense: 342, specialAttack: 142, specialDefense: 421, speed: 106,
      status: 'None', moves: [], terastallized: false, teraType: '',
    } as const;

    const matrixDef = buildDamageMatrix({
      attackers: [{
        species: 'Heatran', level: 100, types: ['Fire', 'Steel'],
        hp: 386, maxhp: 386, ability: 'flashfire', item: 'leftovers',
        attack: 216, defense: 249, specialAttack: 266, specialDefense: 342, speed: 190,
        status: 'None', moves: [{ id: 'magmastorm', pp: 8 }],
        terastallized: false, teraType: '',
      }],
      defenders: [tox as any],
      field: { weather: '', terrain: '' },
      attackerSide: 'mine', // attacker is "mine" — exact stats path
    });

    const matrixOff = buildDamageMatrix({
      attackers: [{
        species: 'Heatran', level: 100, types: ['Fire', 'Steel'],
        hp: 323, maxhp: 323, ability: 'flashfire', item: 'leftovers',
        attack: 194, defense: 248, specialAttack: 394, specialDefense: 248, speed: 191,
        status: 'None', moves: [{ id: 'magmastorm', pp: 8 }],
        terastallized: false, teraType: '',
      }],
      defenders: [tox as any],
      field: { weather: '', terrain: '' },
      attackerSide: 'mine',
    });

    const defCell = matrixDef.cells[0];
    const offCell = matrixOff.cells[0];

    // The defensive (Careful, no SpA invest) Heatran must do LESS damage.
    expect(defCell.dmgPctMax).toBeLessThan(offCell.dmgPctMax);

    // Calc-true raw damage from @smogon/calc (verified manually):
    //   defensive Heatran:  34-41 raw  (% depends on defender heuristic Tox)
    //   offensive Heatran:  51-60 raw  (% depends on defender heuristic Tox)
    // The opp defender (Tox) uses the heuristic path (spd: 421 falls just
    // under the 1.25x ratio cutoff → mixed bulk), giving heuristic Tox
    // hp=304/spd=352. So observed % bands: defensive ~13-16%, offensive ~20-24%.
    expect(defCell.dmgPctMin).toBeGreaterThanOrEqual(8);
    expect(defCell.dmgPctMax).toBeLessThanOrEqual(18);
    expect(offCell.dmgPctMin).toBeGreaterThanOrEqual(15);
    expect(offCell.dmgPctMax).toBeLessThanOrEqual(28);
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
