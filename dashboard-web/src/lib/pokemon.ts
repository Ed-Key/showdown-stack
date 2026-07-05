const SPECIAL_SPRITE_IDS: Record<string, string> = {
  'nidoran-f': 'nidoranf',
  'nidoran-m': 'nidoranm',
  'mr-mime': 'mrmime',
  'mime-jr': 'mimejr',
  'type-null': 'typenull',
  'jangmo-o': 'jangmoo',
  'hakamo-o': 'hakamoo',
  'kommo-o': 'kommoo',
  'farfetch-d': 'farfetchd',
  'sirfetch-d': 'sirfetchd',
  'great-tusk': 'greattusk',
  'scream-tail': 'screamtail',
  'brute-bonnet': 'brutebonnet',
  'flutter-mane': 'fluttermane',
  'slither-wing': 'slitherwing',
  'sandy-shocks': 'sandyshocks',
  'roaring-moon': 'roaringmoon',
  'walking-wake': 'walkingwake',
  'gouging-fire': 'gougingfire',
  'raging-bolt': 'ragingbolt',
  'iron-treads': 'irontreads',
  'iron-bundle': 'ironbundle',
  'iron-hands': 'ironhands',
  'iron-jugulis': 'ironjugulis',
  'iron-moth': 'ironmoth',
  'iron-thorns': 'ironthorns',
  'iron-valiant': 'ironvaliant',
  'iron-leaves': 'ironleaves',
  'iron-boulder': 'ironboulder',
  'iron-crown': 'ironcrown',
};

const STATIC_ONLY_SPRITE_IDS = new Set([
  'ironvaliant',
  'ogerpon-wellspring',
  'terapagos',
]);

export function pokemonSpriteId(species: string): string {
  const id = species
    .toLowerCase()
    .replace(/♀/g, 'f')
    .replace(/♂/g, 'm')
    .replace(/['.:%]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  return SPECIAL_SPRITE_IDS[id] || id;
}

export function pokemonSpriteUrl(species: string, fallback = false): string {
  const id = pokemonSpriteId(species);
  const useStatic = fallback || STATIC_ONLY_SPRITE_IDS.has(id);
  const folder = useStatic ? 'gen5' : 'gen5ani';
  const ext = useStatic ? 'png' : 'gif';
  return `https://play.pokemonshowdown.com/sprites/${folder}/${encodeURIComponent(id)}.${ext}`;
}
