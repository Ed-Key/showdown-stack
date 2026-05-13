import { describe, it, expect } from 'vitest';
import { speciesToSpriteURL } from '../../../lib/tcg/species-url';

const BASE = 'https://play.pokemonshowdown.com/sprites/home';

describe('speciesToSpriteURL', () => {
  describe('base species (pure toID)', () => {
    it('Charizard',     () => expect(speciesToSpriteURL('Charizard')).toBe(`${BASE}/charizard.png`));
    it('Iron Valiant',  () => expect(speciesToSpriteURL('Iron Valiant')).toBe(`${BASE}/ironvaliant.png`));
    it('Roaring Moon',  () => expect(speciesToSpriteURL('Roaring Moon')).toBe(`${BASE}/roaringmoon.png`));
    it('Walking Wake',  () => expect(speciesToSpriteURL('Walking Wake')).toBe(`${BASE}/walkingwake.png`));
  });

  describe('regional variants (base-form: keep first hyphen, fuse rest)', () => {
    it('Samurott-Hisui',  () => expect(speciesToSpriteURL('Samurott-Hisui')).toBe(`${BASE}/samurott-hisui.png`));
    it('Slowking-Galar',  () => expect(speciesToSpriteURL('Slowking-Galar')).toBe(`${BASE}/slowking-galar.png`));
    it('Wooper-Paldea',   () => expect(speciesToSpriteURL('Wooper-Paldea')).toBe(`${BASE}/wooper-paldea.png`));
    it('Articuno-Galar',  () => expect(speciesToSpriteURL('Articuno-Galar')).toBe(`${BASE}/articuno-galar.png`));
  });

  describe('multi-part forms (first hyphen survives, rest fuses)', () => {
    it('Tauros-Paldea-Combat',  () => expect(speciesToSpriteURL('Tauros-Paldea-Combat')).toBe(`${BASE}/tauros-paldeacombat.png`));
    it('Mewtwo-Mega-X',         () => expect(speciesToSpriteURL('Mewtwo-Mega-X')).toBe(`${BASE}/mewtwo-megax.png`));
    it('Charizard-Mega-Y',      () => expect(speciesToSpriteURL('Charizard-Mega-Y')).toBe(`${BASE}/charizard-megay.png`));
    it('Urshifu-Rapid-Strike',  () => expect(speciesToSpriteURL('Urshifu-Rapid-Strike')).toBe(`${BASE}/urshifu-rapidstrike.png`));
    it('Toxtricity-Low-Key',    () => expect(speciesToSpriteURL('Toxtricity-Low-Key')).toBe(`${BASE}/toxtricity-lowkey.png`));
    it('Necrozma-Dusk-Mane',    () => expect(speciesToSpriteURL('Necrozma-Dusk-Mane')).toBe(`${BASE}/necrozma-duskmane.png`));
  });

  describe('two-part forms', () => {
    it('Gengar-Mega',     () => expect(speciesToSpriteURL('Gengar-Mega')).toBe(`${BASE}/gengar-mega.png`));
    it('Rotom-Wash',      () => expect(speciesToSpriteURL('Rotom-Wash')).toBe(`${BASE}/rotom-wash.png`));
    it('Calyrex-Ice',     () => expect(speciesToSpriteURL('Calyrex-Ice')).toBe(`${BASE}/calyrex-ice.png`));
    it('Zacian-Crowned',  () => expect(speciesToSpriteURL('Zacian-Crowned')).toBe(`${BASE}/zacian-crowned.png`));
  });

  describe('Gigantamax', () => {
    it('Charizard-Gmax',  () => expect(speciesToSpriteURL('Charizard-Gmax')).toBe(`${BASE}/charizard-gmax.png`));
    it('Urshifu-Gmax',    () => expect(speciesToSpriteURL('Urshifu-Gmax')).toBe(`${BASE}/urshifu-gmax.png`));
  });

  describe('special characters get stripped', () => {
    it('Mr. Mime',     () => expect(speciesToSpriteURL('Mr. Mime')).toBe(`${BASE}/mrmime.png`));
    it('Type: Null',   () => expect(speciesToSpriteURL('Type: Null')).toBe(`${BASE}/typenull.png`));
    it("Farfetch'd",   () => expect(speciesToSpriteURL("Farfetch'd")).toBe(`${BASE}/farfetchd.png`));
    it('Tapu Koko',    () => expect(speciesToSpriteURL('Tapu Koko')).toBe(`${BASE}/tapukoko.png`));
  });

  describe('single-name hyphenated species (denylist — hyphen dropped)', () => {
    it('Ho-Oh',       () => expect(speciesToSpriteURL('Ho-Oh')).toBe(`${BASE}/hooh.png`));
    it('Porygon-Z',   () => expect(speciesToSpriteURL('Porygon-Z')).toBe(`${BASE}/porygonz.png`));
    it('Jangmo-o',    () => expect(speciesToSpriteURL('Jangmo-o')).toBe(`${BASE}/jangmoo.png`));
    it('Kommo-o',     () => expect(speciesToSpriteURL('Kommo-o')).toBe(`${BASE}/kommoo.png`));
  });
});
