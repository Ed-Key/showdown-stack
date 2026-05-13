/**
 * Resolves a Pokémon species name (Showdown format) to its sprite URL
 * on play.pokemonshowdown.com. Uses /sprites/home/ unconditionally for
 * coverage across Gens 1-9 including Mega/Gmax/Paradox/regional forms.
 *
 * Verified empirically against 67 species on 2026-05-12.
 * See docs/superpowers/specs/2026-05-12-tcg-dashboard-redesign-design.md §4.1
 */

/**
 * Species whose canonical name contains a hyphen but is NOT a multi-part form
 * (the hyphen is part of the name itself). The hyphen is dropped for the URL.
 */
const SINGLE_NAME_HYPHEN = new Set([
  'ho-oh',
  'porygon-z',
  'jangmo-o',
  'hakamo-o',
  'kommo-o',
  'nidoran-f',
  'nidoran-m',
]);

function toId(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Convert species name to its Pokémon HOME sprite URL.
 * @param name Showdown-format species name (e.g. "Iron Valiant", "Samurott-Hisui", "Ho-Oh")
 * @returns Full URL string ready for <img src>, or empty string if name has no usable characters
 */
export function speciesToSpriteURL(name: string): string {
  // Strip non-alphanumerics (keeping hyphens), lowercase in one pass.
  // Matches the codebase's idiomatic normalization (see lib/translate.ts norm()).
  const s = name.toLowerCase().replace(/[^a-z0-9-]/g, '');

  // Empty after normalization → caller passed garbage. Return empty so the
  // browser silently skips rendering rather than fetching a 404 placeholder.
  if (s === '' || s === '-') return '';

  let slug: string;
  if (SINGLE_NAME_HYPHEN.has(s)) {
    // Single-name hyphenated → drop hyphen entirely
    slug = toId(s);
  } else if (s.includes('-')) {
    // Multi-part form → keep first hyphen, fuse rest into one segment
    const idx = s.indexOf('-');
    slug = s.slice(0, idx) + '-' + toId(s.slice(idx + 1));
  } else {
    // Plain species name → simple toID (already lowercased above)
    slug = s;
  }

  return `https://play.pokemonshowdown.com/sprites/home/${slug}.png`;
}
