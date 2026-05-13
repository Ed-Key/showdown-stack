import { describe, it, expect } from 'vitest';
import { ENERGY_PALETTE, COLORLESS_SVG_PATH } from '../../../lib/tcg/energy-orbs';

describe('ENERGY_PALETTE', () => {
  const TCG_TYPES = [
    'grass','fire','water','lightning','psychic',
    'fighting','darkness','metal','fairy','colorless',
  ];

  it('contains all 10 TCG types', () => {
    for (const t of TCG_TYPES) {
      expect(ENERGY_PALETTE[t]).toBeDefined();
    }
  });

  it('image-source types have a URL pointing at xy/g1', () => {
    const imgTypes = ['grass','fire','water','lightning','psychic','fighting','darkness','metal','fairy'];
    for (const t of imgTypes) {
      expect(ENERGY_PALETTE[t].src).toBe('img');
      expect(ENERGY_PALETTE[t].url).toMatch(/^https:\/\/assets\.tcgdex\.net\/en\/xy\/g1\/\d+\/high\.png$/);
      expect(ENERGY_PALETTE[t].bg).toBe('220% auto');
      expect(ENERGY_PALETTE[t].pos).toBe('center 68%');
    }
  });

  it('colorless is svg type with a path', () => {
    expect(ENERGY_PALETTE.colorless.src).toBe('svg');
    expect(ENERGY_PALETTE.colorless.path).toBeDefined();
    expect(ENERGY_PALETTE.colorless.path).toContain('M 50 9');
  });
});

describe('COLORLESS_SVG_PATH', () => {
  it('is the 12-curve path defined in the spec', () => {
    expect(COLORLESS_SVG_PATH).toContain('M 50 9');
    expect(COLORLESS_SVG_PATH).toContain('Z');
    // Spec path uses 12 Q commands (6 spikes × 2)
    const qCount = (COLORLESS_SVG_PATH.match(/Q/g) || []).length;
    expect(qCount).toBe(12);
  });
});
