/**
 * Energy orb palette config. 9 types use cropped images from the
 * Pokemon TCG xy/g1 (Generations 2016) basic energy cards on tcgdex CDN.
 * 1 type (colorless) uses a custom SVG path designed by Ed.
 *
 * Crop coords (220% / center 68%) verified to hit the orb cleanly for all 9
 * image-sourced types — they share the same xy/g1 template.
 *
 * See docs/superpowers/specs/2026-05-12-tcg-dashboard-redesign-design.md §2.5 & §4.3
 */

/** SVG path string for the Colorless energy orb — 6-point spiked star with curved
 *  concave sides. Designed by Ed via the brainstorming-session crop tool. */
export const COLORLESS_SVG_PATH =
  'M 50 9 Q 51.59 37.2 56.5 38.74 Q 60.29 42.22 85.51 29.5 ' +
  'Q 61.88 44.98 63 50 Q 61.88 55.02 85.51 70.5 Q 60.29 57.78 56.5 61.26 ' +
  'Q 51.59 62.8 50 91 Q 48.41 62.8 43.5 61.26 Q 39.71 57.78 14.49 70.5 ' +
  'Q 38.12 55.02 37 50 Q 38.12 44.98 14.49 29.5 Q 39.71 42.22 43.5 38.74 ' +
  'Q 48.41 37.2 50 9 Z';

export interface OrbConfigImg {
  src: 'img';
  url: string;
  /** CSS background-size — e.g. '220% auto' */
  bg: string;
  /** CSS background-position — e.g. 'center 68%' */
  pos: string;
}

export interface OrbConfigSvg {
  src: 'svg';
  /** SVG path d-attribute string */
  path: string;
}

export type OrbConfig = OrbConfigImg | OrbConfigSvg;

const IMG_CROP = { bg: '220% auto', pos: 'center 68%' } as const;

export const ENERGY_PALETTE: Record<string, OrbConfig> = {
  grass:     { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/75/high.png', ...IMG_CROP },
  fire:      { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/76/high.png', ...IMG_CROP },
  water:     { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/77/high.png', ...IMG_CROP },
  lightning: { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/78/high.png', ...IMG_CROP },
  psychic:   { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/79/high.png', ...IMG_CROP },
  fighting:  { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/80/high.png', ...IMG_CROP },
  darkness:  { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/81/high.png', ...IMG_CROP },
  metal:     { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/82/high.png', ...IMG_CROP },
  fairy:     { src: 'img', url: 'https://assets.tcgdex.net/en/xy/g1/83/high.png', ...IMG_CROP },
  colorless: { src: 'svg', path: COLORLESS_SVG_PATH },
};
