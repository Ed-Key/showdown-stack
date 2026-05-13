import { describe, it, expect } from 'vitest'; 
import { TCG_TYPE_MAP } from '../../../lib/tcg/types';

describe('TCG_TYPE_MAP', () => {
    it('maps base Pokémon types directly', () => {
            expect(TCG_TYPE_MAP.Fire).toBe('fire');
            expect(TCG_TYPE_MAP.Water).toBe('water');
            expect(TCG_TYPE_MAP.Grass).toBe('grass');
            expect(TCG_TYPE_MAP.Psychic).toBe('psychic');
            expect(TCG_TYPE_MAP.Fighting).toBe('fighting');
            expect(TCG_TYPE_MAP.Fairy).toBe('fairy');
    });

    it('consolidates Ice into Water', () => {
        expect(TCG_TYPE_MAP.Ice).toBe('water');
    });

    it('consolidates Bug into Grass', () => {
        expect(TCG_TYPE_MAP.Bug).toBe('grass');
    });

    it('consolidates Ground and Rock into Fighting', () => {
        expect(TCG_TYPE_MAP.Ground).toBe('fighting');
        expect(TCG_TYPE_MAP.Rock).toBe('fighting');
    });

    it('consolidates Normal, Flying, Dragon into Colorless', () => {
        expect(TCG_TYPE_MAP.Normal).toBe('colorless');
        expect(TCG_TYPE_MAP.Flying).toBe('colorless');
        expect(TCG_TYPE_MAP.Dragon).toBe('colorless');
    });

    it('maps Electric to Lightning', () => {
        expect(TCG_TYPE_MAP.Electric).toBe('lightning');
    });

    it('maps Poison and Dark to Darkness', () => {
        expect(TCG_TYPE_MAP.Poison).toBe('darkness');
        expect(TCG_TYPE_MAP.Dark).toBe('darkness');
    });

    it('maps Ghost to Psychic', () => {
        expect(TCG_TYPE_MAP.Ghost).toBe('psychic');
    });

    it('maps Steel to Metal', () => {
        expect(TCG_TYPE_MAP.Steel).toBe('metal');
    });

    it('covers all 18 Pokémon types', () => {
        const allTypes = [
            'Normal','Fire','Water','Electric','Grass','Ice',
            'Fighting','Poison','Ground','Flying','Psychic','Bug',
            'Rock','Ghost','Dragon','Dark','Steel','Fairy',
        ];
        for (const type of allTypes) {
            expect(TCG_TYPE_MAP[type]).toBeDefined();
        }
    });
});
