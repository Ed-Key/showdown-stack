import { describe, expect, it } from 'vitest';
import { buildPreviewGrounding, MAX_GROUNDING_CELLS } from '../../lib/preview-grounding';
import type { DamageMatrix, MatrixCell } from '../../lib/damage-matrix';

function cell(overrides: Partial<MatrixCell>): MatrixCell {
  return {
    attacker: 'A', defender: 'B', move: 'Move', moveSource: 'revealed',
    dmgPctMin: 40, dmgPctMax: 55, ohko: false, twoHko: true, immune: false,
    ...overrides,
  };
}

function matrix(cells: MatrixCell[], attackerSide: 'mine' | 'opp'): DamageMatrix {
  return { cells, attackerSide, computedAt: 0 };
}

describe('buildPreviewGrounding', () => {
  it('returns null with no matrices', () => {
    expect(buildPreviewGrounding(null, null)).toBeNull();
  });

  it('keeps only the best move per attacker/defender pair and formats pct', () => {
    const my = matrix([
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', move: 'Ivy Cudgel', dmgPctMin: 68, dmgPctMax: 81 }),
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', move: 'Horn Leech', dmgPctMin: 40, dmgPctMax: 48 }),
    ], 'mine');
    const g = buildPreviewGrounding(my, null)!;
    expect(g.damageCells).toHaveLength(1);
    expect(g.damageCells[0]).toMatchObject({ attacker: 'Ogerpon', move: 'Ivy Cudgel', pct: '68-81', direction: 'mine' });
  });

  it('prioritizes OHKO cells, then >=50% hits, and drops weak/immune cells', () => {
    const my = matrix([
      cell({ attacker: 'A1', defender: 'D1', ohko: true, dmgPctMin: 100, dmgPctMax: 120 }),
      cell({ attacker: 'A2', defender: 'D2', dmgPctMin: 51, dmgPctMax: 62 }),
      cell({ attacker: 'A3', defender: 'D3', dmgPctMin: 10, dmgPctMax: 20 }),   // below floor
      cell({ attacker: 'A4', defender: 'D4', immune: true }),                    // immune
    ], 'mine');
    const g = buildPreviewGrounding(my, null)!;
    expect(g.damageCells.map((c) => c.attacker)).toEqual(['A1', 'A2']);
    expect(g.damageCells[0].ohko).toBe(true);
  });

  it('caps at MAX_GROUNDING_CELLS across both directions', () => {
    const many = (side: 'mine' | 'opp') => matrix(
      Array.from({ length: 20 }, (_, i) =>
        cell({ attacker: `${side}-atk-${i}`, defender: `${side}-def-${i}`, ohko: true, dmgPctMin: 100, dmgPctMax: 110 })),
      side,
    );
    const g = buildPreviewGrounding(many('mine'), many('opp'))!;
    expect(g.damageCells.length).toBe(MAX_GROUNDING_CELLS);
  });

  it('summarizes survives/threatens per my mon', () => {
    const my = matrix([
      cell({ attacker: 'Ogerpon', defender: 'Kingdra', ohko: true, dmgPctMin: 100, dmgPctMax: 110 }),
      cell({ attacker: 'Ogerpon', defender: 'Pelipper', ohko: true, dmgPctMin: 100, dmgPctMax: 105 }),
    ], 'mine');
    const opp = matrix([
      cell({ attacker: 'Kingdra', defender: 'Ogerpon', ohko: true, dmgPctMin: 100, dmgPctMax: 130 }),
      cell({ attacker: 'Pelipper', defender: 'Ogerpon', dmgPctMin: 30, dmgPctMax: 40 }),
    ], 'opp');
    const g = buildPreviewGrounding(my, opp)!;
    const row = g.monSummaries.find((m) => m.species === 'Ogerpon')!;
    expect(row.threatens).toBe(2);      // OHKOs Kingdra + Pelipper
    expect(row.survives).toBe(1);       // 2 opp attackers, 1 OHKOs it
  });
});
